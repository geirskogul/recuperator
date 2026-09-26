"""Adding, reconfiguring and configuring a recuperator."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.recuperator.const import DEFAULTS, DOMAIN

from .conftest import DATA, EXHAUST, INSIDE, INTAKE, LINK_EXHAUST, LINK_INTAKE, OUTSIDE, make_entry, setup_entry


async def _start_user_flow(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_user_flow_creates_entry(hass: HomeAssistant, fans, probes) -> None:
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Basement", **DATA})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Basement"
    assert result["data"] == DATA
    assert result["options"] == DEFAULTS
    assert result["result"].unique_id == f"{EXHAUST}|{INTAKE}"
    await hass.async_block_till_done()


async def test_user_flow_rejects_same_fan_twice(hass: HomeAssistant, fans, probes) -> None:
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Basement", **DATA, "intake_switch": EXHAUST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"intake_switch": "same_switch"}


async def test_user_flow_rejects_same_probe_twice(hass: HomeAssistant, fans, probes) -> None:
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Basement", **DATA, "outside_sensor": INSIDE}
    )
    assert result["errors"] == {"outside_sensor": "same_sensor"}


async def test_user_flow_rejects_fan_driven_by_a_linked_unit(hass: HomeAssistant, fans, probes) -> None:
    other = make_entry(
        {"exhaust_switch": "input_boolean.x1", "intake_switch": "input_boolean.x2",
         "link_type": "intake_fan", "link_intake_switch": LINK_INTAKE}
    )
    other.add_to_hass(hass)
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Basement", **DATA, "exhaust_switch": LINK_INTAKE}
    )
    assert result["errors"] == {"exhaust_switch": "fan_in_use"}


async def test_reconfigure_updates_wiring_and_unique_id(hass: HomeAssistant, entry) -> None:
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    new = {**DATA, "exhaust_switch": LINK_EXHAUST, "intake_switch": LINK_INTAKE}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data["exhaust_switch"] == LINK_EXHAUST
    assert entry.unique_id == f"{LINK_EXHAUST}|{LINK_INTAKE}"


async def _open_options(hass: HomeAssistant, entry, step: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"settings", "link", "replay", "colours"}
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})


async def test_settings_are_saved(hass: HomeAssistant, entry) -> None:
    result = await _open_options(hass, entry, "settings")
    assert result["step_id"] == "settings"
    assert "replay_hours" not in result["data_schema"].schema
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"timed_exhaust_seconds": 30, "timed_intake_seconds": 90}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["timed_exhaust_seconds"] == 30
    assert entry.options["timed_intake_seconds"] == 90


async def test_settings_reset_keeps_replay_settings(hass: HomeAssistant, fans, probes) -> None:
    entry = await setup_entry(hass, make_entry(options={"replay_hours": 6, "recovery_percent": 20}))
    result = await _open_options(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"reset_to_defaults": True})
    assert entry.options["recovery_percent"] == DEFAULTS["recovery_percent"]
    assert entry.options["replay_hours"] == 6


async def test_replay_settings_have_their_own_page(hass: HomeAssistant, entry) -> None:
    result = await _open_options(hass, entry, "replay")
    assert set(result["data_schema"].schema) == {"replay_hours", "replay_playback_seconds", "replay_frames"}
    await hass.config_entries.options.async_configure(result["flow_id"], {"replay_hours": 2})
    assert entry.options["replay_hours"] == 2


async def test_link_needs_the_fans_of_its_type(hass: HomeAssistant, entry) -> None:
    result = await _open_options(hass, entry, "link")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"link_type": "recuperator"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {
        "link_exhaust_switch": "link_fan_missing",
        "link_intake_switch": "link_fan_missing",
    }


async def test_link_refuses_own_fan(hass: HomeAssistant, entry) -> None:
    result = await _open_options(hass, entry, "link")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"link_type": "intake_fan", "link_intake_switch": INTAKE}
    )
    assert result["errors"] == {"link_intake_switch": "link_fan_is_own"}


async def test_link_saves_only_the_fans_its_type_uses(hass: HomeAssistant, entry) -> None:
    result = await _open_options(hass, entry, "link")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"link_type": "intake_fan", "link_intake_switch": LINK_INTAKE, "link_exhaust_switch": LINK_EXHAUST},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.data["link_type"] == "intake_fan"
    assert entry.data["link_intake_switch"] == LINK_INTAKE
    assert "link_exhaust_switch" not in entry.data
    # reloaded with the link: the Linked unit sensor now exists
    assert hass.states.get("sensor.breather_linked_unit") is not None


async def test_colours_page_saves_a_palette(hass: HomeAssistant, entry) -> None:
    result = await _open_options(hass, entry, "colours")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "stop_01_temperature": 0,
            "stop_01_colour": [0, 0, 255],
            "stop_02_temperature": 30,
            "stop_02_colour": [255, 0, 0],
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["diagram_palette"] == "0 #0000ff\n30 #ff0000"


async def test_colours_page_needs_two_stops(hass: HomeAssistant, entry) -> None:
    result = await _open_options(hass, entry, "colours")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"stop_01_temperature": 0, "stop_01_colour": [0, 0, 255]}
    )
    assert result["errors"] == {"base": "too_few_stops"}
