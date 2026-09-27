"""Read-outs: phase, why it last changed, phase lengths, what the core achieved, and drying."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_GRAMS_PER_CUBIC_METER,
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DRYING_STATES, LINK_NONE, LINKED_STATES, PHASES, REASONS
from .entity import RecuperatorEntity
from .logic import BreathingLogic


@dataclass(frozen=True)
class Readout:
    key: str
    value: Callable[[BreathingLogic], object]
    device_class: SensorDeviceClass | None = None
    unit: str | None = None
    options: list[str] | None = None
    icon: str | None = None
    decimals: int | None = None
    diagnostic: bool = False  # detail for tuning: shown under Diagnostic on the device page


READOUTS = (
    Readout("phase", lambda l: l.phase, SensorDeviceClass.ENUM, options=PHASES, icon="mdi:fan"),
    Readout("last_reason", lambda l: l.last_reason, SensorDeviceClass.ENUM, options=REASONS, icon="mdi:information-outline", diagnostic=True),
    Readout("last_exhaust", lambda l: l.last_exhaust_seconds, SensorDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:arrow-up-bold-circle-outline", decimals=0, diagnostic=True),
    Readout("last_intake", lambda l: l.last_intake_seconds, SensorDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:arrow-down-bold-circle-outline", decimals=0, diagnostic=True),
    Readout("outdoor_temperature", lambda l: l.outdoor_estimate, SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, decimals=1),
    Readout("basement_temperature", lambda l: l.basement_estimate, SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, decimals=1),
    Readout("passive_inflow_delay", lambda l: l.passive_flow_after_seconds, SensorDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:timer-outline", decimals=0, diagnostic=True),
    # Heat recovery: the share of the room-outdoor gap given back to incoming air, over the last few intakes.
    Readout("recovery", lambda l: _rounded(l.heat_recovery), None, PERCENTAGE, icon="mdi:heat-wave", decimals=0),
    # Core used: how far the far end of the core moved in the last phase (what Recovery target is compared with).
    Readout("core_used", lambda l: _rounded(l.last_recovery_percent), None, PERCENTAGE, icon="mdi:battery-arrow-down-outline", decimals=0, diagnostic=True),
    Readout("drying", lambda l: l.drying.status, SensorDeviceClass.ENUM, options=DRYING_STATES, icon="mdi:water-off-outline"),
)
MEASURED = {"recovery", "core_used"}  # percentages kept in long-term statistics


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


RESTORED = {"basement_temperature": "basement_estimate", "outdoor_temperature": "outdoor_estimate"}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    sensors: list[SensorEntity] = [
        (LearnedTemperatureSensor if r.key in RESTORED else ReadoutSensor)(entry.runtime_data, entry, r)
        for r in READOUTS
    ]
    sensors.extend(_absolute_humidity_sensors(hass, entry))
    if entry.runtime_data.link_type != LINK_NONE:
        sensors.append(LinkedUnitSensor(entry.runtime_data, entry, "linked_unit"))
    else:
        _remove_stale(hass, f"{entry.entry_id}_linked_unit")  # link removed since last time
    async_add_entities(sensors)


def _absolute_humidity_sensors(hass: HomeAssistant, entry) -> list[SensorEntity]:
    """Room / outdoor absolute humidity, for the humidity sensors that are set up (stale ones removed)."""
    c = entry.runtime_data
    out: list[SensorEntity] = []
    for key, sensor_id, value in (
        ("room_absolute_humidity", c.room_humidity_sensor, c.room_absolute_humidity),
        ("outdoor_absolute_humidity", c.outdoor_humidity_sensor, c.outdoor_absolute_humidity),
    ):
        if sensor_id:
            out.append(AbsoluteHumiditySensor(c, entry, key, value))
        else:
            _remove_stale(hass, f"{entry.entry_id}_{key}")
    return out


def _remove_stale(hass: HomeAssistant, unique_id: str) -> None:
    """Drop a sensor this recuperator no longer provides, rather than leave it unavailable."""
    registry = er.async_get(hass)
    if entity_id := registry.async_get_entity_id("sensor", DOMAIN, unique_id):
        registry.async_remove(entity_id)


class ReadoutSensor(RecuperatorEntity, SensorEntity):
    def __init__(self, controller, entry, readout: Readout) -> None:
        super().__init__(controller, entry, readout.key)
        self._readout = readout
        self._attr_device_class = readout.device_class
        self._attr_native_unit_of_measurement = readout.unit
        self._attr_options = readout.options
        self._attr_suggested_display_precision = readout.decimals
        if readout.icon:
            self._attr_icon = readout.icon
        if readout.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if readout.device_class not in (SensorDeviceClass.ENUM, None) or readout.key in MEASURED:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self._readout.value(self._controller.logic)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self._readout.key == "phase":
            return self._controller.phase_attributes()
        if self._readout.key == "drying":
            return self._controller.drying_attributes()
        return None


class LearnedTemperatureSensor(ReadoutSensor, RestoreSensor):
    """Basement / outdoor air temperature, remembered across restarts.

    The cycle uses them to judge whether indoor and outdoor air are similar and
    how big the gap is, so restoring them lets the first phases after a restart
    be temperature-driven straight away.
    """

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        attr = RESTORED[self._readout.key]
        if getattr(self._controller.logic, attr) is not None:
            return
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                setattr(self._controller.logic, attr, float(last.native_value))
            except (TypeError, ValueError):
                pass


class LinkedUnitSensor(RecuperatorEntity, SensorEntity):
    """What the linked unit is doing: intake, exhaust, idle, or not linked."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = LINKED_STATES
    _attr_icon = "mdi:link-variant"

    @property
    def native_value(self) -> str:
        return self._controller.linked_state()

    @property
    def extra_state_attributes(self) -> dict:
        c = self._controller
        return {
            "linked_unit": c.link_type,
            "exhaust_fan": c.link_exhaust_switch,
            "intake_fan": c.link_intake_switch,
        }


class AbsoluteHumiditySensor(RecuperatorEntity, SensorEntity):
    """Grams of water per cubic metre of room or outdoor air (only with a humidity sensor set up)."""

    _attr_native_unit_of_measurement = CONCENTRATION_GRAMS_PER_CUBIC_METER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:water"

    def __init__(self, controller, entry, key: str, value: Callable[[], float | None]) -> None:
        super().__init__(controller, entry, key)
        self._value = value

    @property
    def native_value(self) -> float | None:
        value = self._value()
        return None if value is None else round(value, 2)
