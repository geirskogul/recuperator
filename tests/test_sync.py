"""Two full recuperators synced as a pair: the sync rules, the fans, and linking them up."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.recuperator.const import (
    DEFAULTS,
    DOMAIN,
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
    unique_id_for,
)
from custom_components.recuperator.drying import Drying
from custom_components.recuperator.logic import BreathingLogic, Settings
from custom_components.recuperator.sync import Unit, mirror, resting, step_pair

from .conftest import DATA, EXHAUST, INTAKE, LINK_EXHAUST, LINK_INTAKE, set_probe, setup_entry
from .test_controller import on, tick
from .test_logic import Core

# A: short phases; B: long ones (timed, so the lengths are exact).
A = {"timed_exhaust_seconds": 10, "timed_intake_seconds": 20, "pause_seconds": 1, "min_phase_seconds": 5}
B = {"timed_exhaust_seconds": 30, "timed_intake_seconds": 40, "pause_seconds": 1, "min_phase_seconds": 5}


def run(rule: str, seconds: int, mode: str = MODE_TIMED, probes=(20.0, 5.0)):
    """Both start exhausting at 0; returns the two logics and (time, A phase, B phase) at each change."""
    la, lb = BreathingLogic(), BreathingLogic()
    sa, sb = Settings.from_mapping({**DEFAULTS, **A}), Settings.from_mapping({**DEFAULTS, **B})
    la.start(0, mode, sa, *probes)
    lb.start(0, mode, sb, *probes)
    changes = []
    for t in range(1, seconds + 1):
        if step_pair(t, Unit(la, mode, sa, *probes), Unit(lb, mode, sb, *probes), rule):
            changes.append((t, la.phase, lb.phase))
    return la, lb, changes


def test_pairing_up_sends_them_opposite_ways() -> None:
    _la, _lb, changes = run(SYNC_EITHER, 2)
    # both started exhausting: both pause, then A takes air in while B exhausts
    assert changes == [(1, PHASE_PAUSE, PHASE_PAUSE), (2, PHASE_INTAKE, PHASE_EXHAUST)]


@pytest.mark.parametrize(
    ("rule", "first", "second"),
    [
        (SYNC_EITHER, 22, 33),  # A's 20 s intake, then A's 10 s exhaust
        (SYNC_BOTH, 32, 73),  # B's 30 s exhaust, then B's 40 s intake
        (SYNC_LEAD_THIS, 22, 33),  # A's lengths
        (SYNC_LEAD_PARTNER, 32, 73),  # B's lengths
        (SYNC_AVERAGE, 26, 43),  # e/20 + e/30 reaches 2 at 24 s; e/10 + e/40 at 16 s
    ],
)
def test_sync_rules(rule: str, first: int, second: int) -> None:
    _la, _lb, changes = run(rule, 80)
    switches = [t for t, a, b in changes if a == b == PHASE_PAUSE][1:]  # skip pairing up
    assert switches[:2] == [first, second]
    for _t, a, b in changes:  # never the same way at once
        assert a == b == PHASE_PAUSE or {a, b} == {PHASE_EXHAUST, PHASE_INTAKE}


def test_the_unit_done_first_keeps_running_under_both() -> None:
    la, lb, _ = run(SYNC_BOTH, 30)  # A's intake was done at 22
    assert (la.phase, lb.phase) == (PHASE_INTAKE, PHASE_EXHAUST)


def test_reasons_say_who_switched() -> None:
    la, lb, _ = run(SYNC_EITHER, 22)
    assert (la.last_reason, lb.last_reason) == ("timed", "synced")
    la, lb, _ = run(SYNC_LEAD_PARTNER, 22)
    assert la.phase == PHASE_INTAKE  # a follower that is done waits for the lead


def test_a_protection_rests_a_follower_while_the_lead_finishes() -> None:
    """Under a lead rule, the follower's cold supply air stops its intake, but not the lead's exhaust."""
    la, lb = BreathingLogic(), BreathingLogic()
    s = Settings.from_mapping({**DEFAULTS, "min_phase_seconds": 5, "max_phase_seconds": 600})
    la.start(0, MODE_AUTOMATIC, s, 20.0, 5.0)
    lb.start(0, MODE_AUTOMATIC, s, 20.0, 5.0)
    step_pair(1, Unit(la, MODE_AUTOMATIC, s, 20.0, 5.0), Unit(lb, MODE_AUTOMATIC, s, 20.0, 5.0), SYNC_LEAD_THIS)
    step_pair(2, Unit(la, MODE_AUTOMATIC, s, 20.0, 5.0), Unit(lb, MODE_AUTOMATIC, s, 20.0, 5.0), SYNC_LEAD_THIS)
    assert (la.phase, lb.phase) == (PHASE_INTAKE, PHASE_EXHAUST)
    # A's intake air stays warm; B exhausts while its far probe hardly moves: nobody is done
    for t in range(3, 10):
        assert not step_pair(t, Unit(la, MODE_AUTOMATIC, s, 20.0, 5.0), Unit(lb, MODE_AUTOMATIC, s, 20.0, 5.2), SYNC_LEAD_THIS)
    # flip the roles: B leads, and A's supply air (inside probe) drops 5 °C below the room
    assert step_pair(10, Unit(la, MODE_AUTOMATIC, s, 15.0, 5.0), Unit(lb, MODE_AUTOMATIC, s, 20.0, 5.2), SYNC_LEAD_PARTNER)
    assert la.last_reason == "supply_drop" and resting(la, lb)
    assert lb.phase == PHASE_EXHAUST


def test_mirror() -> None:
    assert mirror(SYNC_LEAD_THIS) == SYNC_LEAD_PARTNER
    assert mirror(SYNC_LEAD_PARTNER) == SYNC_LEAD_THIS
    assert mirror(SYNC_BOTH) == SYNC_BOTH


# -- a phase that ends early rests its unit instead of cutting the other one short ------

T100 = {"timed_exhaust_seconds": 100, "timed_intake_seconds": 100, "pause_seconds": 1, "min_phase_seconds": 5}


def breathe_pair(
    rule: str, seconds: int, a_extra=None, b_extra=None, a_share=None, b_share=None, probes=(20.0, 5.0), core=None
):
    """Both timed; drying leans at the given shares. Returns each unit's (phase, seconds, reason) list and the logics.

    With `core` (a class like test_logic.Core), each unit's probes come from a core of its own instead.
    """
    la, lb = BreathingLogic(), BreathingLogic()
    sa = Settings.from_mapping({**DEFAULTS, **T100, **(a_extra or {})})
    sb = Settings.from_mapping({**DEFAULTS, **T100, **(b_extra or {})})
    la.drying = Drying("drying", a_share) if a_share else Drying()
    lb.drying = Drying("drying", b_share) if b_share else Drying()
    cores = {id(la): core(), id(lb): core()} if core else {}

    def read(logic, t):
        return cores[id(logic)].probes(t, logic.phase) if core else probes

    la.start(0, MODE_TIMED, sa, *read(la, 0))
    lb.start(0, MODE_TIMED, sb, *read(lb, 0))
    phases = {id(la): [], id(lb): []}
    began = {id(la): (la.phase, 0), id(lb): (lb.phase, 0)}
    for t in range(1, seconds + 1):
        step_pair(t, Unit(la, MODE_TIMED, sa, *read(la, t)), Unit(lb, MODE_TIMED, sb, *read(lb, t)), rule)
        for logic in (la, lb):
            phase, since = began[id(logic)]
            if logic.phase != phase:
                if phase in (PHASE_EXHAUST, PHASE_INTAKE):
                    phases[id(logic)].append((phase, t - since, logic.last_reason))
                began[id(logic)] = (logic.phase, t)
            # never the same way at once
            assert not (la.phase == lb.phase and la.phase in (PHASE_EXHAUST, PHASE_INTAKE))
    # skip pairing up, and the first intake, which measures against the exhaust pairing up cut short
    return phases[id(la)][2:], phases[id(lb)][2:], la, lb


def lengths(phases, phase):
    return {seconds for p, seconds, _reason in phases if p == phase}


def test_drying_rests_the_unit_taking_air_in_while_the_other_finishes() -> None:
    """A dries (intakes at half the exhaust); B's exhausts still run their full 100 s."""
    a, b, _la, _lb = breathe_pair(SYNC_EITHER, 3000, a_share=50)
    assert lengths(a, PHASE_EXHAUST) == lengths(b, PHASE_EXHAUST) == {100}
    # one drying decision for the pair: B, with no humidity sensor of its own, leans too
    assert lengths(a, PHASE_INTAKE) == lengths(b, PHASE_INTAKE) == {50}
    assert {reason for p, _s, reason in a + b if p == PHASE_INTAKE} == {"drying"}


