"""The Recuperator integration.

Runs a single-tube ceramic recuperator (heat-recovery ventilator) as a breathing
cycle: one fan blows basement air out through the core, pauses, then a second
fan blows outdoor air in through the same core. Two temperature probes, one at
each end of the core, decide when each phase has done its job.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, LEGACY_TIMED_PHASE, PLATFORMS
from .controller import RecuperatorController
from .entity import main_device_info
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

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
    # The main device first: the Replay device hangs off it (via_device).
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **main_device_info(entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await controller.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: RecuperatorConfigEntry) -> bool:
    """Bring older entries up to date.

    1.1 -> 1.2: the single Timed phase length becomes separate Timed exhaust
    and Timed intake lengths, both starting at the old value.
    """
    if entry.version > 1:
        return False  # made by a newer version of the integration
    if entry.minor_version < 2:
        options = dict(entry.options)
        legacy = options.pop(LEGACY_TIMED_PHASE, None)
        if legacy is not None:
            options.setdefault("timed_exhaust_seconds", legacy)
            options.setdefault("timed_intake_seconds", legacy)
        _remove_entity(hass, "number", f"{entry.entry_id}_{LEGACY_TIMED_PHASE}")
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
        _LOGGER.info("%s: Timed phase split into Timed exhaust and Timed intake", entry.title)
    return True


def _remove_entity(hass: HomeAssistant, domain: str, unique_id: str) -> None:
    """Drop an entity this integration no longer provides."""
    registry = er.async_get(hass)
    if entity_id := registry.async_get_entity_id(domain, DOMAIN, unique_id):
        registry.async_remove(entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: RecuperatorConfigEntry) -> bool:
    """Stop the loop (fans off) and unload the platforms."""
    await entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: RecuperatorConfigEntry) -> None:
    """Settings changed: apply them live, without restarting the cycle."""
    entry.runtime_data.async_options_updated()
