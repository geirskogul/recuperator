"""Connects the breathing logic to Home Assistant: probes in, fan switches out."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_EXHAUST_SWITCH,
    CONF_INSIDE_SENSOR,
    CONF_INTAKE_SWITCH,
    CONF_OUTSIDE_SENSOR,
    DEFAULTS,
    MODE_AUTOMATIC,
    PHASE_EXHAUST,
    PHASE_INTAKE,
    SETTINGS_BY_KEY,
    STARTUP_WAIT_SECONDS,
)
from .logic import INSIDE, OUTSIDE, BreathingLogic, Settings

_LOGGER = logging.getLogger(__name__)

TICK = timedelta(seconds=1)
RESEND_SECONDS = 5  # re-send a fan command at most this often if the fan ignores it


class RecuperatorController:
    """Runs the cycle for one recuperator (one config entry)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Remember the chosen entities; nothing runs until async_start."""
        self.hass = hass
        self.entry = entry
        self.exhaust_switch: str = entry.data[CONF_EXHAUST_SWITCH]
        self.intake_switch: str = entry.data[CONF_INTAKE_SWITCH]
        self.inside_sensor: str = entry.data[CONF_INSIDE_SENSOR]
        self.outside_sensor: str = entry.data[CONF_OUTSIDE_SENSOR]
        self.logic = BreathingLogic()
        self.enabled = False
        self.mode = MODE_AUTOMATIC
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._last_command: dict[str, tuple[str, float]] = {}
        self._hands_off = True  # when disabled, leave the fans alone after turning them off
        self._start_deadline: float | None = None  # waiting for the probes before the first phase

    # -- settings ---------------------------------------------------------------

    @property
    def settings(self) -> Settings:
        """Current settings: stored options over defaults."""
        return Settings.from_mapping(dict(self.entry.options))

    def setting(self, key: str) -> float:
        """One setting's current value."""
        return float(self.entry.options.get(key, DEFAULTS[key]))

    async def async_set_setting(self, key: str, value: float) -> None:
        """Store one setting (called by the number entities)."""
        spec = SETTINGS_BY_KEY[key]
        value = min(max(float(value), spec.minimum), spec.maximum)
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, key: value}
        )

    async def async_reset_settings(self) -> None:
        """Put every setting back to its default."""
        _LOGGER.info("%s: settings reset to defaults", self.entry.title)
        self.hass.config_entries.async_update_entry(self.entry, options=dict(DEFAULTS))

    @callback
    def async_options_updated(self) -> None:
        """Settings changed (from a number entity, the button or Configure)."""
        self._notify()

    # -- listeners (the entities) ---------------------------------------------------

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Call `update` whenever something visible changes."""
        self._listeners.append(update)

        @callback
        def remove() -> None:
            self._listeners.remove(update)

        return remove

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    # -- lifecycle ----------------------------------------------------------------

    async def async_start(self) -> None:
        """Start watching the probes and ticking once a second."""
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self.inside_sensor, self.outside_sensor], self._probe_changed
            )
        )
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, TICK))
        now = self._now()
        self.logic.record(INSIDE, now, self._read(self.inside_sensor))
        self.logic.record(OUTSIDE, now, self._read(self.outside_sensor))

    async def async_stop(self) -> None:
        """Stop the loop and leave both fans off."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self.enabled:
            await self._async_both_off()

    # -- user controls ------------------------------------------------------------

    async def async_set_enabled(self, enabled: bool) -> None:
        """Turn breathing on or off (the Breathing switch)."""
        if enabled == self.enabled:
            return
        self.enabled = enabled
        now = self._now()
        if enabled:
            self._hands_off = False
            # Right after a restart the probes (e.g. ESPHome) take a few seconds to
            # connect. Wait up to STARTUP_WAIT_SECONDS for both before the first
            # phase, so it can be temperature-driven instead of timed.
            self._start_deadline = now + STARTUP_WAIT_SECONDS
            self._maybe_start(now)
        else:
            self._start_deadline = None
            self.logic.stop()
            _LOGGER.info("%s: breathing stopped", self.entry.title)
            await self._async_both_off()
            self._hands_off = True
        await self._async_apply()
        self._notify()

    def _maybe_start(self, now: float) -> None:
        """Start the cycle once both probes read, or when the wait runs out."""
        if self._start_deadline is None:
            return
        inside, outside = self._probes()
        if (inside is None or outside is None) and now < self._start_deadline:
            return
        self._start_deadline = None
        self.logic.start(now, self.mode, self.settings, inside, outside)
        _LOGGER.info("%s: breathing started (%s)", self.entry.title, self.mode)
        self._notify()

    async def async_set_mode(self, mode: str) -> None:
        """Change mode (the Mode select); takes effect on the next tick."""
        self.mode = mode
        if self.enabled:
            await self._async_run(self._now())
        self._notify()

    # -- the loop -------------------------------------------------------------------

    def _now(self) -> float:
        return dt_util.utcnow().timestamp()

    def _read(self, entity_id: str) -> float | None:
        """A probe's temperature, or None if it is unavailable or not a number."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _probes(self) -> tuple[float | None, float | None]:
        return self._read(self.inside_sensor), self._read(self.outside_sensor)

    @callback
    def _probe_changed(self, event: Event) -> None:
        now = self._now()
        entity_id = event.data["entity_id"]
        probe = INSIDE if entity_id == self.inside_sensor else OUTSIDE
        self.logic.record(probe, now, self._read(entity_id))

    async def _tick(self, _now=None) -> None:
        await self._async_run(self._now())

    async def _async_run(self, now: float) -> None:
        if not self.enabled:
            return
        self._maybe_start(now)
        changed = self.logic.step(now, self.mode, self.settings, *self._probes())
        await self._async_apply()
        if changed:
            _LOGGER.debug(
                "%s: phase %s (last change: %s)",
                self.entry.title,
                self.logic.phase,
                self.logic.last_reason,
            )
            self._notify()

    # -- driving the fans -------------------------------------------------------------

    def _wanted(self) -> dict[str, bool]:
        phase = self.logic.phase
        return {
            self.exhaust_switch: self.enabled and phase == PHASE_EXHAUST,
            self.intake_switch: self.enabled and phase == PHASE_INTAKE,
        }

    def _is_on(self, entity_id: str) -> bool | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state.state == STATE_ON

    async def _async_apply(self) -> None:
        """Make the fans match the phase. Always switch off before switching on.

        A fan is only switched on once the other one reports off, so both are
        never on together, even if a switch is slow or was flipped by hand.
        """
        if self._hands_off:
            return
        wanted = self._wanted()
        for entity_id, on in wanted.items():
            if not on and self._is_on(entity_id) is not False:
                await self._async_command(entity_id, False)
        for entity_id, on in wanted.items():
            if not on:
                continue
            other = self.intake_switch if entity_id == self.exhaust_switch else self.exhaust_switch
            if self._is_on(other):
                continue  # interlock: wait for the other fan to report off
            if self._is_on(entity_id) is not True:
                await self._async_command(entity_id, True)

    async def _async_command(self, entity_id: str, on: bool) -> None:
        """Send turn_on/turn_off, but not more than once per RESEND_SECONDS."""
        now = self._now()
        service = SERVICE_TURN_ON if on else SERVICE_TURN_OFF
        last = self._last_command.get(entity_id)
        if last and last[0] == service and now - last[1] < RESEND_SECONDS:
            return
        state = self.hass.states.get(entity_id)
        if state is None or state.state == STATE_UNAVAILABLE:
            return  # nothing to talk to; try again on a later tick
        self._last_command[entity_id] = (service, now)
        await self.hass.services.async_call(
            entity_id.split(".", 1)[0], service, {ATTR_ENTITY_ID: entity_id}, blocking=False
        )

    async def _async_both_off(self) -> None:
        for entity_id in (self.exhaust_switch, self.intake_switch):
            if self._is_on(entity_id) is not False:
                self._last_command.pop(entity_id, None)
                await self._async_command(entity_id, False)

    # -- read-outs for the entities -------------------------------------------------

    def phase_attributes(self) -> dict:
        """Extra detail for the Phase sensor."""
        started = self.logic.phase_started
        return {
            "phase_started": dt_util.utc_from_timestamp(started).isoformat() if started else None,
            "next_phase": self.logic.next_phase,
            "timed": self.logic.timed_reason,
            "cold_weather": self.logic.cold,
            "mode": self.mode,
        }
