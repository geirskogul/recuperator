"""Synced groups in Home Assistant: one big recuperator against two small ones, linking them up, and removal."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.recuperator import sync as sync_module
from custom_components.recuperator.config_flow import plan_link
from custom_components.recuperator.const import DEFAULTS, DOMAIN, SYNC_BOTH, SYNC_EITHER, SYNC_LEAD_THIS, unique_id_for

from .conftest import DATA, EXHAUST, INTAKE, LINK_EXHAUST, LINK_INTAKE, set_probe, setup_entry
from .test_controller import tick

EX3, IN3 = "input_boolean.ex3", "input_boolean.in3"
FANS = (EXHAUST, INTAKE, LINK_EXHAUST, LINK_INTAKE, EX3, IN3)
TIMED = {"timed_exhaust_seconds": 30, "timed_intake_seconds": 30, "pause_seconds": 1, "min_phase_seconds": 5}
DATA_S1 = {"exhaust_switch": LINK_EXHAUST, "intake_switch": LINK_INTAKE, "inside_sensor": "sensor.in_s1", "outside_sensor": "sensor.out_s1"}
DATA_S2 = {"exhaust_switch": EX3, "intake_switch": IN3, "inside_sensor": "sensor.in_s2", "outside_sensor": "sensor.out_s2"}


def on(hass: HomeAssistant) -> set[str]:
    return {e for e in FANS if hass.states.get(e).state == "on"}


@pytest.fixture
async def six_fans(hass: HomeAssistant) -> None:
    config = {"input_boolean": {name: {"name": name} for name in ("ex", "in", "lex", "lin", "ex3", "in3")}}
    assert await async_setup_component(hass, "input_boolean", config)
    await hass.async_block_till_done()
    for probe in ("sensor.inside", "sensor.in_s1", "sensor.in_s2"):
        set_probe(hass, probe, 20.0)
    for probe in ("sensor.outside", "sensor.out_s1", "sensor.out_s2"):
        set_probe(hass, probe, 5.0)


def _entry(entry_id: str, title: str, data: dict, options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, entry_id=entry_id, title=title, data=data,
        options={**DEFAULTS, **TIMED, **(options or {})}, unique_id=unique_id_for(data), version=1, minor_version=2,
    )


def _synced(*others: str, rule: str = SYNC_BOTH) -> dict:
    return {"link_type": "synced", "link_entry": others[0], "link_entries": list(others), "sync_rule": rule}


@pytest.fixture
async def trio(hass: HomeAssistant, six_fans):
    """Big against Small 1 and Small 2, all loaded."""
    big = await setup_entry(hass, _entry("big", "Big", {**DATA, **_synced("s1", "s2")}))
    s1 = await setup_entry(hass, _entry("s1", "Small 1", {**DATA_S1, **_synced("big")}))
    s2 = await setup_entry(hass, _entry("s2", "Small 2", {**DATA_S2, **_synced("big")}))
    return big, s1, s2


async def _breathe(hass: HomeAssistant, name: str, mode: str = "timed") -> None:
    await hass.services.async_call("select", "select_option", {"entity_id": f"select.{name}_mode", "option": mode}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": f"switch.{name}_breathing"}, blocking=True)


async def _off(hass: HomeAssistant, name: str) -> None:
    await hass.services.async_call("switch", "turn_off", {"entity_id": f"switch.{name}_breathing"}, blocking=True)


async def test_big_against_two_small_breathes_opposite_ways(hass: HomeAssistant, trio, freezer) -> None:
    for name in ("big", "small_1", "small_2"):
        await _breathe(hass, name)
    await tick(hass, freezer, 3)  # pair up: pause, then Big takes air in while both small units exhaust
    assert on(hass) == {INTAKE, LINK_EXHAUST, EX3}
    assert hass.states.get("sensor.big_linked_unit").state == "exhaust"
    assert hass.states.get("sensor.small_1_linked_unit").state == "intake"
    attributes = hass.states.get("sensor.big_linked_unit").attributes
    assert attributes["synced_with"] == "Small 1, Small 2" and attributes["in_step"] is True
    assert hass.states.get("sensor.small_2_linked_unit").attributes["same_side"] == "Small 1"
    await tick(hass, freezer, 31)
    assert on(hass) == {EXHAUST, LINK_INTAKE, IN3}
    for _ in range(120):
        await tick(hass, freezer)
        fans = on(hass)
        # never a unit's two fans at once, and the sides never the same way
        assert not ({EXHAUST, INTAKE} <= fans or {LINK_EXHAUST, LINK_INTAKE} <= fans or {EX3, IN3} <= fans)
        assert not (EXHAUST in fans and (LINK_EXHAUST in fans or EX3 in fans))
        assert not (INTAKE in fans and (LINK_INTAKE in fans or IN3 in fans))


async def test_one_step_a_second_for_the_whole_group(hass: HomeAssistant, trio, freezer) -> None:
    for name in ("big", "small_1", "small_2"):
        await _breathe(hass, name)
    with patch("custom_components.recuperator.controller.step_sides", wraps=sync_module.step_sides) as step:
        await tick(hass, freezer, 10)
        assert step.call_count == 10
        await _off(hass, "big")  # the leader leaves: Small 1 leads the two small ones
        step.reset_mock()
        await tick(hass, freezer, 10)
        assert step.call_count == 10


async def test_a_small_unit_off_leaves_big_against_the_other(hass: HomeAssistant, trio, freezer) -> None:
    for name in ("big", "small_1", "small_2"):
        await _breathe(hass, name)
    await tick(hass, freezer, 3)
    await _off(hass, "small_2")
    await tick(hass, freezer, 2)
    assert on(hass) == {INTAKE, LINK_EXHAUST}
    assert hass.states.get("sensor.big_phase").attributes["synced"] is True
    assert hass.states.get("sensor.small_2_phase").attributes["synced"] is False


async def test_with_big_off_the_two_small_ones_pair_up(hass: HomeAssistant, trio, freezer) -> None:
    for name in ("big", "small_1", "small_2"):
        await _breathe(hass, name)
    await tick(hass, freezer, 3)
    await _off(hass, "big")
    await tick(hass, freezer, 3)
    fans = on(hass)
    assert fans in ({LINK_EXHAUST, IN3}, {LINK_INTAKE, EX3})  # opposite ways
    assert hass.states.get("sensor.small_1_phase").attributes["synced"] is True
    await _breathe(hass, "big")  # back: the three line up again, once
    await tick(hass, freezer, 4)
    fans = on(hass)
    assert fans in ({EXHAUST, LINK_INTAKE, IN3}, {INTAKE, LINK_EXHAUST, EX3})


async def test_a_resting_small_unit_has_both_fans_off(hass: HomeAssistant, six_fans, freezer) -> None:
    """Small 1's intake is limited to half its last exhaust: it rests while Small 2 takes air in on."""
    limited = {"phase_limit": "limited_intake", "phase_limit_percent": 50}
    await setup_entry(hass, _entry("big", "Big", {**DATA, **_synced("s1", "s2")}))
    await setup_entry(hass, _entry("s1", "Small 1", {**DATA_S1, **_synced("big")}, limited))
    await setup_entry(hass, _entry("s2", "Small 2", {**DATA_S2, **_synced("big")}))
    for name in ("big", "small_1", "small_2"):
        await _breathe(hass, name)
    for _ in range(300):
        await tick(hass, freezer)
        phase = hass.states.get("sensor.small_1_phase")
        if phase.attributes["resting"]:
            break
    else:
        pytest.fail("Small 1 never rested")
    assert on(hass) == {EXHAUST, IN3}
    assert hass.states.get("sensor.small_2_phase").attributes["resting"] is False


