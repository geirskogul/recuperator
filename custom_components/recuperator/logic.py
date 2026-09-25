"""The breathing cycle itself, kept free of Home Assistant so it is easy to test.

The two probes sit at the two ends of the recuperator's ceramic core:

* During **exhaust** (basement air blowing out), the *inside* probe reads basement
  air and the *outside* probe shows how far the outer end of the core has warmed
  (or cooled) towards it.
* During **intake** (outdoor air blowing in), the *outside* probe reads outdoor
  air and the *inside* probe shows how far the inner end of the core has moved
  towards it.

So in each phase one probe is the *reference* (the air being blown through) and
the other is the *far* probe (the end of the core that is catching up). A phase
ends when the far probe has recovered enough of the gap, has settled, or a time
limit is reached. A short pause with both fans off always separates phases.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .const import (
    DEFAULTS,
    MODE_EXHAUST_ONLY,
    MODE_INTAKE_ONLY,
    MODE_TIMED,
    PHASE_EXHAUST,
    PHASE_INTAKE,
    PHASE_PAUSE,
    PHASE_STOPPED,
    REASON_COLD_LIMIT,
    REASON_MAX_TIME,
    REASON_RECOVERED,
    REASON_SETTLED,
    REASON_STARTED,
    REASON_STOPPED,
    REASON_SUPPLY_COLD,
    REASON_TIMED,
    TIMED_BY_MODE,
    TIMED_NOT,
    TIMED_SENSOR,
    TIMED_SIMILAR,
)

INSIDE = "inside"
OUTSIDE = "outside"
_HISTORY_SECONDS = 900  # how much probe history to keep for the "settled" test
_MIN_GAP = 0.1  # below this gap (°C) the recovered fraction is meaningless


@dataclass
class Settings:
    """The tunable settings, as numbers (see const.SETTINGS for ranges)."""

    recovery_percent: float = DEFAULTS["recovery_percent"]
    settle_seconds: float = DEFAULTS["settle_seconds"]
    settle_delta: float = DEFAULTS["settle_delta"]
    min_phase_seconds: float = DEFAULTS["min_phase_seconds"]
    max_phase_seconds: float = DEFAULTS["max_phase_seconds"]
    pause_seconds: float = DEFAULTS["pause_seconds"]
    timed_phase_seconds: float = DEFAULTS["timed_phase_seconds"]
    similar_band: float = DEFAULTS["similar_band"]
    cold_threshold: float = DEFAULTS["cold_threshold"]
    cold_intake_max_seconds: float = DEFAULTS["cold_intake_max_seconds"]
    cold_exhaust_extra_seconds: float = DEFAULTS["cold_exhaust_extra_seconds"]
    min_supply_temperature: float = DEFAULTS["min_supply_temperature"]

    @classmethod
    def from_mapping(cls, values: dict) -> Settings:
        """Build from a dict of stored options, filling gaps with defaults."""
        return cls(**{k: float(values.get(k, v)) for k, v in DEFAULTS.items()})


class BreathingLogic:
    """State machine: exhaust, pause, intake, pause, exhaust, ..."""

    def __init__(self) -> None:
        """Start stopped, with no history."""
        self.phase: str = PHASE_STOPPED
        self.next_phase: str | None = None
        self.phase_started: float | None = None
        self.timed_reason: str = TIMED_NOT
        self.last_reason: str | None = None
        self.last_exhaust_seconds: float | None = None
        self.last_intake_seconds: float | None = None
        self.outdoor_estimate: float | None = None
        self.basement_estimate: float | None = None
        self.last_recovery_percent: float | None = None
        self.cold: bool = False
        self._far_start: float | None = None
        self._history: dict[str, deque[tuple[float, float | None]]] = {
            INSIDE: deque(),
            OUTSIDE: deque(),
        }

    # -- probe history ---------------------------------------------------------

    def record(self, probe: str, now: float, value: float | None) -> None:
        """Remember a probe reading (None = unavailable) for the settled test."""
        hist = self._history[probe]
        hist.append((now, value))
        while len(hist) > 2 and hist[1][0] < now - _HISTORY_SECONDS:
            hist.popleft()

    def _settled(self, probe: str, now: float, s: Settings) -> bool:
        """True if the probe moved less than settle_delta over the last settle_seconds.

        A probe that sends nothing (for example an ESPHome sensor with a delta
        filter, while steady) counts as unchanged, which is exactly right.
        """
        if self.phase_started is None or now - self.phase_started < s.settle_seconds:
            return False
        start = now - s.settle_seconds
        values: list[float] = []
        before: float | None = None
        for t, v in self._history[probe]:
            if t <= start:
                before = v
            elif v is not None:
                values.append(v)
        if before is not None:
            values.append(before)
        if not values:
            return False
        return max(values) - min(values) < s.settle_delta

    # -- starting and stopping --------------------------------------------------

    def start(self, now: float, mode: str, s: Settings, inside, outside) -> None:
        """Begin breathing: intake-only mode starts with intake, otherwise exhaust."""
        first = PHASE_INTAKE if mode == MODE_INTAKE_ONLY else PHASE_EXHAUST
        self.last_reason = REASON_STARTED
        self._begin(first, now, mode, s, inside, outside)

    def stop(self) -> None:
        """Stop breathing: both fans off."""
        self.phase = PHASE_STOPPED
        self.next_phase = None
        self.phase_started = None
        self.timed_reason = TIMED_NOT
        self.last_reason = REASON_STOPPED

    def _begin(self, phase: str, now: float, mode: str, s: Settings, inside, outside) -> None:
        """Start an exhaust or intake phase and decide whether it is timed."""
        self.phase = phase
        self.next_phase = None
        self.phase_started = now
        ref, far = self._ref_far(phase, inside, outside)
        if mode == MODE_TIMED:
            self.timed_reason = TIMED_BY_MODE
        elif ref is None or far is None:
            self.timed_reason = TIMED_SENSOR
        elif self._air_difference(inside, outside) < s.similar_band:
            self.timed_reason = TIMED_SIMILAR
        else:
            self.timed_reason = TIMED_NOT
        self._far_start = far

    def _air_difference(self, inside: float, outside: float) -> float:
        """How different basement air and outdoor air are.

        At the start of a phase each probe still reads its own end of the core,
        which after a good phase is close to the other side's air. So once both
        have been measured, use the basement temperature (inside probe at the end
        of the last exhaust) and the outdoor temperature (outside probe at the end
        of the last intake). Only the very first phases fall back to the live probes.
        """
        if self.basement_estimate is not None and self.outdoor_estimate is not None:
            return abs(self.basement_estimate - self.outdoor_estimate)
        return abs(inside - outside)

    @staticmethod
    def _ref_far(phase: str, inside, outside):
        """(reference, far) probe values for a phase."""
        return (inside, outside) if phase == PHASE_EXHAUST else (outside, inside)

    # -- the main step ------------------------------------------------------------

    def step(self, now: float, mode: str, s: Settings, inside, outside) -> bool:
        """Advance the cycle. Returns True if the phase changed."""
        if self.phase == PHASE_STOPPED:
            return False

        # Continuous modes: one fan runs all the time.
        if mode in (MODE_EXHAUST_ONLY, MODE_INTAKE_ONLY):
            wanted = PHASE_EXHAUST if mode == MODE_EXHAUST_ONLY else PHASE_INTAKE
            if self.phase in (wanted, PHASE_PAUSE) and (
                self.phase == wanted or self.next_phase == wanted
            ):
                return self._maybe_leave_pause(now, mode, s, inside, outside)
            self._end(now, REASON_STARTED, inside, outside, next_phase=wanted)
            return True

        if self.phase == PHASE_PAUSE:
            return self._maybe_leave_pause(now, mode, s, inside, outside)

        reason = self._end_reason(now, mode, s, inside, outside)
        if reason is None:
            return False
        nxt = PHASE_INTAKE if self.phase == PHASE_EXHAUST else PHASE_EXHAUST
        self._end(now, reason, inside, outside, next_phase=nxt)
        if s.pause_seconds <= 0:
            self._maybe_leave_pause(now, mode, s, inside, outside)
        return True

    def _maybe_leave_pause(self, now, mode, s, inside, outside) -> bool:
        """Leave the pause once it has lasted pause_seconds."""
        if self.phase != PHASE_PAUSE:
            return False
        if now - (now if self.phase_started is None else self.phase_started) < s.pause_seconds:
            return False
        self._begin(self.next_phase or PHASE_EXHAUST, now, mode, s, inside, outside)
        return True

    def _end(self, now, reason, inside, outside, next_phase) -> None:
        """Finish the current phase, remember what it achieved, and pause."""
        if self.phase in (PHASE_EXHAUST, PHASE_INTAKE) and self.phase_started is not None:
            duration = now - self.phase_started
            if self.phase == PHASE_EXHAUST:
                self.last_exhaust_seconds = duration
                if inside is not None:
                    self.basement_estimate = inside
            else:
                self.last_intake_seconds = duration
                if outside is not None:
                    self.outdoor_estimate = outside
            ref, far = self._ref_far(self.phase, inside, outside)
            if self.timed_reason == TIMED_NOT and ref is not None and far is not None:
                self.last_recovery_percent = self._recovered(ref, far) * 100
        self.last_reason = reason
        self.phase = PHASE_PAUSE
        self.next_phase = next_phase
        self.phase_started = now

    def _recovered(self, ref: float, far: float) -> float:
        """How much of the gap the far probe has closed (0..1, can overshoot).

        The gap runs from where the far probe started to the temperature of the
        air being blown through. The reference probe needs 10-30 s to register
        that air after a switch, so while it is still catching up, the last
        measured basement or outdoor temperature is used instead, whichever is
        further from the far probe's start.
        """
        if self._far_start is None:
            return 0.0
        source = ref
        known = self.basement_estimate if self.phase == PHASE_EXHAUST else self.outdoor_estimate
        if known is not None and abs(known - self._far_start) > abs(ref - self._far_start):
            source = known
        gap = source - self._far_start
        if abs(gap) < _MIN_GAP:
            return 1.0
        return (far - self._far_start) / gap

    def _is_cold(self, s: Settings, outside) -> bool:
        """Is it cold outdoors? During intake the outside probe reads outdoor air."""
        if self.phase == PHASE_INTAKE and outside is not None:
            return outside <= s.cold_threshold
        if self.outdoor_estimate is not None:
            return self.outdoor_estimate <= s.cold_threshold
        return False

    def _end_reason(self, now, mode, s: Settings, inside, outside) -> str | None:
        """Decide whether the running exhaust or intake phase should end now."""
        elapsed = now - (now if self.phase_started is None else self.phase_started)
        ref, far = self._ref_far(self.phase, inside, outside)
        self.cold = self._is_cold(s, outside)

        # Room protection: during intake the inside probe reads the air entering the
        # room. If it gets colder than allowed, end the intake now (after the
        # minimum phase), whatever else is going on. Applies in every mode.
        if (
            self.phase == PHASE_INTAKE
            and inside is not None
            and inside < s.min_supply_temperature
            and elapsed >= min(s.min_phase_seconds, s.cold_intake_max_seconds)
        ):
            return REASON_SUPPLY_COLD

        # A probe that drops out mid-phase turns the rest of the phase into a timed one.
        if self.timed_reason == TIMED_NOT and (ref is None or far is None):
            self.timed_reason = TIMED_SENSOR
        if self.timed_reason != TIMED_NOT:
            if self.cold and self.phase == PHASE_INTAKE and elapsed >= s.cold_intake_max_seconds:
                return REASON_COLD_LIMIT  # even timed intake respects the cold limit
            return REASON_TIMED if elapsed >= s.timed_phase_seconds else None

        lower = s.min_phase_seconds
        cap = max(s.max_phase_seconds, lower)
        cap_reason = REASON_MAX_TIME
        if self.cold and self.phase == PHASE_INTAKE and s.cold_intake_max_seconds < cap:
            cap, cap_reason = s.cold_intake_max_seconds, REASON_COLD_LIMIT
            lower = min(lower, cap)
        elif self.cold and self.phase == PHASE_EXHAUST and self.last_intake_seconds:
            # In the cold, exhaust at least as long as the last intake (keeps the
            # core's outer end from freezing and the basement from cooling), but
            # no more than cold_exhaust_extra_seconds longer.
            cold_cap = self.last_intake_seconds + s.cold_exhaust_extra_seconds
            if cold_cap < cap:
                cap, cap_reason = cold_cap, REASON_COLD_LIMIT
            lower = min(max(lower, self.last_intake_seconds), cap)

        if elapsed >= cap:
            return cap_reason
        if elapsed < lower:
            return None
        recovered = self._recovered(ref, far) * 100
        if recovered >= s.recovery_percent:
            return REASON_RECOVERED
        # Settled: the far probe has made real progress and then stopped. A far
        # probe that has not moved yet means the core is still doing its job
        # (the air leaving it is still close to the far side's temperature), so
        # "settled" only counts after at least half the recovery target. Both
        # probes must be steady, so a reference probe still catching up with the
        # new airflow cannot make the phase look finished either.
        if (
            recovered >= s.recovery_percent / 2
            and self._settled(INSIDE, now, s)
            and self._settled(OUTSIDE, now, s)
        ):
            return REASON_SETTLED
        return None
