"""The Breathing switch (turns the cycle on and off), and the on/off settings."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_DIAGRAM_AIRFLOW,
    CONF_DRYING,
    CONF_DRYING_EXHAUST_ONLY,
    CONF_REPLAY_GIF,
    CONF_REPLAY_LINKED,
    DOMAIN,
)
from .entity import REPLAY_DEVICE, RecuperatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    switches: list[SwitchEntity] = [
        BreathingSwitch(c, entry, "breathing"),
        PassiveIntakeSwitch(c, entry, "passive_intake"),
        OptionSwitch(c, entry, CONF_DRYING, "mdi:water-off-outline"),
        OptionSwitch(c, entry, CONF_DRYING_EXHAUST_ONLY, "mdi:fan-chevron-up"),
        OptionSwitch(c, entry, CONF_DIAGRAM_AIRFLOW, "mdi:weather-windy"),
        OptionSwitch(c, entry, CONF_REPLAY_GIF, "mdi:file-gif-box", REPLAY_DEVICE),
    ]
    # Drawing the synced recuperator too only makes sense while there is one.
    if c.link_entry_id:
        switches.append(OptionSwitch(c, entry, CONF_REPLAY_LINKED, "mdi:link-variant", REPLAY_DEVICE))
    elif entity_id := er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_{CONF_REPLAY_LINKED}"):
        er.async_get(hass).async_remove(entity_id)
    async_add_entities(switches)


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


class OptionSwitch(RecuperatorEntity, SwitchEntity):
    """An on/off setting (Drying, Drying exhaust only, Diagram airflow, the replay's); applies on the next tick."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller, entry, key: str, icon: str, device: str | None = None) -> None:
        super().__init__(controller, entry, key, device)
        self._key = key
        self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        return self._controller.option(self._key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.async_set_option(self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.async_set_option(self._key, False)
