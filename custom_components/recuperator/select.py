"""The Mode select (automatic, timed, or one fan continuously) and the Phase limit select."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import MODES, PHASE_LIMITS
from .entity import RecuperatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities([ModeSelect(c, entry, "mode"), PhaseLimitSelect(c, entry, "phase_limit")])


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


class PhaseLimitSelect(RecuperatorEntity, SelectEntity):
    """Keep the intake (or the exhaust) shorter than the other phase: off, limited intake, limited exhaust."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = PHASE_LIMITS
    _attr_icon = "mdi:scale-unbalanced"

    @property
    def current_option(self) -> str:
        return self._controller.phase_limit

    async def async_select_option(self, option: str) -> None:
        await self._controller.async_set_phase_limit(option)
