"""The Recuperator integration.

Runs a single-tube ceramic recuperator (heat-recovery ventilator) as a breathing
cycle: one fan blows basement air out through the core, pauses, then a second
fan blows outdoor air in through the same core. Two temperature probes, one at
each end of the core, decide when each phase has done its job.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .controller import RecuperatorController
from .services import async_setup_services

type RecuperatorConfigEntry = ConfigEntry[RecuperatorController]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the Create replay action."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RecuperatorConfigEntry) -> bool:
    """Set up one recuperator from a config entry.

    The Breathing switch and Mode select restore their previous state into the
    controller when they are added, so the loop starts after the platforms.
    """
    controller = RecuperatorController(hass, entry)
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await controller.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RecuperatorConfigEntry) -> bool:
    """Stop the loop (fans off) and unload the platforms."""
    await entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: RecuperatorConfigEntry) -> None:
    """Settings changed: apply them live, without restarting the cycle."""
    entry.runtime_data.async_options_updated()
