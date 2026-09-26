"""Connects the breathing logic to Home Assistant: probes in, fan switches out."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    CONF_EXHAUST_SWITCH,
    CONF_INSIDE_SENSOR,
    CONF_INTAKE_SWITCH,
    CONF_LINK_EXHAUST_SWITCH,
    CONF_LINK_INTAKE_SWITCH,
    CONF_LINK_TYPE,
    CONF_OUTSIDE_SENSOR,
    CONF_PALETTE,
    CONF_PASSIVE_INTAKE,
    DEFAULTS,
    LINK_EXHAUST_FAN,
    LINK_INTAKE_FAN,
    LINK_NONE,
    LINK_RECUPERATOR,
    LINKED_EXHAUST,
    LINKED_IDLE,
    LINKED_INTAKE,
    LINKED_NOT_LINKED,
    MODE_AUTOMATIC,
    PHASE_EXHAUST,
    PHASE_INTAKE,
    SETTINGS_BY_KEY,
    STARTUP_WAIT_SECONDS,
)
from .diagram import PALETTE, Palette, palette_to_text, parse_palette
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
        # A linked unit breathes opposite to this one: its intake runs while this
        # one exhausts, its exhaust while this one takes air in.
        self.link_type: str = entry.data.get(CONF_LINK_TYPE, LINK_NONE)
        self.link_exhaust_switch, self.link_intake_switch = linked_fans(entry.data)
        self.logic = BreathingLogic()
        self.enabled = False
        self.mode = MODE_AUTOMATIC
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._last_command: dict[str, tuple[str, float]] = {}
        self._hands_off = True  # when disabled, leave the fans alone after turning them off
        self._start_deadline: float | None = None  # waiting for the probes before the first phase
        self.replay_svg: bytes | None = None  # the last replay made by the Create replay action
        self.replay_time = None
        self._replay_listeners: list[Callable[[], None]] = []

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

    @property
    def palette(self) -> Palette:
        """The diagram's colour scale: the stored one if valid, otherwise the default."""
        text = self.entry.options.get(CONF_PALETTE)
        if not text:
            return PALETTE
        try:
            return parse_palette(text)
        except ValueError:
            return PALETTE

    async def async_set_palette(self, text: str) -> None:
        """Store a new colour scale (raises ValueError if it cannot be read)."""
        palette = parse_palette(text)
        options = {**self.entry.options, CONF_PALETTE: palette_to_text(palette)}
        if palette == PALETTE:
            options.pop(CONF_PALETTE, None)
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    @property
    def passive_intake(self) -> bool:
        return bool(self.entry.options.get(CONF_PASSIVE_INTAKE, False))

    async def async_set_passive_intake(self, on: bool) -> None:
        """Turn passive intake on or off (applies from the next intake phase)."""
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, CONF_PASSIVE_INTAKE: bool(on)}
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
    def async_add_replay_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Call `update` when a new replay has been made."""
        self._replay_listeners.append(update)
        return lambda: self._replay_listeners.remove(update)

    @callback
    def set_replay(self, svg: bytes, when: datetime | None = None) -> None:
        """Store a new replay (made at `when`, default now) and tell the replay image."""
        self.replay_svg = svg
        self.replay_time = when or dt_util.utcnow()
        for update in list(self._replay_listeners):
            update()

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
        inside, outside = self.probes()
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
        """A probe's temperature in °C, or None if it is unavailable or not a number."""
        return probe_celsius(self.hass.states.get(entity_id))

    @property
    def display_unit(self) -> str:
        """The unit the user sees temperatures in (°C or °F)."""
        return self.hass.config.units.temperature_unit

    def probes(self) -> tuple[float | None, float | None]:
        """(inside, outside) probe readings in °C."""
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
        changed = self.logic.step(now, self.mode, self.settings, *self.probes())
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

    def wanted_fans(self) -> dict[str, bool]:
        """Which fans should be on now (every fan this recuperator drives)."""
        phase = self.logic.phase if self.enabled else None
        wanted = {
            self.exhaust_switch: phase == PHASE_EXHAUST,
            # Passive intake: the intake fan stays off; air refills on its own.
            self.intake_switch: phase == PHASE_INTAKE and not self.logic.passive,
        }
        # The linked unit moves air the other way, so the house stays balanced.
        # During a passive intake its exhaust is what draws air in through this core.
        if self.link_intake_switch:
            wanted[self.link_intake_switch] = phase == PHASE_EXHAUST
        if self.link_exhaust_switch:
            wanted[self.link_exhaust_switch] = phase == PHASE_INTAKE
        return wanted

    def _partner(self, entity_id: str) -> str | None:
        """The other fan of the same unit: the two must never run together."""
        pairs = [(self.exhaust_switch, self.intake_switch)]
        if self.link_exhaust_switch and self.link_intake_switch:
            pairs.append((self.link_exhaust_switch, self.link_intake_switch))
        for a, b in pairs:
            if entity_id == a:
                return b
            if entity_id == b:
                return a
        return None

    def fans(self) -> list[str]:
        """Every fan this recuperator switches, the linked unit's included."""
        linked = [f for f in (self.link_exhaust_switch, self.link_intake_switch) if f]
        return [self.exhaust_switch, self.intake_switch, *linked]

    def _is_on(self, entity_id: str) -> bool | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state.state == STATE_ON

    async def _async_apply(self) -> None:
        """Make the fans match the phase. Always switch off before switching on.

        A fan is only switched on once the other fan of its unit reports off, so
        a unit's two fans are never on together, even if a switch is slow or was
        flipped by hand.
        """
        if self._hands_off:
            return
        wanted = self.wanted_fans()
        for entity_id, on in wanted.items():
            if not on and self._is_on(entity_id) is not False:
                await self._async_command(entity_id, False)
        for entity_id, on in wanted.items():
            if on:
                await self._async_switch_on(entity_id)

    async def _async_switch_on(self, entity_id: str) -> None:
        """Switch one fan on, unless its partner still reports on (interlock)."""
        partner = self._partner(entity_id)
        if partner is not None and self._is_on(partner):
            return  # wait for the other fan to report off
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
        """Every fan off (the linked unit's too)."""
        for entity_id in self.fans():
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
            "passive": self.logic.phase == PHASE_INTAKE and self.logic.passive,
            "linked_unit": self.link_type,
            "linked_phase": self.linked_state(),
        }

    def linked_state(self) -> str:
        """What the linked unit is doing: exhaust, intake, idle or not linked."""
        if self.link_type == LINK_NONE:
            return LINKED_NOT_LINKED
        wanted = self.wanted_fans()
        if self.link_intake_switch and wanted.get(self.link_intake_switch):
            return LINKED_INTAKE
        if self.link_exhaust_switch and wanted.get(self.link_exhaust_switch):
            return LINKED_EXHAUST
        return LINKED_IDLE


def linked_fans(data) -> tuple[str | None, str | None]:
    """(exhaust fan, intake fan) of the linked unit, as its type uses them."""
    link_type = data.get(CONF_LINK_TYPE, LINK_NONE)
    exhaust = data.get(CONF_LINK_EXHAUST_SWITCH) if link_type in (LINK_RECUPERATOR, LINK_EXHAUST_FAN) else None
    intake = data.get(CONF_LINK_INTAKE_SWITCH) if link_type in (LINK_RECUPERATOR, LINK_INTAKE_FAN) else None
    return exhaust or None, intake or None


def probe_celsius(state: State | None) -> float | None:
    """A probe state as °C, whatever unit the probe reports in.

    The cycle works in °C throughout. A probe without a unit, or with one that is
    not a temperature unit, is taken to be in °C.
    """
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit in (None, UnitOfTemperature.CELSIUS):
        return value
    try:
        return TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
    except (HomeAssistantError, ValueError):
        return value
