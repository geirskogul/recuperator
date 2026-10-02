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
from custom_components.recuperator.logic import BreathingLogic, Settings
from custom_components.recuperator.sync import Unit, mirror, step_pair

from .conftest import DATA, EXHAUST, INTAKE, LINK_EXHAUST, LINK_INTAKE, set_probe, setup_entry
from .test_controller import on, tick

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


def test_safety_stop_on_a_follower_switches_the_pair() -> None:
    """Under 'this unit leads', the other unit's cold supply air still ends the phase."""
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
    assert (la.last_reason, lb.last_reason) == ("supply_drop", "synced")


def test_mirror() -> None:
    assert mirror(SYNC_LEAD_THIS) == SYNC_LEAD_PARTNER
    assert mirror(SYNC_LEAD_PARTNER) == SYNC_LEAD_THIS
    assert mirror(SYNC_BOTH) == SYNC_BOTH


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
        flow["flow_id"], {"link_type": "synced", "link_entry": "beta", "sync_rule": SYNC_LEAD_THIS}
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
        flow["flow_id"], {"link_type": "synced", "link_entry": "beta", "sync_rule": SYNC_BOTH}
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
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"link_type": "synced", "link_entry": "beta"})
    assert result["errors"] == {"link_entry": "link_entry_taken"}
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {"link_type": "synced"})
    assert result["errors"] == {"link_entry": "link_entry_missing"}


async def test_removing_one_unlinks_the_other(hass: HomeAssistant, pair) -> None:
    alpha, beta = pair
    await hass.config_entries.async_remove(alpha.entry_id)
    await hass.async_block_till_done()
    assert beta.data["link_type"] == "none"
    assert beta.runtime_data.partner() is None
