"""The "Create replay" action: an animated diagram from recorded history."""

from __future__ import annotations

from datetime import datetime, timedelta
import os

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import DOMAIN
from .controller import probe_celsius
from .replay import MAX_FRAMES, Frame, frame_times, render_replay_svg, sample

SERVICE_CREATE_REPLAY = "create_replay"
ATTR_CONFIG_ENTRY = "config_entry_id"
ATTR_HOURS = "hours"
ATTR_START = "start"
ATTR_END = "end"
ATTR_PLAYBACK = "playback_seconds"
ATTR_FRAMES = "frames"

SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
        # A period is either "hours" ending at "end" (or now), or "start" to "end" (or now).
        vol.Exclusive(ATTR_HOURS, "period"): vol.All(vol.Coerce(float), vol.Range(min=0.25, max=168)),
        vol.Exclusive(ATTR_START, "period"): cv.datetime,
        vol.Optional(ATTR_END): cv.datetime,
        vol.Optional(ATTR_PLAYBACK): vol.All(vol.Coerce(float), vol.Range(min=5, max=900)),
        vol.Optional(ATTR_FRAMES): vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_FRAMES)),
    }
)

# action field -> stored setting (the last values used are remembered)
SAVED = {ATTR_HOURS: "replay_hours", ATTR_PLAYBACK: "replay_playback_seconds", ATTR_FRAMES: "replay_frames"}

MIN_PERIOD = timedelta(minutes=5)
MAX_PERIOD = timedelta(days=31)


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    """A translated error for the action."""
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders or None
    )


def _pick_entry(hass: HomeAssistant, wanted: str | None) -> ConfigEntry:
    """The recuperator the action is for.

    With one recuperator it may be left out; with several it must be given,
    rather than silently replaying whichever happens to come first.
    """
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
    if wanted is not None:
        for entry in entries:
            if entry.entry_id == wanted:
                return entry
        raise _error("recuperator_not_found")
    if not entries:
        raise _error("no_recuperator")
    if len(entries) > 1:
        raise _error("several_recuperators", names=", ".join(sorted(e.title for e in entries)))
    return entries[0]


async def _async_create_replay(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    entry = _pick_entry(hass, call.data.get(ATTR_CONFIG_ENTRY))
    # Remember the values given, so the Create replay button reuses them.
    given = {SAVED[k]: call.data[k] for k in SAVED if k in call.data}
    if given:
        hass.config_entries.async_update_entry(entry, options={**entry.options, **given})
    return await async_create_replay(hass, entry, start=call.data.get(ATTR_START), end=call.data.get(ATTR_END))


def replay_file(hass: HomeAssistant, entry: ConfigEntry) -> tuple[str, str]:
    """(folder, file name) of the saved replay: /config/www/recuperator/<name>-replay.svg."""
    return hass.config.path("www", "recuperator"), f"{slugify(entry.title)}-replay.svg"


async def _async_history(hass: HomeAssistant, start: datetime, end: datetime, ids: list[str]) -> dict:
    """Recorded states of the given entities, with attributes (for the probes' units)."""
    from homeassistant.components.recorder import get_instance, history

    def _read() -> dict:
        return history.get_significant_states(
            hass,
            start,
            end,
            entity_ids=ids,
            include_start_time_state=True,
            significant_changes_only=False,
            minimal_response=False,
            no_attributes=False,
        )

    return await get_instance(hass).async_add_executor_job(_read)


def _frames(states: dict, times: list[datetime], inside_id: str, outside_id: str, phase_id: str | None) -> list[Frame]:
    """One frame per time: the last recorded value of each entity at that moment."""

    def series(entity_id, value):
        return sorted((s.last_changed, value(s)) for s in states.get(entity_id, []))

    inside = sample(series(inside_id, probe_celsius), times)
    outside = sample(series(outside_id, probe_celsius), times)
    phase = sample(series(phase_id, lambda s: s.state), times) if phase_id else ["stopped"] * len(times)
    return [
        Frame(dt_util.as_local(t), i, o, p or "stopped")
        for t, i, o, p in zip(times, inside, outside, phase)
    ]


def _as_utc(when: datetime) -> datetime:
    """A time from the action in UTC; one without a time zone is local time."""
    return dt_util.as_utc(when if when.tzinfo else dt_util.as_local(when))


def _period(hours: float, start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    """The replayed period in UTC: start to end if a start is given, else `hours` up to end.

    A missing end is now.
    """
    end = _as_utc(end or dt_util.utcnow())
    if start is None:
        return end - timedelta(hours=hours), end
    start = _as_utc(start)
    if end - start < MIN_PERIOD:
        raise _error("period_too_short")
    if end - start > MAX_PERIOD:
        raise _error("period_too_long")
    return start, end


async def async_create_replay(
    hass: HomeAssistant, entry: ConfigEntry, start: datetime | None = None, end: datetime | None = None
) -> dict:
    """Build a replay for one recuperator with its saved replay settings.

    The period is start to end when a start is given (a replay card's date
    picker), otherwise the saved Hours up to end; a missing end is now.
    """
    if "recorder" not in hass.config.components:
        raise _error("recorder_needed")
    controller = entry.runtime_data
    playback = controller.setting("replay_playback_seconds")
    frames_wanted = int(controller.setting("replay_frames"))
    start, end = _period(controller.setting("replay_hours"), start, end)
    minutes = (end - start).total_seconds() / 60
    count = frames_wanted or min(MAX_FRAMES, max(60, int(minutes)))

    phase_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_phase")
    ids = [controller.inside_sensor, controller.outside_sensor] + ([phase_id] if phase_id else [])
    states = await _async_history(hass, start, end, ids)
    times = frame_times(start, end, count)
    frames = _frames(states, times, controller.inside_sensor, controller.outside_sensor, phase_id)
    svg = render_replay_svg(frames, playback, entry.title, controller.palette, controller.display_unit)

    # Also save it where Home Assistant serves files: /config/www -> /local/
    folder, name = replay_file(hass, entry)

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


async def async_load_saved_replay(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """After a restart, show the last saved replay again instead of the placeholder."""
    folder, name = replay_file(hass, entry)
    path = os.path.join(folder, name)

    def _read() -> tuple[bytes, float] | None:
        try:
            with open(path, "rb") as f:
                return f.read(), os.path.getmtime(path)
        except OSError:
            return None

    if (saved := await hass.async_add_executor_job(_read)) is not None:
        svg, mtime = saved
        entry.runtime_data.set_replay(svg, dt_util.utc_from_timestamp(mtime))


def async_setup_services(hass: HomeAssistant) -> None:
    async def handler(call: ServiceCall) -> ServiceResponse:
        return await _async_create_replay(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_REPLAY, handler, schema=SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
