"""Two full recuperators breathing as a pair, kept free of Home Assistant so it is easy to test.

Each unit keeps its own BreathingLogic, probes and settings, but the two always
breathe opposite ways: while one exhausts, the other takes air in, so the house
stays balanced. The sync rule decides when the pair switches:

* either: as soon as one unit's phase is done (recovered, settled, timed, ...).
* both: once both are done. The unit that finished first keeps its fan running
  meanwhile, so air keeps moving both ways.
* average: once the two units' progress towards their Recovery targets,
  averaged, reaches the target (both past their Minimum phase).
* lead_this / lead_partner: only the lead unit's phase decides.

A unit's hard limits switch the pair at once under every rule except a lead
rule: room protection, the cold limits, its Maximum phase, the phase limit and
drying. Under a lead rule the other unit still switches the pair for its
safety stops (room protection and the cold limits), but nothing else.

The two units pause together and leave the pause together, once both of their
pauses have run out.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
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
HARD = (*SAFETY, REASON_MAX_TIME, REASON_PHASE_LIMIT, REASON_DRYING)
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
    """Are the two breathing opposite ways (or pausing before opposite phases)?"""
    if a.phase in _ACTIVE and b.phase in _ACTIVE:
        return b.phase == opposite(a.phase)
    if a.phase == PHASE_PAUSE and b.phase == PHASE_PAUSE:
        return a.next_phase is not None and b.next_phase == opposite(a.next_phase)
    return False


def step_pair(now: float, a: Unit, b: Unit, rule: str) -> bool:
    """Advance both units by one tick; `rule` is from a's point of view. True if either changed."""
    la, lb = a.logic, b.logic
    if not aligned(la, lb):
        # Pairing up (just synced, or one unit just started): both pause, then
        # go opposite ways, with a's next phase first.
        nxt = opposite(la.phase) if la.phase in _ACTIVE else la.next_phase or PHASE_EXHAUST
        la.switch(now, REASON_SYNCED, a.inside, a.outside, nxt)
        lb.switch(now, REASON_SYNCED, b.inside, b.outside, opposite(nxt))
        return True

    if la.phase == PHASE_PAUSE:
        return _leave_pause(now, a, b)

    ra = la.pending_reason(now, a.mode, a.settings, a.inside, a.outside)
    rb = lb.pending_reason(now, b.mode, b.settings, b.inside, b.outside)
    if not _should_switch(now, rule, a, b, ra, rb):
        return False
    lead = {SYNC_LEAD_THIS: a, SYNC_LEAD_PARTNER: b}.get(rule)
    for unit, reason in ((a, ra), (b, rb)):
        # A reason that did not count (a follower's, under a lead rule) is not
        # why this unit switched: the pair did.
        if lead is not None and unit is not lead and reason not in SAFETY:
            reason = None
        unit.logic.switch(now, reason or REASON_SYNCED, unit.inside, unit.outside, opposite(unit.logic.phase))
    _leave_pause(now, a, b)  # at once, when both pauses are zero
    return True


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
        return ra is not None or rb in SAFETY
    if rule == SYNC_LEAD_PARTNER:
        return rb is not None or ra in SAFETY
    if ra in HARD or rb in HARD:
        return True
    if rule == SYNC_BOTH:
        return ra is not None and rb is not None
    if rule == SYNC_AVERAGE:
        return _average_done(now, a, ra) + _average_done(now, b, rb) >= 2
    return ra is not None or rb is not None  # SYNC_EITHER, and anything unknown


def _average_done(now: float, unit: Unit, reason: str | None) -> float:
    """A unit's progress for the Average rule: 0 before its Minimum phase, at least 1 once it is done."""
    logic = unit.logic
    started = now if logic.phase_started is None else logic.phase_started
    if now - started < unit.settings.min_phase_seconds:
        return float("-inf")  # too early: the pair cannot switch yet
    progress = logic.progress(now, unit.settings, unit.inside, unit.outside)
    return max(progress, 1.0) if reason is not None else progress