def test_resting_unit_is_paused_before_the_others_phase() -> None:
    la, lb = BreathingLogic(), BreathingLogic()
    s = Settings.from_mapping({**DEFAULTS, **T100})
    la.drying = Drying("drying", 50)
    la.start(0, MODE_TIMED, s, 20.0, 5.0)
    lb.start(0, MODE_TIMED, s, 20.0, 5.0)
    for t in range(1, 261):  # pair up at 2; B's exhausts and A's run 100 s, the intakes 50 s
        step_pair(t, Unit(la, MODE_TIMED, s, 20.0, 5.0), Unit(lb, MODE_TIMED, s, 20.0, 5.0), SYNC_EITHER)
    # A takes air in from 204 and rests from 254, while B exhausts until 304
    assert (la.phase, la.next_phase, lb.phase) == (PHASE_PAUSE, PHASE_EXHAUST, PHASE_EXHAUST)
    assert resting(la, lb) and not resting(lb, la)


def test_the_drying_spiral_is_gone() -> None:
    """Both drying at 60 % with 1600 s breaths used to shrink every phase to the minimum."""
    extra = {"timed_exhaust_seconds": 1600, "timed_intake_seconds": 1600, "min_phase_seconds": 300}
    a, b, _la, _lb = breathe_pair(SYNC_EITHER, 6 * 3600, extra, extra, a_share=60, b_share=60)
    assert lengths(a, PHASE_EXHAUST) == lengths(b, PHASE_EXHAUST) == {1600}
    assert lengths(a, PHASE_INTAKE) == lengths(b, PHASE_INTAKE) == {960}


