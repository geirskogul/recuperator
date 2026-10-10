"""Serves the Replay card (a dashboard card with Home Assistant's own date picker).

The card's script is loaded on every dashboard page, so the card can be added
without registering a resource by hand. It is loaded two ways:

* as a dashboard resource (on Settings, Dashboards, Resources), like the cards
  HACS installs. Dashboards fetch that list from Home Assistant every time they
  open, so the card is always found;
* as an extra script of the frontend's page, for dashboards whose resources
  are set in YAML, where the list cannot be changed. The page is kept by the
  frontend's offline cache, and the Companion app can show an old copy of it
  for a long time (an old copy without the card shows "Custom element doesn't
  exist"), which is why the resource comes first.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

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
    url = f"{CARD_URL}?v={version}"
    add_extra_js_url(hass, url)
    await _async_set_resource(hass, url)
    _LOGGER.debug("Replay card served at %s", CARD_URL)


async def async_remove_card_resource(hass: HomeAssistant) -> None:
    """Take the card off the dashboards' resources (when the last recuperator is removed)."""
    if (resources := await _async_resources(hass)) is None:
        return
    for item in _card_items(resources):
        await resources.async_delete_item(item["id"])


async def _async_set_resource(hass: HomeAssistant, url: str) -> None:
    """Put the card on the dashboards' resources once, at this version."""
    if (resources := await _async_resources(hass)) is None:
        return
    items = _card_items(resources)
    if not items:
        await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.info("Replay card added to the dashboards' resources")
        return
    first, *duplicates = items
    if first.get("url") != url or first.get("type") != "module":
        await resources.async_update_item(first["id"], {"res_type": "module", "url": url})
    for item in duplicates:
        await resources.async_delete_item(item["id"])


async def _async_resources(hass: HomeAssistant) -> Any | None:
    """The dashboards' resources, if they are kept by Home Assistant (not set in YAML), loaded."""
    try:
        from homeassistant.components.lovelace.resources import ResourceStorageCollection
    except ImportError:
        return None
    resources = getattr(hass.data.get("lovelace"), "resources", None)
    if not isinstance(resources, ResourceStorageCollection):
        return None
    await resources.async_get_info()  # loads them from storage, the first time
    return resources


def _card_items(resources: Any) -> list[dict[str, Any]]:
    """The resources that load this card, whatever their version."""
    return [item for item in resources.async_items() if str(item.get("url", "")).split("?")[0] == CARD_URL]
