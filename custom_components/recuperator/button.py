"""Reset settings to defaults."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import REPLAY_DEVICE, RecuperatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    buttons: list[ButtonEntity] = [
        ResetButton(c, entry, "reset_settings"),
        CreateReplayButton(c, entry, "create_replay", REPLAY_DEVICE),
    ]
    # Only a unit in a synced group has anyone to copy to.
    registry = er.async_get(hass)
    if c.link_entry_id:
        buttons.append(CopySettingsButton(c, entry, "copy_settings"))
    elif entity_id := registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_copy_settings"):
        registry.async_remove(entity_id)
    async_add_entities(buttons)


class ResetButton(RecuperatorEntity, ButtonEntity):
    """Puts every setting back to its default (the fans and probes are kept)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restore"

    async def async_press(self) -> None:
        await self._controller.async_reset_settings()


class CopySettingsButton(RecuperatorEntity, ButtonEntity):
    """Gives the other recuperators of the synced group this one's cycle settings."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:content-copy"

    async def async_press(self) -> None:
        await self._controller.async_copy_settings_to_group()


class CreateReplayButton(RecuperatorEntity, ButtonEntity):
    """Makes a new replay with the saved replay settings (Replay hours, Replay
    playback length, Replay frames: the last values used by the action)."""

    _attr_icon = "mdi:movie-open-play-outline"

    async def async_press(self) -> None:
        from .services import async_create_replay

        await async_create_replay(self.hass, self._entry)
