"""The Create replay action and the saved replay."""

from __future__ import annotations

import os

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.recuperator.const import DOMAIN

from .conftest import INSIDE, LINK_EXHAUST, LINK_INTAKE, OUTSIDE, make_entry, set_probe, setup_entry


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


async def test_create_replay_from_history(recorder_mock, hass: HomeAssistant, fans, tmp_path) -> None:
    """End to end: recorded probe states (one in °F) become a replay file and image."""
    hass.config.config_dir = str(tmp_path)
    set_probe(hass, INSIDE, 68, "°F")
    set_probe(hass, OUTSIDE, 5.0)
    entry = await setup_entry(hass, make_entry())
    await async_wait_recording_done(hass)

    response = await hass.services.async_call(
        DOMAIN, "create_replay", {"hours": 0.25, "playback_seconds": 10}, blocking=True, return_response=True
    )

    assert response["frames"] == 60
    assert response["url"] == "/local/recuperator/breather-replay.svg"
    svg = (tmp_path / "www" / "recuperator" / "breather-replay.svg").read_text()
    assert "20.0 °C" in svg  # the °F probe, converted
    assert "5.0 °C" in svg
    assert entry.runtime_data.replay_svg == svg.encode()
    assert entry.options["replay_hours"] == 0.25

    # The Create button makes a new one with the saved settings.
    before = entry.runtime_data.replay_time
    await hass.services.async_call("button", "press", {"entity_id": "button.breather_replay_create"}, blocking=True)
    assert entry.runtime_data.replay_time >= before
