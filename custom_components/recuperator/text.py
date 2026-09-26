"""Diagram colours as a text entity, so the scale can be tuned from the device page."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .diagram import palette_to_text
from .entity import RecuperatorEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([DiagramColours(entry.runtime_data, entry, "diagram_palette")])


class DiagramColours(RecuperatorEntity, TextEntity):
    """The colour scale on one line: "temperature #colour" pairs separated by ";".

    Example: "-40 #e3c6f5; 0 #4fa3ec; 20 #f1e344; 40 #a8161f".
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False  # Configure, Diagram colours is the friendlier way
    _attr_mode = TextMode.TEXT
    _attr_native_max = 255
    _attr_icon = "mdi:palette"

    @property
    def native_value(self) -> str:
        return palette_to_text(self._controller.palette).replace("\n", "; ")

    async def async_set_value(self, value: str) -> None:
        try:
            await self._controller.async_set_palette(value)
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_palette",
                translation_placeholders={"error": str(err)},
            ) from err
