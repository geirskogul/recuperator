"""The Create replay action and the saved replay."""

from __future__ import annotations

import os

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.recuperator.const import DOMAIN

from .conftest import LINK_EXHAUST, LINK_INTAKE, make_entry, setup_entry


async def test_several_recuperators_need_a_choice(hass: HomeAssistant, fans, probes) -> None:
    await setup_entry(hass, make_entry(title="One"))
    await setup_entry(
        hass, make_entry({"exhaust_switch": LINK_EXHAUST, "intake_switch": LINK_INTAKE}, title="Two")
    )
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "create_replay", {}, blocking=True, return_response=True)
    assert err.value.translation_key == "several_recuperators"
    assert err.value.translation_placeholders == {"names": "One, Two"}


async def test_unknown_recuperator(hass: HomeAssistant, entry) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "create_replay", {"config_entry_id": "nope"}, blocking=True, return_response=True
        )
    assert err.value.translation_key == "recuperator_not_found"


async def test_replay_needs_the_recorder(hass: HomeAssistant, entry) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "create_replay", {"hours": 2}, blocking=True, return_response=True)
    assert err.value.translation_key == "recorder_needed"
    assert entry.options["replay_hours"] == 2  # values given are remembered


async def test_saved_replay_is_shown_after_a_restart(hass: HomeAssistant, fans, probes, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path)
    folder = tmp_path / "www" / "recuperator"
    folder.mkdir(parents=True)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><text>saved</text></svg>'
    (folder / "breather-replay.svg").write_bytes(svg)
    os.utime(folder / "breather-replay.svg", (1_700_000_000, 1_700_000_000))

    entry = await setup_entry(hass, make_entry())

    assert entry.runtime_data.replay_svg == svg
    assert entry.runtime_data.replay_time.timestamp() == 1_700_000_000


async def test_placeholder_without_a_saved_replay(hass: HomeAssistant, fans, probes, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await setup_entry(hass, make_entry())
    assert entry.runtime_data.replay_svg is None



async def test_start_and_hours_are_exclusive(hass: HomeAssistant, entry) -> None:
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "create_replay", {"hours": 2, "start": "2026-09-25 08:00:00"}, blocking=True, return_response=True
        )


@pytest.mark.parametrize(
    ("start", "end", "key"),
    [
        ("2026-09-25 08:00:00", "2026-09-25 08:01:00", "period_too_short"),
        ("2026-09-25 08:00:00", "2026-09-25 07:00:00", "period_too_short"),
        ("2026-08-01 08:00:00", "2026-09-25 08:00:00", "period_too_long"),
    ],
)
async def test_start_to_end_must_be_a_sensible_period(
    hass: HomeAssistant, entry, start: str, end: str, key: str
) -> None:
    hass.config.components.add("recorder")  # checked before the period
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "create_replay", {"start": start, "end": end}, blocking=True, return_response=True
        )
    assert err.value.translation_key == key
