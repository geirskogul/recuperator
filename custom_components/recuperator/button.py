"""Reset settings to defaults."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import RecuperatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ResetButton(entry.runtime_data, entry, "reset_settings")])


class ResetButton(RecuperatorEntity, ButtonEntity):
    """Puts every setting back to its default (the fans and probes are kept)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restore"

    async def async_press(self) -> None:
        await self._controller.async_reset_settings()
