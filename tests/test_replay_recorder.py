"""The Create replay action end to end, with Home Assistant's recorder."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.recuperator.const import DOMAIN

from .conftest import INSIDE, OUTSIDE, make_entry, set_probe, setup_entry


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder must be set up before Home Assistant starts, so ask for it first."""
    yield


async def test_create_replay_from_history(hass: HomeAssistant, fans, tmp_path) -> None:
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


async def test_create_replay_for_a_picked_period(hass: HomeAssistant, fans, tmp_path) -> None:
    """A start and end (as the Replay card's date picker sends) replace the saved Hours, without being saved."""
    hass.config.config_dir = str(tmp_path)
    set_probe(hass, INSIDE, 20.0)
    set_probe(hass, OUTSIDE, 5.0)
    entry = await setup_entry(hass, make_entry())
    await async_wait_recording_done(hass)
    end = dt_util.now()
    start = end - timedelta(minutes=30)

    response = await hass.services.async_call(
        DOMAIN,
        "create_replay",
        {"start": start.isoformat(), "end": end.isoformat(), "playback_seconds": 10},
        blocking=True,
        return_response=True,
    )

    assert response["frames"] == 60  # at least 60, one per minute otherwise
    assert dt_util.parse_datetime(response["start"]) == start
    assert dt_util.parse_datetime(response["end"]) == end
    assert entry.options["replay_hours"] == 24  # not changed by a picked period
    assert "Inside" in entry.runtime_data.replay_svg.decode()


async def test_replay_of_a_synced_pair_with_a_gif(hass: HomeAssistant, fans, tmp_path) -> None:
    """Both units in one replay, and the GIF saved next to the SVG."""
    from .test_sync import A, B, DATA_B, _entry, _synced
    from .conftest import DATA

    hass.config.config_dir = str(tmp_path)
    for probe, value in ((INSIDE, 20.0), (OUTSIDE, 5.0), ("sensor.inside_b", 18.0), ("sensor.outside_b", -2.0)):
        set_probe(hass, probe, value)
    alpha = await setup_entry(hass, _entry("alpha", "Alpha", {**DATA, **_synced("beta")}, A))
    await setup_entry(hass, _entry("beta", "Beta", {**DATA_B, **_synced("alpha")}, B))
    await async_wait_recording_done(hass)
    assert hass.states.get("switch.alpha_replay_include_synced_recuperator").state == "on"

    response = await hass.services.async_call(
        DOMAIN, "create_replay",
        {"config_entry_id": "alpha", "hours": 0.25, "playback_seconds": 10, "gif": True},
        blocking=True, return_response=True,
    )

    assert response["linked"] == "Beta"
    assert response["gif_url"] == "/local/recuperator/alpha-replay.gif"
    svg = alpha.runtime_data.replay_svg.decode()
    assert ">Alpha</text>" in svg and ">Beta</text>" in svg and "-2.0 °C" in svg
    assert (tmp_path / "www" / "recuperator" / "alpha-replay.gif").read_bytes()[:6] == b"GIF89a"
    assert alpha.options["replay_gif"] is True  # remembered for the Create button

    # with the synced recuperator switched off, only this one
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.alpha_replay_include_synced_recuperator"}, blocking=True
    )
    response = await hass.services.async_call(
        DOMAIN, "create_replay", {"config_entry_id": "alpha", "gif": False}, blocking=True, return_response=True
    )
    assert response["linked"] is None and "gif_url" not in response
    assert ">Beta</text>" not in alpha.runtime_data.replay_svg.decode()
