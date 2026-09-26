"""Serves the Replay card (a dashboard card with Home Assistant's own date picker).

The card's script is loaded on every dashboard page, so the card can be added
without registering a resource by hand.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_FILE = "recuperator-replay-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILE}"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card's script and have the frontend load it (if the frontend is running)."""
    if "frontend" not in hass.config.components or hass.http is None:
        return
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    path = Path(__file__).parent / "frontend" / CARD_FILE
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), cache_headers=False)])
    # The version in the address makes browsers fetch the new script after an update.
    version = (await async_get_integration(hass, DOMAIN)).version
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    _LOGGER.debug("Replay card served at %s", CARD_URL)
