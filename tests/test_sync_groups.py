"""Synced groups larger than a pair: the stepping, and who breathes with whom.

A pair must step exactly as it did in 0.5.14 (tests/legacy_sync.py), second
by second, under every rule.
"""

from __future__ import annotations

import random

import pytest

from custom_components.recuperator.const import (
    DEFAULTS,
    MODE_AUTOMATIC,
    MODE_TIMED,
    PHASE_EXHAUST,
    PHASE_INTAKE,
    PHASE_PAUSE,
    SYNC_AVERAGE,
    SYNC_BOTH,
    SYNC_EITHER,
    SYNC_LEAD_PARTNER,
    SYNC_LEAD_THIS,
)
from custom_components.recuperator.drying import Drying
from custom_components.recuperator.logic import BreathingLogic, Settings
from custom_components.recuperator.sync import (
    Unit,
    aligned_sides,
    link_entries_of,
    resting,
    sides_of,
    step_pair,
    step_sides,
)

from . import legacy_sync

RULES = [SYNC_EITHER, SYNC_BOTH, SYNC_AVERAGE, SYNC_LEAD_THIS, SYNC_LEAD_PARTNER]
ACTIVE = (PHASE_EXHAUST, PHASE_INTAKE)


class Core:
    """A core that charges while exhausting and drains while taking air in, with a little noise.

    The probe at the far face reads the stored heat, so an intake ends by
    Maximum supply drop once the core has given up enough of it.
    """

    def __init__(self, seed: int, room: float = 20.0, outdoor: float = 0.0, charge=0.01, drain=0.004) -> None:
        self.rng = random.Random(seed)
        self.room, self.outdoor, self.rate_in, self.rate_out = room, outdoor, charge, drain
        self.charge = 0.5

    def probes(self, phase: str) -> tuple[float, float]:
        if phase == PHASE_EXHAUST:
            self.charge += (1 - self.charge) * self.rate_in
        elif phase == PHASE_INTAKE:
            self.charge -= self.charge * self.rate_out
        face = self.outdoor + (self.room - self.outdoor) * self.charge
        noise = round(self.rng.gauss(0, 0.03), 2)
        if phase == PHASE_EXHAUST:
            return self.room + noise, face + noise
        return face + noise, self.outdoor + noise


# -- a pair, against the 0.5.14 code -----------------------------------------------------

SCENARIOS = {
    "timed": ({}, {"timed_exhaust_seconds": 70}, MODE_TIMED, {}),
    "automatic": ({"recovery_percent": 60}, {"recovery_percent": 70}, MODE_AUTOMATIC, {}),
    "drafts": ({"max_supply_drop": 2}, {"max_supply_drop": 4}, MODE_AUTOMATIC, {}),
    "cold": ({"cold_threshold": 5, "cold_intake_max_seconds": 60}, {"cold_threshold": 5}, MODE_AUTOMATIC, {"outdoor": -8}),
    "limits": ({"phase_limit": "limited_intake", "phase_limit_percent": 60}, {"phase_limit": "limited_exhaust"}, MODE_AUTOMATIC, {}),
    "drying": ({}, {}, MODE_TIMED, {"share_a": 50, "cold_room_b": True}),
    "passive": ({"passive_intake": True}, {}, MODE_AUTOMATIC, {}),
    "passive_both": ({"passive_intake": True, "max_supply_drop": 2}, {"passive_intake": True}, MODE_TIMED, {}),
}


def _world(name: str, seed: int):
    a_extra, b_extra, mode, more = SCENARIOS[name]
    base = {**DEFAULTS, "max_phase_seconds": 240, "min_phase_seconds": 15, "timed_exhaust_seconds": 90, "timed_intake_seconds": 80}
    settings = [Settings.from_mapping({**base, **a_extra}), Settings.from_mapping({**base, **b_extra})]
    outdoor = more.get("outdoor", 0.0)
    cores = [Core(seed, outdoor=outdoor), Core(seed + 1, outdoor=outdoor, charge=0.012, drain=0.005)]
    logics = [BreathingLogic(), BreathingLogic()]
    for logic in logics:
        logic.assisted = True
    if share := more.get("share_a"):
        logics[0].drying = Drying("drying", share)
    if more.get("cold_room_b"):
        logics[1].drying = Drying("room_too_cold")
    for logic, s, core in zip(logics, settings, cores):
        logic.start(0, mode, s, *core.probes(logic.phase))
    return logics, settings, cores, mode


