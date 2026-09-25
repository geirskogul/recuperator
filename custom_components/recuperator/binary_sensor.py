"""Cold weather and frost risk indicators."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import RecuperatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities([ColdWeather(c, entry, "cold_weather"), FrostRisk(c, entry, "frost_risk")])


class ColdWeather(RecuperatorEntity, BinarySensorEntity):
    """On while the cold-weather limits are in force."""

    _attr_icon = "mdi:snowflake"

    @property
    def is_on(self) -> bool:
        return self._controller.logic.cold


class FrostRisk(RecuperatorEntity, BinarySensorEntity):
    """On if, in cold weather, the last exhaust never warmed the core's outdoor face above freezing."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:snowflake-alert"

    @property
    def is_on(self) -> bool:
        return self._controller.logic.frost_risk
