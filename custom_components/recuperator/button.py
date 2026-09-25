"""Reset settings to defaults."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import RecuperatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities([ResetButton(c, entry, "reset_settings"), CreateReplayButton(c, entry, "create_replay")])


class ResetButton(RecuperatorEntity, ButtonEntity):
    """Puts every setting back to its default (the fans and probes are kept)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restore"

    async def async_press(self) -> None:
        await self._controller.async_reset_settings()


class CreateReplayButton(RecuperatorEntity, ButtonEntity):
    """Makes a new replay with the saved replay settings (Replay hours, Replay
    playback length, Replay frames: the last values used by the action)."""

    _attr_icon = "mdi:movie-open-play-outline"

    async def async_press(self) -> None:
        from .services import async_create_replay

        await async_create_replay(self.hass, self._entry)
