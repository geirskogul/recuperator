"""The Replay card's script, served by the integration."""

from __future__ import annotations

from http import HTTPStatus

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.recuperator.card import CARD_URL, async_register_card


async def test_card_is_served_and_loaded_by_the_frontend(hass: HomeAssistant, hass_client) -> None:
    assert await async_setup_component(hass, "http", {})
    client = await hass_client()
    hass.config.components.add("frontend")
    hass.data[DATA_EXTRA_MODULE_URL] = urls = set()

    await async_register_card(hass)

    assert len(urls) == 1 and next(iter(urls)).startswith(f"{CARD_URL}?v=")
    response = await client.get(CARD_URL)
    assert response.status == HTTPStatus.OK
    assert "recuperator-replay-card" in await response.text()


async def test_no_card_without_the_frontend(hass: HomeAssistant) -> None:
    await async_register_card(hass)  # nothing to do, and no error
