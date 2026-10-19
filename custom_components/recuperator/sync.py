"""Two full recuperators breathing as a pair, kept free of Home Assistant so it is easy to test.

Each unit keeps its own BreathingLogic, probes and settings, but the two never
breathe the same way: while one exhausts, the other takes air in (or rests), so
the house stays balanced. The sync rule decides when the pair switches:

* either: as soon as one unit's phase is done (recovered, settled, timed, ...).
* both: once both are done. The unit that finished first keeps its fan running
  meanwhile, so air keeps moving both ways.
* average: once the two units' progress towards their Recovery targets,
  averaged, reaches the target (both past their Minimum phase).
* lead_this / lead_partner: only the lead unit's phase decides (while the lead
  rests, the other unit's own rules end the phase).

A unit's Maximum phase switches the pair at once under every rule except a
lead rule.

A phase that ends early never ends the other unit's phase, whatever ended it:
room protection, a cold limit, the phase limit or drying. That unit rests
instead: it pauses with both fans off, while the other unit finishes its phase
by its own rules. Cutting the other unit short would hand it a shorter phase: a
shorter exhaust stores less heat, so its next intake runs the core out of heat
sooner, and a limit would measure against the shorter phase. Either way the
breaths would shrink one after another. Drying is decided for the pair: either
unit's lean applies to both, except to a unit whose own drying stood down to
protect its room.

The two units leave the pause together, once both of their pauses have run out
(a resting unit's pause started earlier).
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
    DRYING_OUTDOOR_HUMID,
    DRYING_ROOM_COLD,
    PHASE_EXHAUST,
    PHASE_INTAKE,
    PHASE_PAUSE,
    REASON_COLD_LIMIT,
    REASON_DRYING,
    REASON_MAX_TIME,
    REASON_PHASE_LIMIT,
    REASON_SUPPLY_COLD,
    REASON_SUPPLY_DROP,
    REASON_SYNCED,
    SYNC_AVERAGE,
    SYNC_BOTH,
    SYNC_LEAD_PARTNER,
    SYNC_LEAD_THIS,
)
from .logic import BreathingLogic, Settings

SAFETY = (REASON_SUPPLY_DROP, REASON_SUPPLY_COLD, REASON_COLD_LIMIT)
LIMITS = (REASON_PHASE_LIMIT, REASON_DRYING)
# A unit whose phase ends early for one of these rests until the other unit is done.
EARLY = (*SAFETY, *LIMITS)
HARD = (REASON_MAX_TIME,)
_ACTIVE = (PHASE_EXHAUST, PHASE_INTAKE)


@dataclass
class Unit:
    """One recuperator of the pair, as it stands this tick."""

    logic: BreathingLogic
    mode: str
    settings: Settings
    inside: float | None
    outside: float | None


def mirror(rule: str) -> str:
    """The same rule from the other unit's point of view."""
    return {SYNC_LEAD_THIS: SYNC_LEAD_PARTNER, SYNC_LEAD_PARTNER: SYNC_LEAD_THIS}.get(rule, rule)


def opposite(phase: str | None) -> str:
    return PHASE_INTAKE if phase == PHASE_EXHAUST else PHASE_EXHAUST


def aligned(a: BreathingLogic, b: BreathingLogic) -> bool:
    """Are the two breathing opposite ways (or pausing before opposite phases)?

    A unit that rests while the other finishes is paused before the phase the
    other one is running now, so that after the switch they go opposite ways.
    An ordinary pause (a unit that was breathing alone) never counts as resting.
    """
    if a.phase in _ACTIVE and b.phase in _ACTIVE:
        return b.phase == opposite(a.phase)
    if a.phase == PHASE_PAUSE and b.phase == PHASE_PAUSE:
        return a.next_phase is not None and b.next_phase == opposite(a.next_phase)
    if a.phase == PHASE_PAUSE and b.phase in _ACTIVE:
        return a.resting and a.next_phase == b.phase
    if b.phase == PHASE_PAUSE and a.phase in _ACTIVE:
        return b.resting and b.next_phase == a.phase
    return False


def resting(logic: BreathingLogic, other: BreathingLogic) -> bool:
    """Is this unit resting (paused, fans off) while the other finishes its phase?"""
    return logic.phase == PHASE_PAUSE and other.phase in _ACTIVE and aligned(logic, other)


def _lean(this: BreathingLogic, other: BreathingLogic) -> float | None:
    """The other unit's drying lean for this unit's intakes, unless this unit's own drying stood down to protect its room."""
    if this.drying.status in (DRYING_ROOM_COLD, DRYING_OUTDOOR_HUMID):
        return None
    return other.drying.intake_share