# -- the Linked unit page ------------------------------------------------------------------


async def _link(hass: HomeAssistant, entry_id: str, user_input: dict) -> dict:
    flow = await hass.config_entries.options.async_init(entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "link"})
    result = await hass.config_entries.options.async_configure(flow["flow_id"], user_input)
    await hass.async_block_till_done()
    return result


async def test_big_picks_two_small_ones(hass: HomeAssistant, six_fans) -> None:
    big = await setup_entry(hass, _entry("big", "Big", DATA))
    s1 = await setup_entry(hass, _entry("s1", "Small 1", DATA_S1))
    s2 = await setup_entry(hass, _entry("s2", "Small 2", DATA_S2))
    result = await _link(hass, "big", {"link_type": "synced", "link_entry": ["s1", "s2"], "sync_rule": SYNC_BOTH})
    assert result["type"] == "create_entry"
    assert big.data["link_entries"] == ["s1", "s2"] and big.data["link_entry"] == "s1"
    for small in (s1, s2):
        assert small.data["link_entries"] == ["big"] and small.data["sync_rule"] == SYNC_BOTH
    assert big.runtime_data.sides() == (["big"], ["s1", "s2"])
    assert s2.runtime_data.sides() == (["s1", "s2"], ["big"])

    # a new rule alone restarts nobody
    controllers = [e.runtime_data for e in (big, s1, s2)]
    await _link(hass, "s2", {"link_type": "synced", "link_entry": ["big"], "sync_rule": SYNC_EITHER})
    assert [e.runtime_data for e in (big, s1, s2)] == controllers
    assert {e.data["sync_rule"] for e in (big, s1, s2)} == {SYNC_EITHER}

    # a lead rule is for pairs only
    result = await _link(hass, "big", {"link_type": "synced", "link_entry": ["s1", "s2"], "sync_rule": SYNC_LEAD_THIS})
    assert result["errors"] == {"sync_rule": "sync_rule_pair_only"}

    # unpicking Small 2 leaves a pair; Small 2 breathes on its own, and only it restarts besides the two
    result = await _link(hass, "big", {"link_type": "synced", "link_entry": ["s1"], "sync_rule": SYNC_BOTH})
    assert big.data["link_entries"] == ["s1"] and s1.data["link_entries"] == ["big"]
    assert s2.data["link_type"] == "none" and "link_entries" not in s2.data


