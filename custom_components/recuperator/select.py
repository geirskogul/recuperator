"""The Mode select: automatic, timed, or one fan continuously."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import MODES
from .entity import RecuperatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ModeSelect(entry.runtime_data, entry, "mode")])


class ModeSelect(RecuperatorEntity, SelectEntity, RestoreEntity):
    """How the cycle decides when to switch."""

    _attr_options = MODES
    _attr_icon = "mdi:swap-horizontal"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in MODES:
            await self._controller.async_set_mode(last.state)

    @property
    def current_option(self) -> str:
        return self._controller.mode

    async def async_select_option(self, option: str) -> None:
        await self._controller.async_set_mode(option)