@pytest.mark.parametrize("rule", [SYNC_EITHER, SYNC_BOTH, SYNC_AVERAGE, SYNC_LEAD_THIS, SYNC_LEAD_PARTNER])
def test_phase_limit_rests_the_limited_unit_under_every_rule(rule: str) -> None:
    """A keeps its intakes to half its exhausts; B's phases are never cut short for it."""
    a, b, _la, _lb = breathe_pair(rule, 3000, a_extra={"phase_limit": "limited_intake", "phase_limit_percent": 50})
    assert lengths(a, PHASE_INTAKE) == {50}
    assert lengths(b, PHASE_EXHAUST) == lengths(a, PHASE_EXHAUST) == lengths(b, PHASE_INTAKE) == {100}
    assert {reason for p, _s, reason in a if p == PHASE_INTAKE} == {"phase_limit"}


def test_limited_exhaust_rests_and_the_pair_stays_steady() -> None:
    a, b, _la, _lb = breathe_pair(SYNC_EITHER, 3000, a_extra={"phase_limit": "limited_exhaust", "phase_limit_percent": 50})
    assert lengths(a, PHASE_EXHAUST) == {50}
    assert lengths(a, PHASE_INTAKE) == lengths(b, PHASE_INTAKE) == lengths(b, PHASE_EXHAUST) == {100}


def test_contradictory_limits_on_both_units_still_cannot_shrink_the_breaths() -> None:
    """Limited exhaust and drying on both: a limit never measures against a limited phase."""
    extra = {"phase_limit": "limited_exhaust", "phase_limit_percent": 50}
    a, b, _la, _lb = breathe_pair(SYNC_EITHER, 6000, extra, extra, a_share=50, b_share=50)
    seen = [seconds for _p, seconds, _r in a[4:] + b[4:]]
    assert min(seen) >= 50  # nothing shrinks towards the 5 s minimum


