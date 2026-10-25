"""CO2 vent mode: a synced group's exhausts overlap, so one unit is always exhausting."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant

from custom_components.recuperator.const import (
    DEFAULTS,
    MODE_AUTOMATIC,
    MODE_TIMED,
    PHASE_EXHAUST,
    PHASE_INTAKE,
    SYNC_AVERAGE,
    SYNC_BOTH,
    SYNC_EITHER,
    SYNC_LEAD_PARTNER,
    SYNC_LEAD_THIS,
)
from custom_components.recuperator.drying import Drying
from custom_components.recuperator.logic import BreathingLogic, Settings
from custom_components.recuperator.sync import Unit, step_pair, step_sides

from .conftest import DATA, EXHAUST, LINK_EXHAUST, set_probe, setup_entry
from .test_controller import on, tick
from .test_sync_groups import Core
from .test_sync import DATA_B, T100, _breathe, _entry, _synced

ACTIVE = (PHASE_EXHAUST, PHASE_INTAKE)
VENT = {"co2_vent": True, "co2_vent_overlap_percent": 20}


def vent_pair(rule, seconds, a_extra=None, b_extra=None, a_share=None, b_share=None, mode=MODE_TIMED, core=None):
    """Like test_sync.breathe_pair, but the exhausts may overlap. Also returns each second's (A, B) phases."""
    la, lb = BreathingLogic(), BreathingLogic()
    sa = Settings.from_mapping({**DEFAULTS, **T100, **(a_extra or {})})
    sb = Settings.from_mapping({**DEFAULTS, **T100, **(b_extra or {})})
    la.drying = Drying("drying", a_share) if a_share else Drying()
    lb.drying = Drying("drying", b_share) if b_share else Drying()
    cores = {id(la): core(), id(lb): core()} if core else {}

    def read(logic, t):
        return cores[id(logic)].probes(logic.phase) if core else (20.0, 5.0)

    la.start(0, mode, sa, *read(la, 0))
    lb.start(0, mode, sb, *read(lb, 0))
    phases = {id(la): [], id(lb): []}
    began = {id(la): (la.phase, 0), id(lb): (lb.phase, 0)}
    seen = []
    for t in range(1, seconds + 1):
        step_pair(t, Unit(la, mode, sa, *read(la, t)), Unit(lb, mode, sb, *read(lb, t)), rule)
        seen.append((t, la.phase, lb.phase))
        # never both taking air in
        assert not (la.phase == lb.phase == PHASE_INTAKE), f"t={t}"
        for logic in (la, lb):
            phase, since = began[id(logic)]
            if logic.phase != phase:
                if phase in ACTIVE:
                    phases[id(logic)].append((phase, t - since, logic.last_reason))
                began[id(logic)] = (logic.phase, t)
    return phases[id(la)][2:], phases[id(lb)][2:], la, lb, seen


def lengths(phases, phase):
    return {seconds for p, seconds, _reason in phases if p == phase}


def exhausting_always(seen, after: int = 10) -> bool:
    """After pairing up, at least one unit exhausts every second."""
    return all(PHASE_EXHAUST in (a, b) for t, a, b in seen if t > after)


def test_the_exhausts_overlap_so_one_is_always_exhausting() -> None:
    """20 % of a 100 s exhaust: each exhaust runs 20 s on, and the late intake gets what is left."""
    a, b, _la, _lb, seen = vent_pair(SYNC_EITHER, 3000, VENT, VENT)
    assert exhausting_always(seen)
    assert lengths(a, PHASE_EXHAUST) == lengths(b, PHASE_EXHAUST) == {120}
    assert lengths(a, PHASE_INTAKE) == lengths(b, PHASE_INTAKE) == {80}  # 100 s less the overlap
    assert {r for p, _s, r in a + b if p == PHASE_EXHAUST} == {"co2_vent"}
    both = sum(1 for _t, x, y in seen if x == y == PHASE_EXHAUST)
    assert both > 0.15 * len(seen)


def test_the_overlap_minimum_in_seconds() -> None:
    extra = {"co2_vent": True, "co2_vent_overlap_percent": 0, "co2_vent_overlap_seconds": 30}
    a, b, *_rest, seen = vent_pair(SYNC_EITHER, 3000, extra, extra)
    assert exhausting_always(seen)
    assert lengths(a, PHASE_EXHAUST) == lengths(b, PHASE_EXHAUST) == {130}


def test_the_larger_of_the_two_wins() -> None:
    extra = {"co2_vent": True, "co2_vent_overlap_percent": 20, "co2_vent_overlap_seconds": 10}
    a, *_rest = vent_pair(SYNC_EITHER, 3000, extra, extra)
    assert lengths(a, PHASE_EXHAUST) == {120}


