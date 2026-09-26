"""The Create replay action end to end, with Home Assistant's recorder."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from homeassistant.core import HomeAssistant

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