async def test_the_units_left_behind_keep_their_rule(hass: HomeAssistant, trio) -> None:
    big, s1, s2 = trio
    await _link(hass, "s2", {"link_type": "none"})
    assert s2.data["link_type"] == "none"
    assert big.data["link_entries"] == ["s1"] and s1.data["link_entries"] == ["big"]
    assert big.data["sync_rule"] == s1.data["sync_rule"] == SYNC_BOTH


async def test_a_unit_joins_the_side_opposite_its_pick(hass: HomeAssistant, six_fans) -> None:
    big = await setup_entry(hass, _entry("big", "Big", {**DATA, **_synced("s1")}))
    s1 = await setup_entry(hass, _entry("s1", "Small 1", {**DATA_S1, **_synced("big")}))
    s2 = await setup_entry(hass, _entry("s2", "Small 2", DATA_S2))
    await _link(hass, "s2", {"link_type": "synced", "link_entry": ["big"], "sync_rule": SYNC_BOTH})
    assert big.data["link_entries"] == ["s1", "s2"]
    assert s1.data["link_entries"] == s2.data["link_entries"] == ["big"]
    # picking units from both sides of a group is refused
    s3 = await setup_entry(hass, _entry("s3", "Spare", {**DATA, "exhaust_switch": "input_boolean.x", "intake_switch": "input_boolean.y"}))
    result = await _link(hass, "s3", {"link_type": "synced", "link_entry": ["big", "s1"]})
    assert result["errors"] == {"link_entry": "link_entry_both_sides"}
    # and from this unit's own side too
    result = await _link(hass, "s1", {"link_type": "synced", "link_entry": ["s2"]})
    assert result["errors"] == {"link_entry": "link_entry_same_side"}
    del s3


async def test_removing_a_small_unit_leaves_a_pair(hass: HomeAssistant, trio) -> None:
    big, s1, s2 = trio
    await hass.config_entries.async_remove(s2.entry_id)
    await hass.async_block_till_done()
    assert big.data["link_entries"] == ["s1"] and big.data["link_entry"] == "s1"
    assert big.runtime_data.sides() == (["big"], ["s1"])
    await hass.config_entries.async_remove(s1.entry_id)
    await hass.async_block_till_done()
    assert big.data["link_type"] == "none"


