"""Synced recuperators breathing as a group, kept free of Home Assistant so it is easy to test.

A group has two sides that breathe opposite ways: while one side exhausts, the
other takes air in (or rests), so the house stays balanced. A pair is the
smallest group, one unit a side; one big unit against two small ones, or two
against two, are larger ones. Each unit keeps its own BreathingLogic, probes
and settings. The sync rule decides when the group switches:

* either: as soon as one unit's phase is done (recovered, settled, timed, ...).
* both: once every unit is done. A unit that finished first keeps its fan
  running meanwhile, so air keeps moving both ways.
* average: once the units' progress towards their Recovery targets, averaged,
  reaches the target (all past their Minimum phase).
* lead_this / lead_partner (pairs only): only the lead unit's phase decides
  (while the lead rests, the other unit's own rules end the phase).

A unit's Maximum phase switches the group at once under every rule except a
lead rule.

A phase that ends early never ends another unit's phase, whatever ended it:
room protection, a cold limit, the phase limit or drying. That unit rests
instead: it pauses with both fans off, while the others finish their phases.
Cutting another unit short would hand it a shorter phase: a shorter exhaust
stores less heat, so its next intake runs the core out of heat sooner, and a
limit would measure against the shorter phase. Either way the breaths would
shrink one after another. While a whole side rests, the sync rule decides
among the units still running (a lead rule falls back to either); a unit left
running alone ends by its own rules. Drying is decided for the group: the
strongest lean among the other units applies to each unit's intakes, except
to a unit whose own drying stood down to protect its room.

The units leave the pause together, once all of their pauses have run out
(a resting unit's pause started earlier).

CO2 vent mode (a unit's own setting) overlaps the exhausts, so one unit is
always exhausting: when the group switches, a unit in it whose exhaust is
ending keeps exhausting for its overlap while the other side starts its
exhaust. Then it pauses and starts its intake on its own, late. That intake
holds the group for at least its Minimum phase (the relays' floor), and does
not count as a measure for the limits. While any unit overlaps, the group
does not switch. Drying in CO2 vent mode lengthens the overlap instead of
resting the units taking air in (see BreathingLogic.co2_vent_overlap).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .const import (
    CONF_LINK_ENTRIES,
    CONF_LINK_ENTRY,
    CONF_LINK_TYPE,
    DRYING_OUTDOOR_HUMID,
    DRYING_ROOM_COLD,
    LINK_SYNCED,
    PHASE_EXHAUST,
    PHASE_INTAKE,
    PHASE_PAUSE,
    REASON_CO2_VENT,
    REASON_COLD_LIMIT,
    REASON_DRYING,
    REASON_MAX_TIME,
    REASON_PHASE_LIMIT,
    REASON_SUPPLY_COLD,
    REASON_SUPPLY_DROP,
    REASON_SYNCED,
    SYNC_AVERAGE,
    SYNC_BOTH,
    SYNC_EITHER,
    SYNC_LEAD_PARTNER,
    SYNC_LEAD_THIS,
)
from .logic import BreathingLogic, Settings

SAFETY = (REASON_SUPPLY_DROP, REASON_SUPPLY_COLD, REASON_COLD_LIMIT)
LIMITS = (REASON_PHASE_LIMIT, REASON_DRYING)
# A unit whose phase ends early for one of these rests until the others are done.
EARLY = (*SAFETY, *LIMITS)
HARD = (REASON_MAX_TIME,)
LEAD_RULES = (SYNC_LEAD_THIS, SYNC_LEAD_PARTNER)
_ACTIVE = (PHASE_EXHAUST, PHASE_INTAKE)


@dataclass
class Unit:
    """One recuperator of the group, as it stands this tick."""

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


# -- who breathes with whom ----------------------------------------------------------


def link_entries_of(data: Mapping) -> list[str]:
    """The synced recuperators an entry breathes opposite to (its group's other side).

    link_entry, the first of them, is what 0.5 and earlier read. A list whose
    first entry no longer matches it was left behind by an older version that
    re-linked the entry, so the single link wins.
    """
    if data.get(CONF_LINK_TYPE) != LINK_SYNCED or not data.get(CONF_LINK_ENTRY):
        return []
    first = data[CONF_LINK_ENTRY]
    others = list(data.get(CONF_LINK_ENTRIES) or [])
    return others if others and others[0] == first else [first]


def sides_of(me: str, links: Mapping[str, Sequence[str]]) -> tuple[list[str], list[str]] | None:
    """(this unit's side, the other side) of its group, both sorted; None if it has none.

    Every unit of a side must list exactly the other side, and the other side's
    units exactly this side; otherwise the stored links disagree (half saved,
    or edited by hand) and this unit breathes on its own.
    """
    other = sorted(set(links.get(me) or ()))
    if not other or me in other:
        return None
    same = sorted(set(links.get(other[0]) or ()))
    if me not in same or set(same) & set(other):
        return None
    if any(sorted(set(links.get(o) or ())) != same for o in other):
        return None
    if any(sorted(set(links.get(s) or ())) != other for s in same):
        return None
    return same, other


# -- stepping the group ------------------------------------------------------------------


def _overlapping(logic: BreathingLogic) -> bool:
    """Is this unit still finishing a CO2 vent overlap: exhausting on, or paused before its late intake?"""
    return logic.overlap_until is not None or logic.joining


def _way(logic: BreathingLogic, overlap: bool = False) -> str | None:
    """Which way this unit's side breathes now: its running phase, or the other side's opposite while it rests.

    While some unit overlaps, the group has just switched: an overlapping
    exhaust's side takes air in, and a pause is before its side's new phase.
    """
    if overlap:
        if logic.overlap_until is not None:
            return opposite(logic.phase)
        return logic.next_phase if logic.phase == PHASE_PAUSE else logic.phase
    if logic.phase in _ACTIVE:
        return logic.phase
    if logic.phase == PHASE_PAUSE and logic.resting and logic.next_phase is not None:
        return opposite(logic.next_phase)  # paused before the phase the other side runs now
    return None


def aligned_sides(side_a: Sequence[BreathingLogic], side_b: Sequence[BreathingLogic]) -> bool:
    """Do the two sides breathe opposite ways (or pause before opposite phases)?

    A unit that rests while the others finish is paused before the phase the
    other side is running now, so that after the switch the sides go opposite
    ways. An ordinary pause (a unit that was breathing alone) never counts as
    resting while anyone runs.
    """
    units = [*side_a, *side_b]
    overlap = any(_overlapping(u) for u in units)
    if not overlap and not any(u.phase in _ACTIVE for u in units):
        if any(u.phase != PHASE_PAUSE for u in units):
            return False
        ways_a, ways_b = {u.next_phase for u in side_a}, {u.next_phase for u in side_b}
    else:
        ways_a, ways_b = {_way(u, overlap) for u in side_a}, {_way(u, overlap) for u in side_b}
    if len(ways_a) != 1 or len(ways_b) != 1 or None in ways_a:
        return False
    return ways_b == {opposite(next(iter(ways_a)))}


def aligned(a: BreathingLogic, b: BreathingLogic) -> bool:
    """Are the two units of a pair breathing opposite ways (or pausing before opposite phases)?"""
    return aligned_sides([a], [b])


def resting(logic: BreathingLogic, *others: BreathingLogic) -> bool:
    """Is this unit resting (paused, fans off) while the other side of its group finishes its phase?

    It is paused before the phase the other side runs now.
    """
    return (
        logic.phase == PHASE_PAUSE
        and logic.resting
        and logic.next_phase is not None
        and any(o.phase == logic.next_phase for o in others)
    )


def group_lean(this: BreathingLogic, others: Sequence[BreathingLogic]) -> float | None:
    """The strongest drying lean among the other units, for this unit's intakes.

    None when this unit's own drying stood down to protect its room.
    """
    if this.drying.status in (DRYING_ROOM_COLD, DRYING_OUTDOOR_HUMID):
        return None
    shares = [o.drying.intake_share for o in others if o.drying.intake_share is not None]
    return min(shares) if shares else None


def step_pair(now: float, a: Unit, b: Unit, rule: str) -> bool:
    """Advance a pair by one tick; `rule` is from a's point of view. True if either changed."""
    lead = {SYNC_LEAD_THIS: a, SYNC_LEAD_PARTNER: b}.get(rule)
    return step_sides(now, [a], [b], rule, lead)


def step_sides(
    now: float, side_a: Sequence[Unit], side_b: Sequence[Unit], rule: str, lead: Unit | None = None
) -> bool:
    """Advance a group by one tick. True if any unit changed.

    `lead` is the lead unit under a lead rule (pairs only); with no lead, a
    lead rule counts as either.
    """
    units = [*side_a, *side_b]
    # One drying decision for the group: each unit's intakes follow the others' leans too.
    for u in units:
        u.logic.grouped = True
        u.logic.partner_intake_share = group_lean(u.logic, [o.logic for o in units if o is not u])
    if not aligned_sides([u.logic for u in side_a], [u.logic for u in side_b]):
        # Pairing up (just synced, or a unit just started): all pause, then the
        # sides go opposite ways, with the first unit's next phase first.
        first = side_a[0].logic
        nxt = opposite(first.phase) if first.phase in _ACTIVE else first.next_phase or PHASE_EXHAUST
        for side, way in ((side_a, nxt), (side_b, opposite(nxt))):
            for u in side:
                u.logic.switch(now, REASON_SYNCED, u.inside, u.outside, way)
        return True

    if any(_overlapping(u.logic) for u in units):
        return _overlap_step(now, units)

    running = [u for u in units if u.logic.phase in _ACTIVE]
    if not running:
        return _leave_pause(now, units)
    reasons = {
        id(u): u.logic.pending_reason(now, u.mode, u.settings, u.inside, u.outside) for u in running
    }

    if not all(any(u.logic.phase in _ACTIVE for u in side) for side in (side_a, side_b)):
        # A whole side rests. A unit left running alone ends by its own rules,
        # whatever the sync rule; several decide among themselves by the rule.
        if len(running) == 1:
            if reasons[id(running[0])] is None:
                return False
            _switch(now, units, reasons)
            return True
        lead = None
        if rule in LEAD_RULES:
            rule = SYNC_EITHER

    # An intake that started late, after an overlap, runs at least its Minimum phase.
    young = any(u.logic.late and now - u.logic.phase_started < u.settings.min_phase_seconds for u in running)
    if not young and _should_switch(now, rule, running, reasons, lead):
        if lead is not None:
            for u in running:
                # A reason that did not count (a follower's, under a lead rule) is not
                # why this unit switched: the group did. One that ended its phase early did.
                if u is not lead and reasons[id(u)] not in EARLY:
                    reasons[id(u)] = None
        _switch(now, units, reasons)
        return True

    # Not switching: a unit whose phase ended early rests.
    rested = False
    for u in running:
        reason = reasons[id(u)]
        if reason not in EARLY:
            continue
        if u.logic.phase == PHASE_EXHAUST and _pulls_passive_intake(u, units):
            # Another unit's intake fan is off: this exhaust is what pulls its air in.
            _switch(now, units, {id(u): reason})
            return True
        u.logic.switch(now, reason, u.inside, u.outside, opposite(u.logic.phase))
        u.logic.resting = True
        rested = True
    if rested:
        _leave_pause(now, units)  # all rested at once: on together when all pauses are zero
    return rested


def _pulls_passive_intake(unit: Unit, units: Sequence[Unit]) -> bool:
    """Is this the last unit exhausting while another unit's passive intake depends on it?"""
    others = [o.logic for o in units if o is not unit]
    if any(o.phase == PHASE_EXHAUST for o in others):
        return False
    return any(o.phase == PHASE_INTAKE and o.passive for o in others)


def _switch(now: float, units: Sequence[Unit], reasons: Mapping[int, str | None]) -> None:
    """Switch the group: each running unit ends its phase, and a resting one keeps its next phase.

    In CO2 vent mode an exhaust keeps going for its overlap instead.
    """
    for u in units:
        logic = u.logic
        if logic.phase in _ACTIVE:
            overlap = logic.co2_vent_overlap(now, u.settings)
            if overlap > 0:
                logic.overlap_until = now + overlap
                continue
            reason = reasons.get(id(u)) or REASON_SYNCED
            logic.switch(now, reason, u.inside, u.outside, opposite(logic.phase))
    _leave_pause(now, [u for u in units if not _overlapping(u.logic)])  # at once, when all pauses are zero


def _overlap_step(now: float, units: Sequence[Unit]) -> bool:
    """While exhausts overlap: each overlap ends on time, then its unit starts its intake after its own pause."""
    changed = False
    for u in units:
        logic = u.logic
        if logic.overlap_until is not None and now >= logic.overlap_until:
            logic.switch(now, REASON_CO2_VENT, u.inside, u.outside, PHASE_INTAKE)
            logic.joining = True
            changed = True
        if logic.joining and logic.pause_over(now, u.settings):
            logic.begin_next(now, u.mode, u.settings, u.inside, u.outside)
            logic.late = True
            changed = True
    # The other side, paused since the switch, starts together.
    paused = [u for u in units if u.logic.phase == PHASE_PAUSE and not _overlapping(u.logic)]
    if paused and _leave_pause(now, paused):
        changed = True
    return changed


def _leave_pause(now: float, units: Sequence[Unit]) -> bool:
    """All start their next phase together, once all pauses have run out."""
    if not all(u.logic.pause_over(now, u.settings) for u in units):
        return False
    for u in units:
        u.logic.begin_next(now, u.mode, u.settings, u.inside, u.outside)
    return True


def _should_switch(
    now: float, rule: str, running: Sequence[Unit], reasons: Mapping[int, str | None], lead: Unit | None
) -> bool:
    """Does the sync rule switch the group, given why each running unit's phase would end (None: not yet)?"""
    if lead is not None and lead.logic.late and len(running) > 1:
        lead = None  # a lead whose intake started late, after an overlap, does not decide: the other does
    if lead is not None:
        reason = reasons.get(id(lead))
        return reason is not None and reason not in EARLY
    found = [reasons[id(u)] for u in running]
    if any(r in HARD for r in found):
        return True
    late = {id(u) for u in running if u.logic.late}
    if late and len(late) < len(running):
        # An intake that started late, after an overlap, neither holds the others up
        # nor ends their phases (once past its Minimum phase): the units that started
        # on time decide. Its Maximum phase still counts, and a protection still rests it.
        found = [r for u, r in zip(running, found) if id(u) not in late]
        running = [u for u in running if id(u) not in late]
    if rule == SYNC_BOTH:
        return all(r is not None for r in found)
    if all(r in EARLY for r in found):
        return True  # every phase ended early: nobody is left to finish
    if rule == SYNC_AVERAGE:
        # A unit whose phase only ended early is not done: it rests, and the others finish.
        done = [_average_done(now, u, None if reasons[id(u)] in EARLY else reasons[id(u)]) for u in running]
        return sum(done) >= len(done)
    # SYNC_EITHER, a lead rule without its lead, and anything unknown:
    # a unit whose phase only ended early rests instead.
    return any(r is not None and r not in EARLY for r in found)


def _average_done(now: float, unit: Unit, reason: str | None) -> float:
    """A unit's progress for the Average rule: never enough before its Minimum phase, at least 1 once it is done."""
    logic = unit.logic
    started = now if logic.phase_started is None else logic.phase_started
    if now - started < unit.settings.min_phase_seconds:
        return float("-inf")  # too early: the group cannot switch yet
    progress = logic.progress(now, unit.settings, unit.inside, unit.outside)
    return max(progress, 1.0) if reason is not None else progress
