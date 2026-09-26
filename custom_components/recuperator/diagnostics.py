"""Diagnostics: everything needed to understand a bug report, in one download.

Settings > Devices & services > Recuperator > three-dot menu > Download diagnostics.
Nothing here is secret (entity IDs, settings, temperatures), so nothing is redacted.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import DOMAIN


def _iso(timestamp: float | None) -> str | None:
    return dt_util.utc_from_timestamp(timestamp).isoformat() if timestamp else None


def _entity_state(hass: HomeAssistant, entity_id: str | None) -> dict[str, Any] | None:
    """The state and unit of a wired entity, as Home Assistant sees it now."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None:
        return {"entity_id": entity_id, "state": None}
    return {
        "entity_id": entity_id,
        "state": state.state,
        "unit": state.attributes.get("unit_of_measurement"),
        "last_changed": state.last_changed.isoformat(),
    }


def _logic_state(logic) -> dict[str, Any]:
    """Where the breathing cycle is, and what it has learned."""
    return {
        "phase": logic.phase,
        "next_phase": logic.next_phase,
        "phase_started": _iso(logic.phase_started),
        "timed_reason": logic.timed_reason,
        "last_reason": logic.last_reason,
        "last_exhaust_seconds": logic.last_exhaust_seconds,
        "last_intake_seconds": logic.last_intake_seconds,
        "basement_estimate": logic.basement_estimate,
        "outdoor_estimate": logic.outdoor_estimate,
        "last_recovery_percent": logic.last_recovery_percent,
        "cold": logic.cold,
        "frost_risk": logic.frost_risk,
        "passive": logic.passive,
        "passive_flow": logic.passive_flow,
        "passive_flow_after_seconds": logic.passive_flow_after_seconds,
    }


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry) -> dict[str, Any]:
    """Diagnostics for one recuperator."""
    controller = entry.runtime_data
    integration = await async_get_integration(hass, DOMAIN)
    inside, outside = controller.probes()
    fans = {entity_id: _entity_state(hass, entity_id) for entity_id in controller.fans()}
    return {
        "integration_version": str(integration.version),
        "entry": {
            "title": entry.title,
            "version": f"{entry.version}.{entry.minor_version}",
            "unique_id": entry.unique_id,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "controller": {
            "enabled": controller.enabled,
            "mode": controller.mode,
            "display_unit": controller.display_unit,
            "link_type": controller.link_type,
            "linked_state": controller.linked_state(),
            "wanted_fans": controller.wanted_fans(),
        },
        "probes_celsius": {"inside": inside, "outside": outside},
        "probes": {
            "inside": _entity_state(hass, controller.inside_sensor),
            "outside": _entity_state(hass, controller.outside_sensor),
        },
        "fans": fans,
        "logic": _logic_state(controller.logic),
        "replay": {
            "made": controller.replay_time.isoformat() if controller.replay_time else None,
            "bytes": len(controller.replay_svg) if controller.replay_svg else 0,
        },
    }