def _snapshot(logic: BreathingLogic) -> tuple:
    return (
        logic.phase, logic.next_phase, logic.phase_started, logic.last_reason, logic.resting,
        logic.partner_intake_share, logic.last_exhaust_seconds, logic.last_intake_seconds,
        logic.passive, logic.cold, dict(logic._measure), logic.last_intake_limited,
    )


@pytest.mark.parametrize("rule", RULES)
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_a_pair_steps_exactly_as_in_0_5_14(rule: str, scenario: str) -> None:
    old, new = _world(scenario, 11), _world(scenario, 11)
    switches = 0
    for t in range(1, 2400):
        results = []
        for (logics, settings, cores, mode), step in ((old, legacy_sync.step_pair), (new, step_pair)):
            unit = legacy_sync.Unit if step is legacy_sync.step_pair else Unit
            a, b = (unit(lg, mode, s, *c.probes(lg.phase)) for lg, s, c in zip(logics, settings, cores))
            results.append(step(t, a, b, rule))
        assert results[0] == results[1], f"t={t}"
        switches += results[0]
        for lo, ln in zip(old[0], new[0]):
            assert _snapshot(lo) == _snapshot(ln), f"t={t}"
        assert resting(new[0][0], new[0][1]) == legacy_sync.resting(old[0][0], old[0][1])
    assert switches > 10  # it really breathed


# -- one big unit against two small ones ------------------------------------------------------


def breathe_group(rule: str, seconds: int, cores, extras=None, mode=MODE_AUTOMATIC, sides=(1, 2), shares=None):
    """Units 0..n-1, the first `sides[0]` on side A. Returns logics and each unit's (phase, seconds, reason) list."""
    n = len(cores)
    base = {**DEFAULTS, "max_phase_seconds": 240, "min_phase_seconds": 15}
    settings = [Settings.from_mapping({**base, **((extras or [{}] * n)[i])}) for i in range(n)]
    logics = [BreathingLogic() for _ in range(n)]
    for i, logic in enumerate(logics):
        logic.assisted = True
        if shares and shares[i] is not None:
            logic.drying = shares[i]
        logic.start(0, mode, settings[i], *cores[i].probes(logic.phase))
    phases = [[] for _ in range(n)]
    began = [(lg.phase, 0) for lg in logics]
    for t in range(1, seconds + 1):
        units = [Unit(lg, mode, s, *c.probes(lg.phase)) for lg, s, c in zip(logics, settings, cores)]
        step_sides(t, units[: sides[0]], units[sides[0] :], rule)
        side_a, side_b = logics[: sides[0]], logics[sides[0] :]
        ways_a = {lg.phase for lg in side_a if lg.phase in ACTIVE}
        ways_b = {lg.phase for lg in side_b if lg.phase in ACTIVE}
        # a side never breathes two ways at once, and never the same way as the other side
        assert len(ways_a) <= 1 and len(ways_b) <= 1 and not ways_a & ways_b, f"t={t}"
        for i, logic in enumerate(logics):
            phase, since = began[i]
            if logic.phase != phase:
                if phase in ACTIVE:
                    phases[i].append((phase, t - since, logic.last_reason))
                began[i] = (logic.phase, t)
    return logics, [p[2:] for p in phases]  # skip pairing up


def trio(seed: int = 3, outdoor: float = 0.0, small_drain: float = 0.004):
    """A big core (slow to charge and drain) and two small ones."""
    return [
        Core(seed, outdoor=outdoor, charge=0.006, drain=0.0025),
        Core(seed + 1, outdoor=outdoor, drain=small_drain),
        Core(seed + 2, outdoor=outdoor, charge=0.011, drain=small_drain * 1.1),
    ]


@pytest.mark.parametrize("rule", [SYNC_EITHER, SYNC_BOTH, SYNC_AVERAGE])
def test_one_big_against_two_small_breathes_balanced(rule: str) -> None:
    logics, phases = breathe_group(rule, 4000, trio())
    assert all(len(p) > 10 for p in phases)