def test_off_by_default_breathes_as_before() -> None:
    a, b, *_rest, seen = vent_pair(SYNC_EITHER, 3000)
    assert lengths(a, PHASE_EXHAUST) == lengths(a, PHASE_INTAKE) == {100}
    assert not any(x == y == PHASE_EXHAUST for _t, x, y in seen)


def test_only_a_unit_with_vent_mode_on_overlaps() -> None:
    a, b, *_rest, seen = vent_pair(SYNC_EITHER, 3000, VENT, None)
    assert lengths(a, PHASE_EXHAUST) == {120}
    assert lengths(b, PHASE_EXHAUST) == {100}
    assert lengths(a, PHASE_INTAKE) == {80}  # A's own intake starts late
    assert lengths(b, PHASE_INTAKE) == {100}


def test_drying_lengthens_the_overlap_instead_of_resting() -> None:
    """A dries at a 60 % intake share: 20 % + 40 % of the exhaust, and no unit rests."""
    a, b, la, lb, seen = vent_pair(SYNC_EITHER, 3000, VENT, VENT, a_share=60)
    assert exhausting_always(seen)
    assert lengths(a, PHASE_EXHAUST) == lengths(b, PHASE_EXHAUST) == {160}
    assert lengths(a, PHASE_INTAKE) == lengths(b, PHASE_INTAKE) == {40}
    assert "drying" not in {r for _p, _s, r in a + b}
    assert not la.resting and not lb.resting


def test_the_overlap_share_stops_at_90_percent() -> None:
    """90 % plus a 50 % lean: still 90 %, and the late intake keeps its Minimum phase."""
    extra = {"co2_vent": True, "co2_vent_overlap_percent": 90, "min_phase_seconds": 5}
    a, b, *_rest, seen = vent_pair(SYNC_EITHER, 3000, extra, extra, a_share=50)
    assert exhausting_always(seen)
    assert lengths(a, PHASE_EXHAUST) == {190}
    assert lengths(a, PHASE_INTAKE) == {10}


@pytest.mark.parametrize("rule", [SYNC_EITHER, SYNC_BOTH, SYNC_AVERAGE, SYNC_LEAD_THIS, SYNC_LEAD_PARTNER])
def test_long_overlaps_cannot_grow_the_breaths(rule: str) -> None:
    """At the 90 % cap, with 1600 s breaths and a 300 s relay floor, the breaths settle instead of growing."""
    extra = {
        "co2_vent": True, "co2_vent_overlap_percent": 90,
        "timed_exhaust_seconds": 1600, "timed_intake_seconds": 1600, "min_phase_seconds": 300,
    }
    a, b, *_rest, seen = vent_pair(rule, 24 * 3600, extra, extra)
    assert exhausting_always(seen)
    for unit in (a, b):
        exhausts = [s for p, s, _r in unit if p == PHASE_EXHAUST]
        intakes = [s for p, s, _r in unit if p == PHASE_INTAKE]
        assert len(exhausts) > 10
        # The late intake's 300 s floor stretches the breaths, but only up to a fixed size:
        # a period P = 0.9 P + 301 s settles at about 3000 s, an exhaust at 1.9 P.
        assert max(exhausts) < 1.9 * 301 / 0.1 + 10
        assert min(intakes) >= 300  # the relays' floor


