"""A live picture of the recuperator: the pipe, filled with a temperature gradient."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.image import ImageEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .diagram import render_svg
from .replay import PLACEHOLDER
from .entity import REPLAY_DEVICE, RecuperatorEntity

MIN_REDRAW = timedelta(seconds=10)  # probe changes redraw at most this often


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([
        RecuperatorDiagram(hass, entry.runtime_data, entry),
        RecuperatorReplay(hass, entry.runtime_data, entry),
    ])


class RecuperatorDiagram(RecuperatorEntity, ImageEntity):
    """Show it with a Picture Entity card. Redraws on phase changes, and on
    probe changes at most every 10 seconds (to keep the history database small)."""

    _attr_content_type = "image/svg+xml"
    _attr_icon = "mdi:pipe"

    def __init__(self, hass: HomeAssistant, controller, entry) -> None:
        RecuperatorEntity.__init__(self, controller, entry, "diagram")
        ImageEntity.__init__(self, hass)
        self._attr_image_last_updated = dt_util.utcnow()
        self._pending = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        c = self._controller
        self.async_on_remove(
            async_track_state_change_event(self.hass, [c.inside_sensor, c.outside_sensor], self._probe_changed)
        )

    @callback
    def _handle_update(self) -> None:
        """Controller update (phase change): redraw now."""
        self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_update()

    @callback
    def _probe_changed(self, _event: Event) -> None:
        if self._pending is not None:
            return
        wait = (self._attr_image_last_updated + MIN_REDRAW - dt_util.utcnow()).total_seconds()
        self._pending = async_call_later(self.hass, max(0.0, wait), self._redraw)

    @callback
    def _redraw(self, _now: datetime) -> None:
        self._pending = None
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._pending is not None:
            self._pending()
            self._pending = None
        await super().async_will_remove_from_hass()

    async def async_image(self) -> bytes | None:
        c = self._controller
        inside, outside = c.probes()
        return render_svg(
            outside, inside, c.logic.phase, self._entry.title, c.palette, c.logic.passive, c.display_unit
        ).encode()


class RecuperatorReplay(RecuperatorEntity, ImageEntity):
    """The last animated replay made with the Create replay action."""

    _attr_content_type = "image/svg+xml"
    _attr_icon = "mdi:play-box-outline"

    def __init__(self, hass: HomeAssistant, controller, entry) -> None:
        RecuperatorEntity.__init__(self, controller, entry, "replay", REPLAY_DEVICE)
        ImageEntity.__init__(self, hass)
        self._attr_image_last_updated = controller.replay_time or dt_util.utcnow()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._controller.async_add_replay_listener(self._new_replay))

    @callback
    def _handle_update(self) -> None:
        """Phase changes do not change a replay."""

    @callback
    def _new_replay(self) -> None:
        self._attr_image_last_updated = self._controller.replay_time
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        return self._controller.replay_svg or PLACEHOLDER.encode()
