"""Shared entity base for the Recuperator platforms."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, MODEL
from .controller import RecuperatorController


class RecuperatorEntity(Entity):
    """Base entity: attaches to the recuperator device and follows the controller."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: RecuperatorController, entry: ConfigEntry, key: str) -> None:
        """Bind the entity to its controller and to the config entry's device."""
        self._controller = controller
        self._entry = entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to controller updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self._controller.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        if self.hass is None or self.entity_id is None:
            return
        self.async_write_ha_state()
