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
from dataclasses import dataclass, fields

from .ambient import Ambient
from .const import (
    DEFAULTS,
    EFFICIENCY_BREATHS,
    EFFICIENCY_MIN_GAP,
    FROST_FACE_TEMPERATURE,
    LEGACY_TIMED_PHASE,
    LIMIT_EXHAUST,
    LIMIT_INTAKE,
    LIMIT_OFF,
    MODE_AUTOMATIC,
    MODE_EXHAUST_ONLY,
    MODE_INTAKE_ONLY,
    MODE_TIMED,
    PHASE_EXHAUST,
    PHASE_INTAKE,
    PHASE_PAUSE,
    PHASE_STOPPED,
    REASON_COLD_LIMIT,
    REASON_DRYING,
    REASON_MAX_TIME,
    REASON_PHASE_LIMIT,
    REASON_RECOVERED,
    REASON_SETTLED,
    REASON_STARTED,
    REASON_STOPPED,
    REASON_SUPPLY_COLD,
    REASON_SUPPLY_DROP,
    REASON_TIMED,
    TIMED_BY_MODE,
    TIMED_NOT,
    TIMED_SENSOR,
    TIMED_SIMILAR,
)
from .drying import OFF as DRYING_IDLE, Drying, decide as decide_drying

INSIDE = "inside"
OUTSIDE = "outside"
# An intake that ended for one of these ran the core out of heat (the air entering
# the room got too cold). Its length says how much heat the exhaust before it
# stored, not how much air came in, so Limited exhaust must not cut the next
# exhaust to it (see BreathingLogic._limit_share).
DRAINED_INTAKE = (REASON_SUPPLY_DROP, REASON_SUPPLY_COLD, REASON_COLD_LIMIT)
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
    timed_exhaust_seconds: float = DEFAULTS["timed_exhaust_seconds"]
    timed_intake_seconds: float = DEFAULTS["timed_intake_seconds"]
    similar_band: float = DEFAULTS["similar_band"]
    cold_threshold: float = DEFAULTS["cold_threshold"]
    cold_intake_max_seconds: float = DEFAULTS["cold_intake_max_seconds"]
    cold_exhaust_extra_seconds: float = DEFAULTS["cold_exhaust_extra_seconds"]
    max_supply_drop: float = DEFAULTS["max_supply_drop"]
    min_supply_temperature: float = DEFAULTS["min_supply_temperature"]
    passive_intake_max_seconds: float = DEFAULTS["passive_intake_max_seconds"]
    passive_flow_delta: float = DEFAULTS["passive_flow_delta"]
    phase_limit_percent: float = DEFAULTS["phase_limit_percent"]
    target_humidity: float = DEFAULTS["target_humidity"]
    drying_band: float = DEFAULTS["drying_band"]
    drying_min_intake_share: float = DEFAULTS["drying_min_intake_share"]
    drying_exhaust_only_humidity: float = DEFAULTS["drying_exhaust_only_humidity"]
    drying_min_room_temperature: float = DEFAULTS["drying_min_room_temperature"]
    passive_intake: bool = False
    phase_limit: str = LIMIT_OFF
    drying: bool = False
    drying_exhaust_only: bool = False

    @classmethod
    def from_mapping(cls, values: dict) -> Settings:
        """Build from a dict of stored options, filling gaps with defaults."""
        own = {f.name for f in fields(cls)}
        values = dict(values)
        legacy = values.get(LEGACY_TIMED_PHASE)
        if legacy is not None:  # stored before the two timed lengths existed
            values.setdefault("timed_exhaust_seconds", legacy)
            values.setdefault("timed_intake_seconds", legacy)
        return cls(
            **{k: float(values.get(k, v)) for k, v in DEFAULTS.items() if k in own},
            passive_intake=bool(values.get("passive_intake", False)),
            phase_limit=str(values.get("phase_limit", LIMIT_OFF)),
            drying=bool(values.get("drying", False)),
            drying_exhaust_only=bool(values.get("drying_exhaust_only", False)),
        )

    def timed_seconds(self, phase: str) -> float:
        """The fixed length of a timed exhaust or intake phase."""
        return self.timed_intake_seconds if phase == PHASE_INTAKE else self.timed_exhaust_seconds


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
        self.frost_risk: bool = False
        # Passive intake: the intake phase runs with the intake fan off.
        self.passive: bool = False  # the running (or last) intake is passive
        self.last_intake_passive: bool = False
        self.last_intake_limited: bool = False  # the last intake was cut short by the phase limit
        self.last_intake_drained: bool = False  # the last intake ended because the core ran out of heat
        self.passive_flow: bool | None = None  # inflow seen during the running/last passive intake
        self.passive_flow_after_seconds: float | None = None
        self._intake_inside_start: float | None = None
        self._exhaust_max_outside: float | None = None
        self._far_start: float | None = None
        self._history: dict[str, deque[tuple[float, float | None]]] = {
            INSIDE: deque(),
            OUTSIDE: deque(),
        }
        # The optional room and outdoor sensors (set by the controller each tick).
        self.ambient: Ambient = Ambient()
        self.drying: Drying = DRYING_IDLE
        # Heat recovery: the supply air (inside probe) averaged over each intake.
        self._efficiencies: deque[float] = deque(maxlen=EFFICIENCY_BREATHS)
        self._supply_sum = 0.0
        self._supply_seconds = 0.0
        self._supply_last: tuple[float, float] | None = None

    # -- the room and the outdoors ------------------------------------------------

    def room_air(self) -> float | None:
        """The room's temperature: the room sensor if there is one, else the learned value."""
        if self.ambient.room_temperature is not None:
            return self.ambient.room_temperature
        return self.basement_estimate

    def outdoor_air(self) -> float | None:
        """The outdoor temperature: the outdoor sensor if there is one, else the learned value."""
        if self.ambient.outdoor_temperature is not None:
            return self.ambient.outdoor_temperature
        return self.outdoor_estimate

    def update_drying(self, s: Settings) -> None:
        """Decide what drying wants from the latest readings."""
        self.drying = decide_drying(s, self.ambient, self.room_air(), self.drying.exhaust_only)

    def effective_mode(self, mode: str) -> str:
        """Drying can turn Automatic or Timed breathing into Exhaust only while the room is very humid."""
        if self.drying.exhaust_only and mode in (MODE_AUTOMATIC, MODE_TIMED):
            return MODE_EXHAUST_ONLY
        return mode

    # -- heat recovery -------------------------------------------------------------

    @property
    def heat_recovery(self) -> float | None:
        """How much of the room-outdoor gap the core gave back to incoming air (%), over the last few intakes."""
        if not self._efficiencies:
            return None
        return sum(self._efficiencies) / len(self._efficiencies)

    @property
    def heat_recovery_samples(self) -> list[float]:
        """The intakes the Heat recovery sensor averages (%), oldest first."""
        return list(self._efficiencies)

    def _sample_supply(self, now: float, inside: float | None) -> None:
        """Add the supply air since the last sample to the running intake average (time-weighted)."""
        if self._supply_last is not None:
            then, value = self._supply_last
            self._supply_sum += value * (now - then)
            self._supply_seconds += now - then
        self._supply_last = None if inside is None else (now, inside)

    def _record_efficiency(self, now: float, inside: float | None) -> None:
        """At the end of a powered intake: (supply - outdoor) / (room - outdoor), averaged over the intake."""
        self._sample_supply(now, inside)
        room, outdoor = self.room_air(), self.outdoor_air()
        if self.passive or self._supply_seconds <= 0 or room is None or outdoor is None:
            return
        if abs(room - outdoor) < EFFICIENCY_MIN_GAP:
            return  # too little difference to judge
        supply = self._supply_sum / self._supply_seconds
        efficiency = (supply - outdoor) / (room - outdoor)
        self._efficiencies.append(min(max(efficiency, 0.0), 1.0) * 100)

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
        if phase == PHASE_INTAKE:
            self._intake_inside_start = inside
            self._supply_sum, self._supply_seconds = 0.0, 0.0
            self._supply_last = None if inside is None else (now, inside)
            self.passive = s.passive_intake
            if self.passive:
                self.passive_flow = False
                self.passive_flow_after_seconds = None
        if phase == PHASE_EXHAUST:
            self._exhaust_max_outside = outside

    def _air_difference(self, inside: float, outside: float) -> float:
        """How different basement air and outdoor air are.

        At the start of a phase each probe still reads its own end of the core,
        which after a good phase is close to the other side's air. So once both
        have been measured, use the basement temperature (inside probe at the end
        of the last exhaust) and the outdoor temperature (outside probe at the end
        of the last intake), or the room and outdoor sensors where set up. Only
        the very first phases fall back to the live probes.
        """
        room, outdoor = self.room_air(), self.outdoor_air()
        if room is not None and outdoor is not None:
            return abs(room - outdoor)
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
        chosen, mode = mode, self.effective_mode(mode)
        if self.phase == PHASE_INTAKE:
            self._sample_supply(now, inside)

        # Continuous modes: one fan runs all the time.
        if mode in (MODE_EXHAUST_ONLY, MODE_INTAKE_ONLY):
            wanted = PHASE_EXHAUST if mode == MODE_EXHAUST_ONLY else PHASE_INTAKE
            if self.phase in (wanted, PHASE_PAUSE) and (
                self.phase == wanted or self.next_phase == wanted
            ):
                return self._maybe_leave_pause(now, mode, s, inside, outside)
            reason = REASON_DRYING if mode != chosen else REASON_STARTED
            self._end(now, reason, inside, outside, next_phase=wanted)
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

    # -- driven from outside, by a synced pair (see sync.py) ----------------------

    def pending_reason(self, now: float, mode: str, s: Settings, inside, outside) -> str | None:
        """Why the running exhaust or intake would end now (None: not yet), without ending it."""
        if self.phase == PHASE_INTAKE:
            self._sample_supply(now, inside)
        return self._end_reason(now, mode, s, inside, outside)

    def progress(self, now: float, s: Settings, inside, outside) -> float:
        """How far the running phase is towards ending on its own (1.0 = there).

        The recovered share of the Recovery target, or for a timed phase the
        share of its length that has run.
        """
        elapsed = now - (now if self.phase_started is None else self.phase_started)
        if self.timed_reason != TIMED_NOT:
            passive = self.phase == PHASE_INTAKE and self.passive
            length = s.passive_intake_max_seconds if passive else s.timed_seconds(self.phase)
            return elapsed / length
        ref, far = self._ref_far(self.phase, inside, outside)
        if ref is None or far is None:
            return 0.0
        return self._recovered(ref, far) * 100 / s.recovery_percent

    def switch(self, now: float, reason: str, inside, outside, next_phase: str) -> None:
        """End the running phase (or re-aim a pause) so that `next_phase` comes next."""
        if self.phase == PHASE_PAUSE:
            self.next_phase = next_phase
        else:
            self._end(now, reason, inside, outside, next_phase=next_phase)

    def pause_over(self, now: float, s: Settings) -> bool:
        """Has the running pause lasted pause_seconds?"""
        return self.phase == PHASE_PAUSE and now - (now if self.phase_started is None else self.phase_started) >= s.pause_seconds

    def begin_next(self, now: float, mode: str, s: Settings, inside, outside) -> None:
        """Leave the pause into the phase that is due."""
        self._begin(self.next_phase or PHASE_EXHAUST, now, mode, s, inside, outside)

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
                # Frost risk: in cold weather, if even the warm exhaust never got the
                # core's outdoor face above freezing, condensation there can ice up.
                self.frost_risk = bool(
                    self.cold
                    and self._exhaust_max_outside is not None
                    and self._exhaust_max_outside <= FROST_FACE_TEMPERATURE
                )
            else:
                self.last_intake_seconds = duration
                self.last_intake_passive = self.passive
                self.last_intake_limited = reason in (REASON_PHASE_LIMIT, REASON_DRYING)
                self.last_intake_drained = reason in DRAINED_INTAKE
                if outside is not None:
                    self.outdoor_estimate = outside
                self._record_efficiency(now, inside)
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
        """Is it cold outdoors? An outdoor sensor knows; otherwise, during intake, the outside probe reads outdoor air."""
        if self.ambient.outdoor_temperature is not None:
            return self.ambient.outdoor_temperature <= s.cold_threshold
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

        if self.phase == PHASE_EXHAUST and outside is not None:
            if self._exhaust_max_outside is None or outside > self._exhaust_max_outside:
                self._exhaust_max_outside = outside
        passive = self.phase == PHASE_INTAKE and self.passive
        if passive:
            self._watch_passive_flow(elapsed, s, inside)

        # Room protection: during intake the inside probe reads the air entering the
        # room. After the minimum phase (so fresh air has had time to arrive), end
        # the intake if that air has fallen more than max_supply_drop below room
        # temperature, or below the optional hard floor. Applies in every mode.
        if self.phase == PHASE_INTAKE and inside is not None and elapsed >= s.min_phase_seconds:
            room = self.room_air()
            if room is None:
                room = self._intake_inside_start
            if room is not None and inside < room - s.max_supply_drop:
                return REASON_SUPPLY_DROP
            if inside < s.min_supply_temperature:
                return REASON_SUPPLY_COLD

        # A probe that drops out mid-phase turns the rest of the phase into a timed one.
        if self.timed_reason == TIMED_NOT and (ref is None or far is None):
            self.timed_reason = TIMED_SENSOR
        if self.timed_reason != TIMED_NOT:
            if self.cold and self.phase == PHASE_INTAKE and elapsed >= s.cold_intake_max_seconds:
                return REASON_COLD_LIMIT  # even timed intake respects the cold limit
            limit, limit_reason = self._phase_limit(s, passive)
            if limit is not None and elapsed >= limit:
                return limit_reason
            length = s.passive_intake_max_seconds if passive else s.timed_seconds(self.phase)
            return REASON_TIMED if elapsed >= length else None

        lower = s.min_phase_seconds
        cap = max(s.passive_intake_max_seconds if passive else s.max_phase_seconds, lower)
        cap_reason = REASON_MAX_TIME
        cold_exhaust = bool(
            self.cold and self.phase == PHASE_EXHAUST and self.last_intake_seconds and not self.last_intake_passive
        )
        if self.cold and self.phase == PHASE_INTAKE and s.cold_intake_max_seconds < cap:
            cap, cap_reason = s.cold_intake_max_seconds, REASON_COLD_LIMIT
            lower = min(lower, cap)
        elif cold_exhaust:
            # In the cold, exhaust at least as long as the last intake (keeps the
            # core's outer end from freezing and the basement from cooling), but
            # no more than cold_exhaust_extra_seconds longer. This frost protection
            # wins over a limited exhaust. The "no longer" part is skipped after an
            # intake the phase limit cut short: otherwise each exhaust would be held
            # to a shortened intake, which is then shortened again, and the phases
            # would shrink breath by breath.
            if not self.last_intake_limited:
                cold_cap = self.last_intake_seconds + s.cold_exhaust_extra_seconds
                if cold_cap < cap:
                    cap, cap_reason = cold_cap, REASON_COLD_LIMIT
            lower = min(max(lower, self.last_intake_seconds), cap)
        limit, limit_reason = (None, None) if cold_exhaust else self._phase_limit(s, passive)
        if limit is not None and limit < cap:
            cap, cap_reason = limit, limit_reason

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

    def _limit_share(self, s: Settings, passive: bool) -> tuple[float | None, str | None]:
        """(share of the other phase's last length in %, reason) for the running phase, if limited.

        Limited intake, and drying, keep an intake shorter than the last exhaust
        (the smaller share wins); limited exhaust does the reverse. Passive
        intakes are neither limited nor used as a measure (the intake fan is off).

        Limited exhaust skips the exhaust after an intake that ran the core out of
        heat: that intake was short because the exhaust before it stored little
        heat, so cutting the next exhaust to it would store even less, and the
        breaths would shrink one after another down to the minimum phase.
        """
        shares: list[tuple[float, str]] = []
        if self.phase == PHASE_INTAKE and not passive:
            if s.phase_limit == LIMIT_INTAKE:
                shares.append((s.phase_limit_percent, REASON_PHASE_LIMIT))
            if self.drying.intake_share is not None:
                shares.append((self.drying.intake_share, REASON_DRYING))
        elif self.phase == PHASE_EXHAUST and s.phase_limit == LIMIT_EXHAUST and self.phase_limit_status(s) == "active":
            shares.append((s.phase_limit_percent, REASON_PHASE_LIMIT))
        return min(shares) if shares else (None, None)

    def phase_limit_status(self, s: Settings) -> str:
        """off, active, or paused: Limited exhaust waits out the exhaust after a drained or passive intake."""
        if s.phase_limit == LIMIT_OFF:
            return "off"
        if s.phase_limit == LIMIT_EXHAUST and (self.last_intake_drained or self.last_intake_passive):
            return "paused"
        return "active"

    def _phase_limit(self, s: Settings, passive: bool) -> tuple[float | None, str | None]:
        """(the longest the running phase may last, why) under the phase limit or drying, if either applies.

        Never below the minimum phase, which protects the fans and relays.
        """
        share, reason = self._limit_share(s, passive)
        if share is None:
            return None, None
        other = self.last_exhaust_seconds if self.phase == PHASE_INTAKE else self.last_intake_seconds
        if not other:
            return None, None  # nothing to compare with yet
        return max(other * share / 100, s.min_phase_seconds), reason

    def _watch_passive_flow(self, elapsed: float, s: Settings, inside) -> None:
        """During a passive intake, notice air actually flowing in.

        With the intake fan off, air that drifts in through the core cools (or
        warms) the air at the room end towards the outdoor temperature. Once the
        inside probe has moved passive_flow_delta that way, passive inflow is seen.
        Needs a known outdoor temperature that differs from the room.
        """
        if self.passive_flow or inside is None or self._intake_inside_start is None:
            return
        outdoor = self.outdoor_air()
        if outdoor is None:
            return
        direction = outdoor - self._intake_inside_start
        if abs(direction) < s.passive_flow_delta:
            return  # indoor and outdoor too alike to tell
        moved = (inside - self._intake_inside_start) * (1 if direction > 0 else -1)
        if moved >= s.passive_flow_delta:
            self.passive_flow = True
            self.passive_flow_after_seconds = elapsed
