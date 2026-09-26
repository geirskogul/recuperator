"""Shared fixtures: a recuperator wired to four input_boolean fans and two probes."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.recuperator.const import DEFAULTS, DOMAIN, unique_id_for

EXHAUST = "input_boolean.ex"
INTAKE = "input_boolean.in"
LINK_EXHAUST = "input_boolean.lex"
LINK_INTAKE = "input_boolean.lin"
INSIDE = "sensor.inside"
OUTSIDE = "sensor.outside"

DATA = {
    "exhaust_switch": EXHAUST,
    "intake_switch": INTAKE,
    "inside_sensor": INSIDE,
    "outside_sensor": OUTSIDE,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load the integration from custom_components/."""
    yield


@pytest.fixture
async def fans(hass: HomeAssistant) -> None:
    """Four fans that really switch: the recuperator's two and a linked unit's two."""
    config = {"input_boolean": {name: {"name": name} for name in ("ex", "in", "lex", "lin")}}
    assert await async_setup_component(hass, "input_boolean", config)
    await hass.async_block_till_done()


def set_probe(hass: HomeAssistant, entity_id: str, value: float | str, unit: str = "°C") -> None:
    hass.states.async_set(entity_id, str(value), {"unit_of_measurement": unit, "device_class": "temperature"})


@pytest.fixture
def probes(hass: HomeAssistant) -> None:
    """A warm room and cool outdoor air."""
    set_probe(hass, INSIDE, 20.0)
    set_probe(hass, OUTSIDE, 5.0)


def make_entry(
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    title: str = "Breather",
    minor_version: int = 2,
) -> MockConfigEntry:
    data = {**DATA, **(data or {})}
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data=data,
        options={**DEFAULTS, **(options or {})},
        unique_id=unique_id_for(data),
        version=1,
        minor_version=minor_version,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def entry(hass: HomeAssistant, fans, probes) -> MockConfigEntry:
    """A loaded recuperator named Breather."""
    return await setup_entry(hass, make_entry())
