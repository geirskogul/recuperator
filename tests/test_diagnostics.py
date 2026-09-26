"""The diagnostics download."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant

from custom_components.recuperator.diagnostics import async_get_config_entry_diagnostics

from .conftest import EXHAUST, INSIDE, LINK_INTAKE, make_entry, set_probe, setup_entry


async def test_diagnostics(hass: HomeAssistant, fans, probes) -> None:
    set_probe(hass, INSIDE, 68, "°F")
    entry = await setup_entry(hass, make_entry({"link_type": "intake_fan", "link_intake_switch": LINK_INTAKE}))

    diag = await async_get_config_entry_diagnostics(hass, entry)

    json.dumps(diag)  # must be serialisable for the download
    assert diag["entry"]["version"] == "1.2"
    assert diag["entry"]["data"]["link_type"] == "intake_fan"
    assert diag["controller"]["enabled"] is False
    assert diag["controller"]["linked_state"] == "idle"
    assert diag["probes"]["inside"]["unit"] == "°F"
    assert round(diag["probes_celsius"]["inside"], 1) == 20.0
    assert set(diag["fans"]) == {EXHAUST, "input_boolean.in", LINK_INTAKE}
    assert diag["logic"]["phase"] == "stopped"
    assert diag["integration_version"]