def test_a_safety_stop_on_the_running_unit_switches_while_the_other_rests() -> None:
    la, lb = BreathingLogic(), BreathingLogic()
    s = Settings.from_mapping({**DEFAULTS, **T100})
    sa = Settings.from_mapping({**DEFAULTS, **T100, "phase_limit": "limited_exhaust", "phase_limit_percent": 50})
    la.start(0, MODE_TIMED, sa, 20.0, 5.0)
    lb.start(0, MODE_TIMED, s, 20.0, 5.0)
    for t in range(1, 160):  # A's exhaust (with B taking air in) is limited to 50 s: A rests from 155
        step_pair(t, Unit(la, MODE_TIMED, sa, 20.0, 5.0), Unit(lb, MODE_TIMED, s, 20.0, 5.0), SYNC_LEAD_THIS)
    assert resting(la, lb) and lb.phase == PHASE_INTAKE
    # B's supply air drops 5 °C below the room: the pair switches at once
    assert step_pair(160, Unit(la, MODE_TIMED, sa, 20.0, 5.0), Unit(lb, MODE_TIMED, s, 15.0, 5.0), SYNC_LEAD_THIS)
    assert (la.last_reason, lb.last_reason) == ("phase_limit", "supply_drop")
    step_pair(161, Unit(la, MODE_TIMED, sa, 20.0, 5.0), Unit(lb, MODE_TIMED, s, 20.0, 5.0), SYNC_LEAD_THIS)
    assert (la.phase, lb.phase) == (PHASE_INTAKE, PHASE_EXHAUST)


class Store(Core):
    """A core that fills up during a 100 s exhaust, so the intake after it runs for a while before draining it."""

    CHARGE = 0.03


@pytest.mark.parametrize("rule", [SYNC_EITHER, SYNC_BOTH, SYNC_AVERAGE, SYNC_LEAD_THIS, SYNC_LEAD_PARTNER])
def test_an_intake_that_drains_its_core_rests_and_the_breaths_hold(rule: str) -> None:
    """Cutting the other unit's exhaust to a drained intake stored less heat in its core, so its next intake
    drained sooner, and the breaths shrank to Minimum phase (5 s). Now the drained unit rests instead."""
    a, b, _la, _lb = breathe_pair(rule, 3000, core=Store)
    assert lengths(a, PHASE_EXHAUST) == lengths(b, PHASE_EXHAUST) == {100}
    intakes = [(seconds, reason) for p, seconds, reason in a + b if p == PHASE_INTAKE]
    assert {reason for _s, reason in intakes} == {"supply_drop"}
    assert min(seconds for seconds, _r in intakes) >= 30


@pytest.mark.parametrize("rule", [SYNC_EITHER, SYNC_BOTH, SYNC_AVERAGE, SYNC_LEAD_THIS, SYNC_LEAD_PARTNER])
def test_a_drained_intake_lets_the_other_finish_its_frost_protected_exhaust(rule: str) -> None:
    """In the cold B must exhaust at least as long as its last intake (200 s); A's supply drop at 20 s must not cut it."""
    from custom_components.recuperator.ambient import Ambient

    s = Settings.from_mapping({**DEFAULTS, "min_phase_seconds": 20, "max_phase_seconds": 600, "cold_intake_max_seconds": 600})
    la, lb = BreathingLogic(), BreathingLogic()
    for logic, length in ((la, 100), (lb, 200)):
        logic.ambient = Ambient(outdoor_temperature=-10.0, room_temperature=20.0)
        logic.last_exhaust_seconds = logic.last_intake_seconds = length
        logic.outdoor_estimate, logic.basement_estimate = -10.0, 20.0
    la._begin(PHASE_INTAKE, 0, MODE_AUTOMATIC, s, 19.0, -10.0)
    lb._begin(PHASE_EXHAUST, 0, MODE_AUTOMATIC, s, 20.0, -9.0)
    for t in range(1, 400):
        a_inside = 19.0 - 29 * 0.4 * min(t / 100, 1)  # A's supply air falls below 17 °C before 20 s
        b_outside = -9.0 + 29 * min(t / 40, 1)  # B has recovered by 40 s
        step_pair(t, Unit(la, MODE_AUTOMATIC, s, a_inside, -10.0), Unit(lb, MODE_AUTOMATIC, s, 20.0, b_outside), rule)
        if lb.phase != PHASE_EXHAUST:
            break
    assert t == 200 and lb.last_reason == "recovered"
    assert la.last_reason == "supply_drop"


