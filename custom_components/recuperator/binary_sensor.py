"""Cold weather: on while the cold-weather limits are in force."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import RecuperatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ColdWeather(entry.runtime_data, entry, "cold_weather")])


class ColdWeather(RecuperatorEntity, BinarySensorEntity):
    _attr_icon = "mdi:snowflake"

    @property
    def is_on(self) -> bool:
        return self._controller.logic.cold
