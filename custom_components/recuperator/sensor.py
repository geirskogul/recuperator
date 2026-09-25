"""Read-outs: phase, why it last changed, phase lengths and what the core achieved."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import PHASES, REASONS
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


READOUTS = (
    Readout("phase", lambda l: l.phase, SensorDeviceClass.ENUM, options=PHASES, icon="mdi:fan"),
    Readout("last_reason", lambda l: l.last_reason, SensorDeviceClass.ENUM, options=REASONS, icon="mdi:information-outline"),
    Readout("last_exhaust", lambda l: l.last_exhaust_seconds, SensorDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:arrow-up-bold-circle-outline", decimals=0),
    Readout("last_intake", lambda l: l.last_intake_seconds, SensorDeviceClass.DURATION, UnitOfTime.SECONDS, icon="mdi:arrow-down-bold-circle-outline", decimals=0),
    Readout("outdoor_temperature", lambda l: l.outdoor_estimate, SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, decimals=1),
    Readout("basement_temperature", lambda l: l.basement_estimate, SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, decimals=1),
    Readout("recovery", lambda l: None if l.last_recovery_percent is None else round(l.last_recovery_percent, 1), None, PERCENTAGE, icon="mdi:heat-wave", decimals=0),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities(ReadoutSensor(entry.runtime_data, entry, r) for r in READOUTS)


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
        if readout.device_class not in (SensorDeviceClass.ENUM, None) or readout.key == "recovery":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self._readout.value(self._controller.logic)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self._readout.key == "phase":
            return self._controller.phase_attributes()
        return None