def test_partner_drying_stops_counting_once_alone() -> None:
    _a, _b, la, lb = breathe_pair(SYNC_EITHER, 300, a_share=50)
    assert lb.partner_intake_share == 50
    s = Settings.from_mapping({**DEFAULTS, **T100})
    lb.step(301, MODE_TIMED, s, 20.0, 5.0)
    assert lb.partner_intake_share is None


def test_an_ordinary_pause_at_sync_up_is_not_a_rest() -> None:
    """A unit breathing alone, in its pause before an exhaust, when the other starts exhausting: they pair up."""
    la, lb = BreathingLogic(), BreathingLogic()
    s = Settings.from_mapping({**DEFAULTS, **T100, "pause_seconds": 5})
    la.start(0, MODE_TIMED, s, 20.0, 5.0)
    la.switch(50, "timed", 20.0, 5.0, PHASE_EXHAUST)  # A paused alone, before an exhaust
    lb.start(51, MODE_TIMED, s, 20.0, 5.0)  # B starts exhausting
    assert not resting(la, lb)
    step_pair(52, Unit(la, MODE_TIMED, s, 20.0, 5.0), Unit(lb, MODE_TIMED, s, 20.0, 5.0), SYNC_LEAD_THIS)
    assert (la.next_phase, lb.next_phase) == (PHASE_EXHAUST, PHASE_INTAKE)  # paired up: opposite ways
    for t in range(53, 58):
        step_pair(t, Unit(la, MODE_TIMED, s, 20.0, 5.0), Unit(lb, MODE_TIMED, s, 20.0, 5.0), SYNC_LEAD_THIS)
    assert (la.phase, lb.phase) == (PHASE_EXHAUST, PHASE_INTAKE)


def test_average_rule_lets_the_other_finish_its_frost_protected_exhaust() -> None:
    """In the cold B must exhaust at least as long as its last intake (200 s); A's 50 s limit must not cut it."""
    from custom_components.recuperator.ambient import Ambient

    cold = {"min_phase_seconds": 20, "max_phase_seconds": 600, "cold_intake_max_seconds": 600, "max_supply_drop": 20}
    sa = Settings.from_mapping({**DEFAULTS, **cold, "phase_limit": "limited_intake", "phase_limit_percent": 50})
    sb = Settings.from_mapping({**DEFAULTS, **cold})
    la, lb = BreathingLogic(), BreathingLogic()
    for logic, length in ((la, 100), (lb, 200)):
        logic.ambient = Ambient(outdoor_temperature=-10.0, room_temperature=20.0)
        logic.last_exhaust_seconds = logic.last_intake_seconds = length
        logic.outdoor_estimate, logic.basement_estimate = -10.0, 20.0
    la._begin(PHASE_INTAKE, 0, MODE_AUTOMATIC, sa, 19.0, -10.0)
    lb._begin(PHASE_EXHAUST, 0, MODE_AUTOMATIC, sb, 20.0, -9.0)
    for t in range(1, 400):
        a_inside = 19.0 - 29 * 0.4 * min(t / 100, 1)  # A recovers slowly
        b_outside = -9.0 + 29 * min(t / 40, 1)  # B has recovered by 40 s
        step_pair(t, Unit(la, MODE_AUTOMATIC, sa, a_inside, -10.0), Unit(lb, MODE_AUTOMATIC, sb, 20.0, b_outside), SYNC_AVERAGE)
        if lb.phase != PHASE_EXHAUST:
            break
    assert t == 200 and lb.last_reason == "recovered"
    assert la.last_reason == "phase_limit"


def test_a_followers_limit_keeps_its_reason_when_the_lead_switches_at_the_same_time() -> None:
    """Otherwise Limited exhaust would measure against an intake drying had cut short."""
    a, b, _la, lb = breathe_pair(
        SYNC_LEAD_THIS, 3000, a_extra={"timed_exhaust_seconds": 50},
        b_extra={"phase_limit": "limited_exhaust", "phase_limit_percent": 50}, a_share=50,
    )
    assert lengths(b, PHASE_INTAKE) == {50}
    assert {reason for p, _s, reason in b if p == PHASE_INTAKE} == {"drying"}
    assert lengths(b, PHASE_EXHAUST) == {100}  # Limited exhaust sits out after each drying-cut intake


