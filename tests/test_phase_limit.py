"""The Phase limit select and share number on the device."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .conftest import make_entry, setup_entry


async def test_phase_limit_select_is_stored(hass: HomeAssistant, fans, probes) -> None:
    entry = await setup_entry(hass, make_entry())
    assert hass.states.get("select.breather_phase_limit").state == "off"
    assert hass.states.get("number.breather_phase_limit_share").state == "90.0"

    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.breather_phase_limit", "option": "limited_intake"}, blocking=True
    )
    await hass.async_block_till_done()

    assert entry.options["phase_limit"] == "limited_intake"
    assert entry.runtime_data.settings.phase_limit == "limited_intake"
    assert hass.states.get("select.breather_phase_limit").state == "limited_intake"
