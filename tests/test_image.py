"""The Diagram and replay Animation images, fetched as a dashboard would."""

from __future__ import annotations

from http import HTTPStatus

from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from .conftest import INSIDE, OUTSIDE, make_entry, set_probe, setup_entry


async def _fetch(hass: HomeAssistant, hass_client, entity_id: str) -> str:
    client = await hass_client()
    response = await client.get(f"/api/image_proxy/{entity_id}")
    assert response.status == HTTPStatus.OK
    assert response.content_type == "image/svg+xml"
    return await response.text()


async def test_diagram_shows_the_probes(hass: HomeAssistant, hass_client, fans) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    set_probe(hass, INSIDE, 20.0)
    set_probe(hass, OUTSIDE, 5.0)
    await setup_entry(hass, make_entry())
    svg = await _fetch(hass, hass_client, "image.breather_diagram")
    assert "68.0 °F" in svg and "41.0 °F" in svg
    assert "Stopped" in svg


async def test_replay_image_starts_with_a_placeholder(hass: HomeAssistant, hass_client, fans, probes, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path)
    await setup_entry(hass, make_entry())
    svg = await _fetch(hass, hass_client, "image.breather_replay_animation")
    assert "No replay yet" in svg