def test_no_rest_while_the_others_passive_intake_depends_on_this_exhaust() -> None:
    """B's intake fan is off; A's exhaust pulls the air in. A's limit switches the pair instead of resting."""
    la, lb = BreathingLogic(), BreathingLogic()
    sa = Settings.from_mapping({**DEFAULTS, **T100, "phase_limit": "limited_exhaust", "phase_limit_percent": 50})
    sb = Settings.from_mapping({**DEFAULTS, **T100, "passive_intake": True})
    la.start(0, MODE_TIMED, sa, 20.0, 5.0)
    lb.start(0, MODE_TIMED, sb, 20.0, 5.0)
    for t in range(1, 3000):
        step_pair(t, Unit(la, MODE_TIMED, sa, 20.0, 5.0), Unit(lb, MODE_TIMED, sb, 20.0, 5.0), SYNC_EITHER)
        if lb.phase == PHASE_INTAKE and lb.passive:
            assert la.phase == PHASE_EXHAUST  # something always pulls B's air in
    assert not la.resting


def test_a_unit_protecting_its_cold_room_ignores_the_others_drying() -> None:
    la, lb = BreathingLogic(), BreathingLogic()
    s = Settings.from_mapping({**DEFAULTS, **T100})
    la.drying = Drying("drying", 50)
    lb.drying = Drying("room_too_cold")
    la.start(0, MODE_TIMED, s, 20.0, 5.0)
    lb.start(0, MODE_TIMED, s, 20.0, 5.0)
    step_pair(1, Unit(la, MODE_TIMED, s, 20.0, 5.0), Unit(lb, MODE_TIMED, s, 20.0, 5.0), SYNC_EITHER)
    assert lb.partner_intake_share is None
    lb.drying = Drying("below_target")
    step_pair(2, Unit(la, MODE_TIMED, s, 20.0, 5.0), Unit(lb, MODE_TIMED, s, 20.0, 5.0), SYNC_EITHER)
    assert lb.partner_intake_share == 50


# -- in Home Assistant ----------------------------------------------------------------

DATA_B = {
    "exhaust_switch": LINK_EXHAUST,
    "intake_switch": LINK_INTAKE,
    "inside_sensor": "sensor.inside_b",
    "outside_sensor": "sensor.outside_b",
}


def _entry(entry_id: str, title: str, data: dict, options: dict) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, entry_id=entry_id, title=title, data=data,
        options={**DEFAULTS, **options}, unique_id=unique_id_for(data), version=1, minor_version=2,
    )


def _synced(partner: str, rule: str = SYNC_EITHER) -> dict:
    return {"link_type": "synced", "link_entry": partner, "sync_rule": rule}


@pytest.fixture
async def pair(hass: HomeAssistant, fans, probes):
    """Alpha (short timed phases) synced with Beta (long ones), both loaded."""
    set_probe(hass, "sensor.inside_b", 20.0)
    set_probe(hass, "sensor.outside_b", 5.0)
    alpha = await setup_entry(hass, _entry("alpha", "Alpha", {**DATA, **_synced("beta")}, A))
    beta = await setup_entry(hass, _entry("beta", "Beta", {**DATA_B, **_synced("alpha")}, B))
    return alpha, beta