async def test_a_fan_inside_a_group_counts_as_in_use(hass: HomeAssistant, trio) -> None:
    """A switch group holding Small 1's exhaust fan cannot be another recuperator's fan."""
    hass.states.async_set("switch.small_fans", "off", {"entity_id": [LINK_EXHAUST, "switch.other"]})
    hass.states.async_set("switch.other", "off")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "name": "Fourth", "exhaust_switch": "switch.small_fans", "intake_switch": "switch.free",
        "inside_sensor": "sensor.in_s1", "outside_sensor": "sensor.out_s1",
    })
    assert result["errors"] == {"exhaust_switch": "fan_in_use"}
    # and a linked unit's group may not hold this recuperator's own fan
    hass.states.async_set("switch.mine", "off", {"entity_id": [EXHAUST]})
    result = await _link(hass, "big", {"link_type": "exhaust_fan", "link_exhaust_switch": "switch.mine"})
    assert result["errors"] == {"link_exhaust_switch": "link_fan_is_own"}


# -- plan_link, on its own -----------------------------------------------------------------

FREE = "none"
S = "synced"


@pytest.mark.parametrize(
    ("links", "kinds", "me", "picks", "changes", "error"),
    [
        # a new pair
        ({"a": [], "b": []}, {"a": FREE, "b": FREE}, "a", ["b"], {"a": ["b"], "b": ["a"]}, None),
        # one big against two small
        ({"a": [], "b": [], "c": []}, {"a": FREE, "b": FREE, "c": FREE}, "a", ["b", "c"],
         {"a": ["b", "c"], "b": ["a"], "c": ["a"]}, None),
        # a third joins a pair, opposite its pick
        ({"a": ["b"], "b": ["a"], "c": []}, {"a": S, "b": S, "c": FREE}, "c", ["a"],
         {"b": ["a"], "c": ["a"], "a": ["b", "c"]}, None),
        # two groups never merge
        ({"a": ["b"], "b": ["a"], "c": ["d"], "d": ["c"]}, {k: S for k in "abcd"}, "c", ["a"], {}, "link_entry_taken"),
        # picks from both sides
        ({"a": ["b"], "b": ["a"], "c": []}, {"a": S, "b": S, "c": FREE}, "c", ["a", "b"], {}, "link_entry_both_sides"),
        # a unit linked to something else
        ({"a": [], "b": []}, {"a": FREE, "b": "intake_fan"}, "a", ["b"], {}, "link_entry_taken"),
        # unpicking one: it is unlinked
        ({"a": ["b", "c"], "b": ["a"], "c": ["a"]}, {k: S for k in "abc"}, "a", ["b"], {"c": None, "a": ["b"], "b": ["a"]}, None),
        # leaving a three: the other two stay a pair
        ({"a": ["b", "c"], "b": ["a"], "c": ["a"]}, {k: S for k in "abc"}, "b", None, {"b": None, "a": ["c"], "c": ["a"]}, None),
        # leaving a pair dissolves it
        ({"a": ["b"], "b": ["a"]}, {k: S for k in "ab"}, "a", None, {"a": None, "b": None}, None),
        # five is too many
        ({k: [] for k in "abcde"}, {k: FREE for k in "abcde"}, "a", ["b", "c", "d", "e"], {}, "group_too_big"),
        # a half-saved link back to me counts as free
        ({"a": [], "b": ["a"]}, {"a": FREE, "b": S}, "a", ["b"], {"a": ["b"], "b": ["a"]}, None),
    ],
)
def test_plan_link(links, kinds, me, picks, changes, error) -> None:
    assert plan_link(links, kinds, me, picks) == (changes, error)


async def test_diagnostics_show_the_group(hass: HomeAssistant, trio, freezer) -> None:
    from custom_components.recuperator.diagnostics import async_get_config_entry_diagnostics

    big, s1, s2 = trio
    for name in ("big", "small_1", "small_2"):
        await _breathe(hass, name)
    await tick(hass, freezer, 3)
    controller = (await async_get_config_entry_diagnostics(hass, s2))["controller"]
    assert controller["link_entries"] == ["big"]
    assert controller["sides"] == (["s1", "s2"], ["big"])
    assert controller["group"] == {"leader": "Big", "leading_side": ["Big"], "other_side": ["Small 1", "Small 2"]}
    assert controller["synced"] is True


def test_no_draughts_anywhere() -> None:
    """The wording is "draft" (cold air coming in), never "draught"."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for path in [root / "README.md", *(root / "custom_components").rglob("*"), *(root / "tools").glob("*.py")]:
        if path.is_file() and path.suffix in (".md", ".py", ".json", ".yaml", ".js"):
            assert "draught" not in path.read_text(encoding="utf-8").lower(), path