def test_a_drained_small_intake_rests_while_the_others_carry_on() -> None:
    """Small 1 drains fast: its intakes end on draft protection, small 2 runs its out."""
    cores = trio(outdoor=17)
    cores[1].rate_out = 0.03
    logics, phases = breathe_group(SYNC_BOTH, 4000, cores, extras=[{}, {"max_supply_drop": 1}, {}])
    assert {r for p, _s, r in phases[1] if p == PHASE_INTAKE} == {"supply_drop"}
    # nobody else was cut short by it
    assert {r for p, _s, r in phases[2] if p == PHASE_INTAKE} == {"max_time"}
    assert {s for p, s, _r in phases[2] if p == PHASE_INTAKE} == {240}


def test_under_both_only_maximum_cuts_a_sibling_short() -> None:
    cores = trio()
    logics, phases = breathe_group(SYNC_BOTH, 5000, cores)
    for unit in phases:
        assert {r for _p, _s, r in unit} <= {"recovered", "settled", "supply_drop", "max_time", "synced"}
    # "synced" only ever comes from another unit's Maximum phase
    for i, unit in enumerate(phases):
        for p, seconds, reason in unit:
            if reason == "synced":
                assert seconds < 240


def test_under_both_the_rule_holds_while_a_whole_side_rests() -> None:
    """The big unit's intakes end early (draft protection): it rests, and the two small
    exhausts each run until both are done, rather than the first one ending both."""
    cores = trio()
    cores[0].rate_out = 0.03
    logics, phases = breathe_group(SYNC_BOTH, 4000, cores, extras=[{"max_supply_drop": 1}, {}, {}])
    assert {r for p, _s, r in phases[0] if p == PHASE_INTAKE} == {"supply_drop"}
    for unit in phases[1:]:
        assert "synced" not in {r for p, _s, r in unit if p == PHASE_EXHAUST}


def test_maximum_phase_switches_everyone() -> None:
    cores = trio()
    logics, phases = breathe_group(SYNC_BOTH, 3000, cores, extras=[{"max_phase_seconds": 60}, {}, {}])
    big = [s for p, s, r in phases[0] if r == "max_time"]
    assert big and all(s == 60 for s in big)
    assert {s for p, s, r in phases[1] if r == "synced"} <= {60}


def test_the_strongest_lean_applies_to_the_others_but_not_a_cold_room() -> None:
    cores = trio()
    shares = [Drying("drying", 70), Drying("drying", 50), Drying("room_too_cold")]
    logics, _phases = breathe_group(SYNC_EITHER, 20, cores, mode=MODE_TIMED, shares=shares)
    assert logics[0].partner_intake_share == 50
    assert logics[1].partner_intake_share == 70
    assert logics[2].partner_intake_share is None


def _paused(next_phase: str, resting_: bool = False) -> BreathingLogic:
    logic = BreathingLogic()
    logic.phase, logic.next_phase, logic.resting = PHASE_PAUSE, next_phase, resting_
    return logic


def _running(phase: str) -> BreathingLogic:
    logic = BreathingLogic()
    logic.phase, logic.phase_started = phase, 0
    return logic


def test_aligned_sides() -> None:
    ex, ex2, inn = _running(PHASE_EXHAUST), _running(PHASE_EXHAUST), _running(PHASE_INTAKE)
    assert aligned_sides([ex, ex2], [inn])
    assert not aligned_sides([ex, inn], [_running(PHASE_INTAKE)])
    # a unit resting before the other side's phase is in line
    assert aligned_sides([ex, _paused(PHASE_INTAKE, resting_=True)], [inn])
    assert aligned_sides([_paused(PHASE_INTAKE, resting_=True)], [inn])
    # an ordinary pause is not, while anyone runs
    assert not aligned_sides([ex, _paused(PHASE_INTAKE)], [inn])
    # all paused: next phases agree per side, opposite across
    assert aligned_sides([_paused(PHASE_EXHAUST), _paused(PHASE_EXHAUST)], [_paused(PHASE_INTAKE)])
    assert not aligned_sides([_paused(PHASE_EXHAUST), _paused(PHASE_INTAKE)], [_paused(PHASE_INTAKE)])