def step_pair(now: float, a: Unit, b: Unit, rule: str) -> bool:
    """Advance both units by one tick; `rule` is from a's point of view. True if either changed."""
    la, lb = a.logic, b.logic
    # One drying decision for the pair: each unit's intakes follow the other's lean too.
    la.partner_intake_share = _lean(la, lb)
    lb.partner_intake_share = _lean(lb, la)
    if not aligned(la, lb):
        # Pairing up (just synced, or one unit just started): both pause, then
        # go opposite ways, with a's next phase first.
        nxt = opposite(la.phase) if la.phase in _ACTIVE else la.next_phase or PHASE_EXHAUST
        la.switch(now, REASON_SYNCED, a.inside, a.outside, nxt)
        lb.switch(now, REASON_SYNCED, b.inside, b.outside, opposite(nxt))
        return True

    if la.phase == PHASE_PAUSE and lb.phase == PHASE_PAUSE:
        return _leave_pause(now, a, b)

    if PHASE_PAUSE in (la.phase, lb.phase):
        # One rests: the other's phase ends by its own rules, whatever the sync rule.
        active = b if la.phase == PHASE_PAUSE else a
        reason = active.logic.pending_reason(now, active.mode, active.settings, active.inside, active.outside)
        if reason is None:
            return False
        _switch(now, a, b, {id(active): reason})
        return True

    ra = la.pending_reason(now, a.mode, a.settings, a.inside, a.outside)
    rb = lb.pending_reason(now, b.mode, b.settings, b.inside, b.outside)
    if _should_switch(now, rule, a, b, ra, rb):
        lead = {SYNC_LEAD_THIS: a, SYNC_LEAD_PARTNER: b}.get(rule)
        reasons = {}
        for unit, reason in ((a, ra), (b, rb)):
            # A reason that did not count (a follower's, under a lead rule) is not
            # why this unit switched: the pair did. One that ended its phase early did.
            if lead is not None and unit is not lead and reason not in EARLY:
                reason = None
            reasons[id(unit)] = reason
        _switch(now, a, b, reasons)
        return True

    # Not switching: a unit whose phase ended early rests.
    rested = False
    for unit, reason, other in ((a, ra, lb), (b, rb, la)):
        if reason not in EARLY:
            continue
        if unit.logic.phase == PHASE_EXHAUST and other.phase == PHASE_INTAKE and other.passive:
            # The other unit's intake fan is off: this exhaust is what pulls its air in.
            _switch(now, a, b, {id(unit): reason})
            return True
        unit.logic.switch(now, reason, unit.inside, unit.outside, opposite(unit.logic.phase))
        unit.logic.resting = True
        rested = True
    if rested:
        _leave_pause(now, a, b)  # both rested at once: on together when both pauses are zero
    return rested


def _switch(now: float, a: Unit, b: Unit, reasons: dict[int, str | None]) -> None:
    """Switch the pair: each running unit ends its phase, and a resting one keeps its next phase."""
    for unit in (a, b):
        logic = unit.logic
        if logic.phase in _ACTIVE:
            reason = reasons.get(id(unit)) or REASON_SYNCED
            logic.switch(now, reason, unit.inside, unit.outside, opposite(logic.phase))
    _leave_pause(now, a, b)  # at once, when both pauses are zero


def _leave_pause(now: float, a: Unit, b: Unit) -> bool:
    """Both start their next phase together, once both pauses have run out."""
    if not (a.logic.pause_over(now, a.settings) and b.logic.pause_over(now, b.settings)):
        return False
    for unit in (a, b):
        unit.logic.begin_next(now, unit.mode, unit.settings, unit.inside, unit.outside)
    return True


def _should_switch(now: float, rule: str, a: Unit, b: Unit, ra: str | None, rb: str | None) -> bool:
    """Does the sync rule switch the pair, given why each unit's phase would end (None: not yet)?"""
    if rule == SYNC_LEAD_THIS:
        return ra is not None and ra not in EARLY
    if rule == SYNC_LEAD_PARTNER:
        return rb is not None and rb not in EARLY
    if ra in HARD or rb in HARD:
        return True
    if rule == SYNC_BOTH:
        return ra is not None and rb is not None
    if rule == SYNC_AVERAGE:
        # A unit whose phase only ended early is not done: it rests, and the other finishes.
        da = _average_done(now, a, None if ra in EARLY else ra)
        db = _average_done(now, b, None if rb in EARLY else rb)
        return da + db >= 2 or (ra in EARLY and rb in EARLY)
    # SYNC_EITHER, and anything unknown: a unit whose phase only ended early rests instead.
    if ra in EARLY and rb in EARLY:
        return True
    return (ra is not None and ra not in EARLY) or (rb is not None and rb not in EARLY)


def _average_done(now: float, unit: Unit, reason: str | None) -> float:
    """A unit's progress for the Average rule: 0 before its Minimum phase, at least 1 once it is done."""
    logic = unit.logic
    started = now if logic.phase_started is None else logic.phase_started
    if now - started < unit.settings.min_phase_seconds:
        return float("-inf")  # too early: the pair cannot switch yet
    progress = logic.progress(now, unit.settings, unit.inside, unit.outside)
    return max(progress, 1.0) if reason is not None else progress

