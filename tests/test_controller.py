"""The cycle driving real (input_boolean) fans, a linked unit, and probe units."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant, State
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.recuperator.controller import probe_celsius

from .conftest import EXHAUST, INSIDE, INTAKE, LINK_EXHAUST, LINK_INTAKE, OUTSIDE, make_entry, set_probe, setup_entry

TIMED = {"timed_exhaust_seconds": 10, "timed_intake_seconds": 20, "pause_seconds": 1}


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int = 1) -> None:
    for _ in range(seconds):
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def on(hass: HomeAssistant) -> set[str]:
    """The fans that are on."""
    return {e for e in (EXHAUST, INTAKE, LINK_EXHAUST, LINK_INTAKE) if hass.states.get(e).state == "on"}


async def start_timed(hass: HomeAssistant, freezer, data=None) -> None:
    await setup_entry(hass, make_entry(data, options=TIMED))
    await hass.services.async_call("select", "select_option", {"entity_id": "select.breather_mode", "option": "timed"}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.breather_breathing"}, blocking=True)
    await tick(hass, freezer, 2)


async def test_timed_cycle_switches_the_fans(hass: HomeAssistant, fans, probes, freezer) -> None:
    await start_timed(hass, freezer)
    assert on(hass) == {EXHAUST}
    assert hass.states.get("sensor.breather_phase").state == "exhaust"

    await tick(hass, freezer, 12)  # 10 s exhaust, 1 s pause, then intake
    assert on(hass) == {INTAKE}
    await tick(hass, freezer, 10)
    assert on(hass) == {INTAKE}  # intake lasts 20 s
    await tick(hass, freezer, 12)
    assert on(hass) == {EXHAUST}
    assert float(hass.states.get("sensor.breather_last_intake").state) == pytest.approx(20, abs=1)
    assert float(hass.states.get("sensor.breather_last_exhaust").state) == pytest.approx(10, abs=1)


async def test_breathing_off_switches_everything_off(hass: HomeAssistant, fans, probes, freezer) -> None:
    await start_timed(hass, freezer, {"link_type": "recuperator", "link_exhaust_switch": LINK_EXHAUST, "link_intake_switch": LINK_INTAKE})
    assert on(hass) == {EXHAUST, LINK_INTAKE}
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.breather_breathing"}, blocking=True)
    await tick(hass, freezer, 2)
    assert on(hass) == set()


async def test_linked_recuperator_breathes_the_other_way(hass: HomeAssistant, fans, probes, freezer) -> None:
    link = {"link_type": "recuperator", "link_exhaust_switch": LINK_EXHAUST, "link_intake_switch": LINK_INTAKE}
    await start_timed(hass, freezer, link)
    assert on(hass) == {EXHAUST, LINK_INTAKE}
    assert hass.states.get("sensor.breather_linked_unit").state == "intake"
    await tick(hass, freezer, 12)
    assert on(hass) == {INTAKE, LINK_EXHAUST}
    assert hass.states.get("sensor.breather_linked_unit").state == "exhaust"


async def test_linked_intake_fan_runs_during_exhaust_only(hass: HomeAssistant, fans, probes, freezer) -> None:
    await start_timed(hass, freezer, {"link_type": "intake_fan", "link_intake_switch": LINK_INTAKE})
    assert on(hass) == {EXHAUST, LINK_INTAKE}
    await tick(hass, freezer, 12)
    assert on(hass) == {INTAKE}
    assert hass.states.get("sensor.breather_linked_unit").state == "idle"


async def test_interlock_waits_for_the_other_fan(hass: HomeAssistant, fans, probes, freezer) -> None:
    """A fan that ignores turn_off (a stuck relay) keeps the other fan from starting."""
    stuck = "switch.stuck_exhaust"
    hass.states.async_set(stuck, "on")  # nothing behind it: commands to it do nothing
    await start_timed(hass, freezer, {"exhaust_switch": stuck})
    await tick(hass, freezer, 15)  # the 10 s exhaust is over; intake is due
    assert hass.states.get("sensor.breather_phase").state == "intake"
    assert hass.states.get(INTAKE).state == "off"

    hass.states.async_set(stuck, "off")  # the relay finally lets go
    await tick(hass, freezer, 1)
    assert hass.states.get(INTAKE).state == "on"


async def test_fahrenheit_probes_are_converted(hass: HomeAssistant, fans) -> None:
    set_probe(hass, INSIDE, 68, "°F")
    set_probe(hass, OUTSIDE, 41, "°F")
    entry = await setup_entry(hass, make_entry())
    inside, outside = entry.runtime_data.probes()
    assert inside == pytest.approx(20.0)
    assert outside == pytest.approx(5.0)


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        ("20", "°C", 20.0),
        ("68", "°F", 20.0),
        ("293.15", "K", 20.0),
        ("20", None, 20.0),
        ("20", "whatever", 20.0),
        ("unavailable", "°C", None),
        ("abc", "°C", None),
    ],
)
def test_probe_celsius(value: str, unit: str | None, expected: float | None) -> None:
    attributes = {"unit_of_measurement": unit} if unit else {}
    result = probe_celsius(State("sensor.x", value, attributes))
    assert result == (None if expected is None else pytest.approx(expected))


async def test_temperature_settings_follow_the_unit_system(hass: HomeAssistant, fans, probes) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    await setup_entry(hass, make_entry(options={"cold_threshold": -5}))
    state = hass.states.get("number.breather_cold_threshold")
    assert state.attributes["unit_of_measurement"] == "°F"
    assert float(state.state) == pytest.approx(23.0)
    # a temperature difference stays in °C
    assert hass.states.get("number.breather_maximum_supply_drop").attributes["unit_of_measurement"] == "°C"