def test_passive_guard_only_for_the_last_exhauster() -> None:
    """Two exhaust while the other side's intake is passive: the first to end early rests,
    the last switches the group instead (nothing else would pull the air in)."""
    s = Settings.from_mapping({**DEFAULTS, "min_phase_seconds": 5, "timed_exhaust_seconds": 200, "timed_intake_seconds": 200})
    limited = Settings.from_mapping({**DEFAULTS, "min_phase_seconds": 5, "timed_exhaust_seconds": 200,
                                     "phase_limit": "limited_exhaust", "phase_limit_percent": 50})
    a1, a2, b = BreathingLogic(), BreathingLogic(), BreathingLogic()
    for logic in (a1, a2, b):
        logic.assisted = True
    a1.phase, a1.next_phase, a1.phase_started = PHASE_EXHAUST, None, 0
    a1._measure[PHASE_INTAKE] = 40  # limited exhaust: 20 s
    a2.phase, a2.phase_started = PHASE_EXHAUST, 0
    a2._measure[PHASE_INTAKE] = 60  # 30 s
    b.phase, b.phase_started, b.passive = PHASE_INTAKE, 0, True
    a1.timed_reason = a2.timed_reason = b.timed_reason = "mode"
    units = lambda: ([Unit(a1, MODE_TIMED, limited, 20, 5), Unit(a2, MODE_TIMED, limited, 20, 5)], [Unit(b, MODE_TIMED, s, 20, 5)])
    for t in range(1, 21):
        step_sides(t, *units(), SYNC_EITHER)
    assert a1.phase == PHASE_PAUSE and a1.resting and a2.phase == PHASE_EXHAUST and b.phase == PHASE_INTAKE
    for t in range(21, 31):
        step_sides(t, *units(), SYNC_EITHER)
    assert b.phase == PHASE_PAUSE and b.last_reason == "synced"  # a2 switched the group
    assert a2.last_reason == "phase_limit" and not a2.resting


# -- who breathes with whom ----------------------------------------------------------------


def test_sides_of() -> None:
    pair = {"a": ["b"], "b": ["a"]}
    assert sides_of("a", pair) == (["a"], ["b"])
    trio_links = {"big": ["s1", "s2"], "s1": ["big"], "s2": ["big"]}
    assert sides_of("big", trio_links) == (["big"], ["s1", "s2"])
    assert sides_of("s2", trio_links) == (["s1", "s2"], ["big"])
    quad = {"a1": ["b1", "b2"], "a2": ["b1", "b2"], "b1": ["a1", "a2"], "b2": ["a1", "a2"]}
    assert sides_of("b2", quad) == (["b1", "b2"], ["a1", "a2"])
    # a missing back-pointer: nobody of that group breathes as a group
    assert sides_of("big", {"big": ["s1", "s2"], "s1": ["big"], "s2": []}) is None
    assert sides_of("s1", {"big": ["s1", "s2"], "s1": ["big"], "s2": []}) is None
    # two pairs that share nothing stay apart
    two_pairs = {**pair, "c": ["d"], "d": ["c"]}
    assert sides_of("c", two_pairs) == (["c"], ["d"])
    assert sides_of("a", {"a": []}) is None
    assert sides_of("a", {"a": ["a"]}) is None


def test_link_entries_of() -> None:
    assert link_entries_of({"link_type": "synced", "link_entry": "b"}) == ["b"]
    data = {"link_type": "synced", "link_entry": "b", "link_entries": ["b", "c"]}
    assert link_entries_of(data) == ["b", "c"]
    # a list an older version left behind after re-linking: the single link wins
    assert link_entries_of({**data, "link_entry": "d"}) == ["d"]
    assert link_entries_of({**data, "link_type": "none"}) == []


@pytest.mark.parametrize("rule", [SYNC_EITHER, SYNC_BOTH, SYNC_AVERAGE])
def test_a_groups_breaths_do_not_shrink(rule: str) -> None:
    """Draft protection and a phase limit on a small unit: the breaths of the last hour are as long as the first's."""
    cores = trio(outdoor=-5)
    extras = [{}, {"max_supply_drop": 2, "phase_limit": "limited_intake", "phase_limit_percent": 70}, {}]
    _logics, phases = breathe_group(rule, 7200, cores, extras=extras)
    for unit in phases:
        exhausts = [s for p, s, _r in unit if p == PHASE_EXHAUST]
        assert len(exhausts) > 10
        half = len(exhausts) // 2
        first, last = sorted(exhausts[:half]), sorted(exhausts[half:])
        assert last[len(last) // 2] >= first[len(first) // 2] - 1
