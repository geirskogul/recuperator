"""The Breathing switch: turns the cycle on and off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import RecuperatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities([BreathingSwitch(c, entry, "breathing"), PassiveIntakeSwitch(c, entry, "passive_intake")])


class BreathingSwitch(RecuperatorEntity, SwitchEntity, RestoreEntity):
    """On: the fans follow the cycle. Off: both fans off, then left alone."""

    _attr_icon = "mdi:lungs"

    async def async_added_to_hass(self) -> None:
        """Restore the last on/off state (a new recuperator starts off)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_ON:
            await self._controller.async_set_enabled(True)

    @property
    def is_on(self) -> bool:
        return self._controller.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.async_set_enabled(False)


class PassiveIntakeSwitch(RecuperatorEntity, SwitchEntity):
    """On: intake phases run with the intake fan off (passive re-ventilation)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:fan-off"

    @property
    def is_on(self) -> bool:
        return self._controller.passive_intake

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.async_set_passive_intake(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.async_set_passive_intake(False)