@pytest.mark.parametrize("rule", [SYNC_EITHER, SYNC_BOTH, SYNC_AVERAGE])
def test_automatic_breaths_hold_with_vent_mode(rule: str) -> None:
    extra = {**VENT, "max_phase_seconds": 600, "min_phase_seconds": 15}
    a, b, *_rest, seen = vent_pair(rule, 4 * 3600, extra, extra, mode=MODE_AUTOMATIC, core=lambda: Core(3))
    assert exhausting_always(seen, after=60)
    for unit in (a, b):
        exhausts = [s for p, s, _r in unit if p == PHASE_EXHAUST]
        assert len(exhausts) > 10
        half = len(exhausts) // 2
        first, last = sorted(exhausts[:half]), sorted(exhausts[half:])
        assert last[len(last) // 2] >= first[len(first) // 2] - 1


def test_a_protection_still_rests_the_late_intake() -> None:
    """Supply air too cold during the late intake: it rests, and the other's exhaust carries on."""
    la, lb = BreathingLogic(), BreathingLogic()
    s = Settings.from_mapping({**DEFAULTS, **T100, **VENT})
    for logic in (la, lb):
        logic.start(0, MODE_TIMED, s, 20.0, 5.0)
    for t in range(1, 230):
        step_pair(t, Unit(la, MODE_TIMED, s, 20.0, 5.0), Unit(lb, MODE_TIMED, s, 20.0, 5.0), SYNC_EITHER)
    late = la if la.late else lb
    other = lb if late is la else la
    assert late.phase == PHASE_INTAKE and other.phase == PHASE_EXHAUST
    cold = (10.0, 5.0)  # 10 °C below the room
    units = (Unit(la, MODE_TIMED, s, *(cold if la is late else (20.0, 5.0))),
             Unit(lb, MODE_TIMED, s, *(cold if lb is late else (20.0, 5.0))))
    step_pair(230, *units, SYNC_EITHER)
    assert late.last_reason == "supply_drop" and late.resting
    assert other.phase == PHASE_EXHAUST


def test_a_group_of_three_overlaps_side_by_side() -> None:
    """One big against two small, all in vent mode: someone is always exhausting."""
    cores = [Core(3, charge=0.006, drain=0.0025), Core(4), Core(5, charge=0.011)]
    settings = [Settings.from_mapping({**DEFAULTS, **VENT, "max_phase_seconds": 240, "min_phase_seconds": 15})] * 3
    logics = [BreathingLogic() for _ in cores]
    for logic, s, c in zip(logics, settings, cores):
        logic.assisted = True
        logic.start(0, MODE_AUTOMATIC, s, *c.probes(logic.phase))
    exhausting = []
    for t in range(1, 4000):
        units = [Unit(lg, MODE_AUTOMATIC, s, *c.probes(lg.phase)) for lg, s, c in zip(logics, settings, cores)]
        step_sides(t, units[:1], units[1:], SYNC_EITHER)
        side_b = {lg.phase for lg in logics[1:] if lg.phase in ACTIVE}
        assert len(side_b) <= 1, f"t={t}"  # a side still breathes one way
        exhausting.append(any(lg.phase == PHASE_EXHAUST for lg in logics))
    assert all(exhausting[30:])


def test_alone_vent_mode_does_nothing_and_drying_still_limits() -> None:
    logic = BreathingLogic()
    s = Settings.from_mapping({**DEFAULTS, **T100, **VENT})
    logic.drying = Drying("drying", 50)
    logic.start(0, MODE_TIMED, s, 20.0, 5.0)
    ends = []
    for t in range(1, 700):
        if logic.step(t, MODE_TIMED, s, 20.0, 5.0):
            ends.append((logic.last_reason, t))
    assert ("drying" in {r for r, _t in ends}) and "co2_vent" not in {r for r, _t in ends}


async def test_both_exhaust_fans_run_during_the_overlap(hass: HomeAssistant, fans, probes, freezer) -> None:
    set_probe(hass, "sensor.inside_b", 20.0)
    set_probe(hass, "sensor.outside_b", 5.0)
    options = {**T100, "timed_exhaust_seconds": 20, "timed_intake_seconds": 20, **VENT, "co2_vent_overlap_percent": 50}
    await setup_entry(hass, _entry("alpha", "Alpha", {**DATA, **_synced("beta")}, options))
    await setup_entry(hass, _entry("beta", "Beta", {**DATA_B, **_synced("alpha")}, options))
    await _breathe(hass, "alpha")
    await _breathe(hass, "beta")
    for _ in range(200):
        await tick(hass, freezer)
        if hass.states.get("sensor.alpha_phase").attributes["co2_vent_overlap"] or hass.states.get(
            "sensor.beta_phase"
        ).attributes["co2_vent_overlap"]:
            if on(hass) == {EXHAUST, LINK_EXHAUST}:
                break
    else:
        pytest.fail("the exhausts never overlapped")
    reasons = set()
    for _ in range(60):
        await tick(hass, freezer)
        assert on(hass) & {EXHAUST, LINK_EXHAUST}  # one exhaust fan always on
        reasons |= {hass.states.get(f"sensor.{n}_last_change_reason").state for n in ("alpha", "beta")}
    assert "co2_vent" in reasons


async def test_vent_mode_switch_and_numbers(hass: HomeAssistant, fans, probes) -> None:
    set_probe(hass, "sensor.inside_b", 20.0)
    set_probe(hass, "sensor.outside_b", 5.0)
    entry = await setup_entry(hass, _entry("alpha", "Alpha", {**DATA, **_synced("beta")}, {}))
    assert hass.states.get("switch.alpha_co2_vent_mode").state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.alpha_co2_vent_mode"}, blocking=True)
    assert entry.options["co2_vent"] is True
    assert float(hass.states.get("number.alpha_co2_vent_overlap").state) == 20
    assert float(hass.states.get("number.alpha_co2_vent_overlap_minimum").state) == 0
