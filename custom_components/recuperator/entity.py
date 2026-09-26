"""Shared entity base for the Recuperator platforms."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, MODEL
from .controller import RecuperatorController

REPLAY_DEVICE = "replay"


def main_device_info(entry: ConfigEntry) -> DeviceInfo:
    """The recuperator's own device."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model=MODEL,
    )


def replay_device_info(entry: ConfigEntry) -> DeviceInfo:
    """A sub-device holding the replay: its image, button and settings.

    It keeps the replay out of the recuperator's own settings on the device
    page, and shows up there under "Connected devices".
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{REPLAY_DEVICE}")},
        name=f"{entry.title} Replay",
        manufacturer=MANUFACTURER,
        model="Replay",
        via_device=(DOMAIN, entry.entry_id),
    )


class RecuperatorEntity(Entity):
    """Base entity: attaches to the recuperator device and follows the controller."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, controller: RecuperatorController, entry: ConfigEntry, key: str, device: str | None = None
    ) -> None:
        """Bind the entity to its controller and to the config entry's device (or its replay device)."""
        self._controller = controller
        self._entry = entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = replay_device_info(entry) if device == REPLAY_DEVICE else main_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Subscribe to controller updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self._controller.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        if self.hass is None or self.entity_id is None:
            return
        self.async_write_ha_state()
