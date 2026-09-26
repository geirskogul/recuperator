"""One number entity per tunable setting, so everything can be tuned from a dashboard."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import REPLAY_KEYS, SETTINGS, Setting
from .entity import REPLAY_DEVICE, RecuperatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities(SettingNumber(entry.runtime_data, entry, s) for s in SETTINGS)


class SettingNumber(RecuperatorEntity, NumberEntity):
    """A setting; changes apply on the next tick without restarting the cycle.

    Absolute temperatures are temperature numbers, so Home Assistant shows them
    in the user's units (°C is stored). Advanced settings start disabled on new
    installs; they are always available in Configure, Settings.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, controller, entry, setting: Setting) -> None:
        device = REPLAY_DEVICE if setting.key in REPLAY_KEYS else None
        super().__init__(controller, entry, setting.key, device)
        self._key = setting.key
        self._attr_native_min_value = setting.minimum
        self._attr_native_max_value = setting.maximum
        self._attr_native_step = setting.step
        self._attr_native_unit_of_measurement = setting.unit
        self._attr_icon = setting.icon
        self._attr_entity_registry_enabled_default = not setting.advanced
        if setting.temperature:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE

    @property
    def native_value(self) -> float:
        return self._controller.setting(self._key)

    async def async_set_native_value(self, value: float) -> None:
        await self._controller.async_set_setting(self._key, value)
