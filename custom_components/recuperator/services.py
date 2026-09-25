"""The "Create replay" action: an animated diagram from recorded history."""

from __future__ import annotations

from datetime import timedelta
import os

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import DOMAIN
from .replay import MAX_FRAMES, Frame, frame_times, render_replay_svg, sample

SERVICE_CREATE_REPLAY = "create_replay"
ATTR_CONFIG_ENTRY = "config_entry_id"
ATTR_HOURS = "hours"
ATTR_END = "end"
ATTR_PLAYBACK = "playback_seconds"
ATTR_FRAMES = "frames"

SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
        vol.Optional(ATTR_HOURS): vol.All(vol.Coerce(float), vol.Range(min=0.25, max=168)),
        vol.Optional(ATTR_END): cv.datetime,
        vol.Optional(ATTR_PLAYBACK): vol.All(vol.Coerce(float), vol.Range(min=5, max=900)),
        vol.Optional(ATTR_FRAMES): vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_FRAMES)),
    }
)

# action field -> stored setting (the last values used are remembered)
SAVED = {ATTR_HOURS: "replay_hours", ATTR_PLAYBACK: "replay_playback_seconds", ATTR_FRAMES: "replay_frames"}


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _async_create_replay(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
    if (wanted := call.data.get(ATTR_CONFIG_ENTRY)) is not None:
        entries = [e for e in entries if e.entry_id == wanted]
    if not entries:
        raise ServiceValidationError("No loaded recuperator found.")
    entry = entries[0]
    # Remember the values given, so the Create replay button reuses them.
    given = {SAVED[k]: call.data[k] for k in SAVED if k in call.data}
    if given:
        hass.config_entries.async_update_entry(entry, options={**entry.options, **given})
    return await async_create_replay(hass, entry, end=call.data.get(ATTR_END))


async def async_create_replay(hass: HomeAssistant, entry, end=None) -> dict:
    """Build a replay for one recuperator with its saved replay settings."""
    if "recorder" not in hass.config.components:
        raise ServiceValidationError("The replay needs Home Assistant's recorder (history).")
    from homeassistant.components.recorder import get_instance, history

    controller = entry.runtime_data
    hours = controller.setting("replay_hours")
    playback = controller.setting("replay_playback_seconds")
    frames_wanted = int(controller.setting("replay_frames"))
    end = end or dt_util.utcnow()
    end = dt_util.as_utc(end if end.tzinfo else dt_util.as_local(end))
    start = end - timedelta(hours=hours)
    count = frames_wanted or min(MAX_FRAMES, max(60, int(hours * 60)))

    phase_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_phase")
    ids = [controller.inside_sensor, controller.outside_sensor] + ([phase_id] if phase_id else [])
    states = await get_instance(hass).async_add_executor_job(
        history.get_significant_states, hass, start, end, ids, None, True, False, False, True
    )

    def series(entity_id):
        return sorted((s.last_changed, s.state) for s in states.get(entity_id, []))

    times = frame_times(start, end, count)
    inside = sample(series(controller.inside_sensor), times)
    outside = sample(series(controller.outside_sensor), times)
    phase = sample(series(phase_id), times) if phase_id else ["stopped"] * len(times)
    frames = [
        Frame(dt_util.as_local(t), _float(i), _float(o), p or "stopped")
        for t, i, o, p in zip(times, inside, outside, phase)
    ]
    svg = render_replay_svg(frames, playback, entry.title, controller.palette)

    # Also save it where Home Assistant serves files: /config/www -> /local/
    name = f"{slugify(entry.title)}-replay.svg"
    folder = hass.config.path("www", "recuperator")

    def _write() -> None:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(svg)

    await hass.async_add_executor_job(_write)
    controller.set_replay(svg.encode())
    return {
        "url": f"/local/recuperator/{name}",
        "file": os.path.join(folder, name),
        "start": dt_util.as_local(start).isoformat(),
        "end": dt_util.as_local(end).isoformat(),
        "frames": len(frames),
        "playback_seconds": playback,
    }


def async_setup_services(hass: HomeAssistant) -> None:
    async def handler(call: ServiceCall) -> ServiceResponse:
        return await _async_create_replay(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_REPLAY, handler, schema=SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
