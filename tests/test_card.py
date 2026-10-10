"""The Replay card's script, served by the integration."""

from __future__ import annotations

from http import HTTPStatus

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.recuperator.card import CARD_URL, _async_set_resource, async_register_card

from .conftest import LINK_EXHAUST, LINK_INTAKE, make_entry, setup_entry


async def test_card_is_served_and_loaded_by_the_frontend(hass: HomeAssistant, hass_client) -> None:
    assert await async_setup_component(hass, "http", {})
    hass.config.components.add("frontend")
    hass.data[DATA_EXTRA_MODULE_URL] = urls = set()

    # Before the test client starts the app: its router is frozen from then on
    # (Home Assistant's own server keeps it open, the test client does not).
    await async_register_card(hass)
    client = await hass_client()

    assert len(urls) == 1 and next(iter(urls)).startswith(f"{CARD_URL}?v=")
    response = await client.get(CARD_URL)
    assert response.status == HTTPStatus.OK
    assert "recuperator-replay-card" in await response.text()


async def test_no_card_without_the_frontend(hass: HomeAssistant) -> None:
    await async_register_card(hass)  # nothing to do, and no error


async def _setup_frontend(hass: HomeAssistant, lovelace: dict | None = None) -> None:
    """Enough of the frontend for the card: the web server, dashboards and the extra scripts."""
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "lovelace", {"lovelace": lovelace or {}})
    hass.config.components.add("frontend")
    hass.data[DATA_EXTRA_MODULE_URL] = set()


def _card_resources(hass: HomeAssistant) -> list[dict]:
    return [r for r in hass.data["lovelace"].resources.async_items() if r["url"].startswith(CARD_URL)]


async def test_card_is_a_dashboard_resource(hass: HomeAssistant) -> None:
    """Dashboards fetch their resources every time they open, unlike the cached page's scripts."""
    await _setup_frontend(hass)
    await async_register_card(hass)
    (resource,) = _card_resources(hass)
    assert resource["type"] == "module"
    assert resource["url"] == next(iter(hass.data[DATA_EXTRA_MODULE_URL]))  # the same address both ways
    await _async_set_resource(hass, resource["url"])  # as after a restart: still the one
    assert _card_resources(hass) == [resource]


async def test_card_resource_follows_updates(hass: HomeAssistant) -> None:
    await _setup_frontend(hass)
    resources = hass.data["lovelace"].resources
    await resources.async_get_info()
    other = await resources.async_create_item({"res_type": "module", "url": "/hacsfiles/other-card.js"})
    for _ in range(2):  # an old version, twice over
        await resources.async_create_item({"res_type": "js", "url": f"{CARD_URL}?v=0.4.0"})
    await async_register_card(hass)
    (resource,) = _card_resources(hass)
    assert resource["type"] == "module" and resource["url"] != f"{CARD_URL}?v=0.4.0"
    assert other in resources.async_items()  # other cards are left alone


async def test_yaml_resources_are_left_alone(hass: HomeAssistant) -> None:
    await _setup_frontend(hass, {"mode": "yaml", "resources": []})
    await async_register_card(hass)
    assert hass.data["lovelace"].resources.async_items() == []
    assert len(hass.data[DATA_EXTRA_MODULE_URL]) == 1  # loaded by the page's scripts instead


async def test_removing_the_last_recuperator_removes_the_resource(hass: HomeAssistant, fans, probes) -> None:
    await _setup_frontend(hass)
    first = await setup_entry(hass, make_entry())  # setting up the integration adds the card
    second = await setup_entry(
        hass, make_entry({"exhaust_switch": LINK_EXHAUST, "intake_switch": LINK_INTAKE}, title="Workshop")
    )
    assert len(_card_resources(hass)) == 1
    await hass.config_entries.async_remove(first.entry_id)
    assert len(_card_resources(hass)) == 1  # the workshop still has the card
    await hass.config_entries.async_remove(second.entry_id)
    assert _card_resources(hass) == []