async def _breathe(hass: HomeAssistant, name: str, mode: str = "timed") -> None:
    await hass.services.async_call("select", "select_option", {"entity_id": f"select.{name}_mode", "option": mode}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": f"switch.{name}_breathing"}, blocking=True)


async def test_synced_pair_breathes_opposite_ways(hass: HomeAssistant, pair, freezer) -> None:
    await _breathe(hass, "alpha")
    await _breathe(hass, "beta")
    await tick(hass, freezer, 3)  # pair up: pause, then Alpha takes air in while Beta exhausts
    assert on(hass) == {INTAKE, LINK_EXHAUST}
    assert hass.states.get("sensor.alpha_linked_unit").state == "exhaust"
    assert hass.states.get("sensor.beta_linked_unit").state == "intake"
    assert hass.states.get("sensor.alpha_phase").attributes["synced"] is True

    await tick(hass, freezer, 20)  # Alpha's 20 s intake is done: both switch
    assert on(hass) == {EXHAUST, LINK_INTAKE}
    assert hass.states.get("sensor.beta_last_change_reason").state == "synced"


async def test_each_breathes_alone_when_the_other_is_off(hass: HomeAssistant, pair, freezer) -> None:
    await _breathe(hass, "beta")
    await tick(hass, freezer, 2)
    assert on(hass) == {LINK_EXHAUST}
    await tick(hass, freezer, 31)  # Beta's own 30 s exhaust, then its pause
    assert on(hass) == {LINK_INTAKE}
    assert hass.states.get("sensor.beta_phase").attributes["synced"] is False


async def test_link_page_links_both_and_mirrors_the_rule(hass: HomeAssistant, fans, probes) -> None:
    alpha = await setup_entry(hass, _entry("alpha", "Alpha", DATA, A))
    beta = await setup_entry(hass, _entry("beta", "Beta", DATA_B, B))
    flow = await hass.config_entries.options.async_init(alpha.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "link"})
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"link_type": "synced", "link_entry": ["beta"], "sync_rule": SYNC_LEAD_THIS}
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert alpha.data["link_entry"] == "beta" and alpha.data["sync_rule"] == SYNC_LEAD_THIS
    assert beta.data["link_entry"] == "alpha" and beta.data["sync_rule"] == SYNC_LEAD_PARTNER
    assert alpha.runtime_data.partner() is beta.runtime_data

    # a new rule alone applies without restarting either
    controller = alpha.runtime_data
    flow = await hass.config_entries.options.async_init(alpha.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "link"})
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {"link_type": "synced", "link_entry": ["beta"], "sync_rule": SYNC_BOTH}
    )
    await hass.async_block_till_done()
    assert alpha.runtime_data is controller
    assert beta.runtime_data.sync_rule == SYNC_BOTH

    # unlinking one end unlinks the other
    flow = await hass.config_entries.options.async_init(alpha.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "link"})
    await hass.config_entries.options.async_configure(flow["flow_id"], {"link_type": "none"})
    await hass.async_block_till_done()
    assert beta.data["link_type"] == "none" and "link_entry" not in beta.data


async def test_link_page_refuses_a_recuperator_linked_elsewhere(hass: HomeAssistant, fans, probes) -> None:
    alpha = await setup_entry(hass, _entry("alpha", "Alpha", DATA, A))
    await setup_entry(hass, _entry("beta", "Beta", {**DATA_B, "link_type": "intake_fan", "link_intake_switch": "input_boolean.x"}, B))
    flow = await hass.config_entries.options.async_init(alpha.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "link"})
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"link_type": "synced", "link_entry": ["beta"]})
    assert result["errors"] == {"link_entry": "link_entry_taken"}
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"link_type": "synced"})
    assert result["errors"] == {"link_entry": "link_entry_missing"}


async def test_removing_one_unlinks_the_other(hass: HomeAssistant, pair) -> None:
    alpha, beta = pair
    await hass.config_entries.async_remove(alpha.entry_id)
    await hass.async_block_till_done()
    assert beta.data["link_type"] == "none"
    assert beta.runtime_data.partner() is None


async def test_resting_unit_has_both_fans_off(hass: HomeAssistant, fans, probes, freezer) -> None:
    """Alpha's intake is limited to half its last exhaust: it rests while Beta exhausts on."""
    set_probe(hass, "sensor.inside_b", 20.0)
    set_probe(hass, "sensor.outside_b", 5.0)
    limited = {**A, "timed_exhaust_seconds": 20, "phase_limit": "limited_intake", "phase_limit_percent": 50}
    await setup_entry(hass, _entry("alpha", "Alpha", {**DATA, **_synced("beta")}, limited))
    await setup_entry(hass, _entry("beta", "Beta", {**DATA_B, **_synced("alpha")}, B))
    await _breathe(hass, "alpha")
    await _breathe(hass, "beta")
    for _ in range(200):
        await tick(hass, freezer)
        phase = hass.states.get("sensor.alpha_phase")
        if phase.attributes["resting"]:
            break
    else:
        pytest.fail("Alpha never rested")
    assert phase.state == "pause"
    assert on(hass) == {LINK_EXHAUST}  # Beta exhausts on; both of Alpha's fans are off
    assert hass.states.get("sensor.alpha_last_change_reason").state == "phase_limit"
    assert hass.states.get("sensor.beta_phase").attributes["resting"] is False
