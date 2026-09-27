"""Optional room and outdoor sensors, weather entities, and the drying entities."""

from __future__ import annotations

from datetime import timedelta

import pytest

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from custom_components.recuperator.controller import ambient_celsius, ambient_humidity

from .conftest import make_entry, setup_entry
from .test_config_flow import _open_options

ROOM_T = "sensor.room_t"
ROOM_RH = "sensor.room_rh"
WEATHER = "weather.home"


def _sensor(hass: HomeAssistant, entity_id: str, value, unit: str, device_class: str) -> None:
    hass.states.async_set(entity_id, str(value), {"unit_of_measurement": unit, "device_class": device_class})


def test_weather_entity_and_sensor_readings() -> None:
    weather = State(WEATHER, "rainy", {"temperature": 41.0, "temperature_unit": "°F", "humidity": 90})
    assert ambient_celsius(weather) == 5.0
    assert ambient_humidity(weather) == 90
    assert ambient_celsius(State(ROOM_T, "20.5", {"unit_of_measurement": "°C"})) == 20.5
    assert ambient_humidity(State(ROOM_RH, "55")) == 55
    assert ambient_humidity(State(ROOM_RH, "150")) is None
    assert ambient_humidity(State(ROOM_RH, "unavailable")) is None
    assert ambient_celsius(State(WEATHER, "sunny", {})) is None


async def test_sensors_page_adds_readings_and_entities(hass: HomeAssistant, fans, probes) -> None:
    _sensor(hass, ROOM_T, 20.0, "°C", "temperature")
    _sensor(hass, ROOM_RH, 70, "%", "humidity")
    hass.states.async_set(WEATHER, "cloudy", {"temperature": 5.0, "temperature_unit": "°C", "humidity": 80})
    entry = await setup_entry(hass, make_entry())
    assert hass.states.get("sensor.breather_room_absolute_humidity") is None

    result = await _open_options(hass, entry, "sensors")
    assert result["step_id"] == "sensors"
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "room_temperature_sensor": ROOM_T,
            "room_humidity_sensor": ROOM_RH,
            "outdoor_temperature_sensor": WEATHER,
            "outdoor_humidity_sensor": WEATHER,
        },
    )
    await hass.async_block_till_done()
    assert entry.data["outdoor_temperature_sensor"] == WEATHER

    # Drying on; one tick reads the sensors.
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.breather_drying"}, blocking=True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()

    logic = entry.runtime_data.logic
    assert logic.ambient.outdoor_temperature == 5.0
    assert logic.room_air() == 20.0
    assert hass.states.get("sensor.breather_drying").state == "drying"
    assert hass.states.get("sensor.breather_drying").attributes["intake_share"] == 50
    assert float(hass.states.get("sensor.breather_room_absolute_humidity").state) == pytest.approx(12.1, abs=0.05)
    assert float(hass.states.get("sensor.breather_outdoor_absolute_humidity").state) == pytest.approx(5.4, abs=0.05)

    # Clearing the fields removes the sensors and their entities.
    result = await _open_options(hass, entry, "sensors")
    await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert "room_humidity_sensor" not in entry.data
    assert hass.states.get("sensor.breather_room_absolute_humidity") is None


async def test_heat_recovery_and_core_used_entities(hass: HomeAssistant, fans, probes) -> None:
    await setup_entry(hass, make_entry())
    assert hass.states.get("sensor.breather_heat_recovery").state == "unknown"
    assert hass.states.get("sensor.breather_core_used") is not None
    assert hass.states.get("sensor.breather_drying").state == "off"
