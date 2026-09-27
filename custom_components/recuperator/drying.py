"""Drying: lean the breathing towards exhaust while the room is too humid.

With the fans running most of the time, the air moved per hour is roughly
fixed, so drying cannot breathe faster. What it can do is shift the balance:
shorter intakes than exhausts, sliding from no change at the target humidity
down to the drying intake share at target + drying range. Optionally, above
the exhaust-only humidity, it runs Exhaust only until the room is back a few
per cent below that.

It stands down when the room is colder than the drying room minimum (so drying
never chills the room in winter), and when the outdoor air holds as much water
as the room's (ventilating would only bring more in).

Pure Python (no Home Assistant), so it can be tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ambient import Ambient, outdoor_holds_more_water
from .const import (
    DRYING_ACTIVE,
    DRYING_BELOW_TARGET,
    DRYING_EXHAUST_ONLY,
    DRYING_HYSTERESIS,
    DRYING_NO_SENSOR,
    DRYING_OFF,
    DRYING_OUTDOOR_HUMID,
    DRYING_ROOM_COLD,
)


@dataclass(frozen=True)
class Drying:
    """What drying wants right now."""

    status: str = DRYING_OFF
    intake_share: float | None = None  # % of the last exhaust an intake may last; None = no limit
    exhaust_only: bool = False


OFF = Drying()


def intake_share(s, humidity: float) -> float:
    """100 % at the target humidity, sliding down to the drying intake share at target + range."""
    above = (humidity - s.target_humidity) / s.drying_band
    return 100 - (100 - s.drying_min_intake_share) * min(max(above, 0.0), 1.0)


def _wants_exhaust_only(s, humidity: float, was_exhaust_only: bool) -> bool:
    """Above the exhaust-only humidity, until the room is DRYING_HYSTERESIS below it again."""
    if not s.drying_exhaust_only:
        return False
    threshold = s.drying_exhaust_only_humidity - (DRYING_HYSTERESIS if was_exhaust_only else 0)
    return humidity >= threshold


def _stand_down(s, ambient: Ambient, room_temperature: float | None) -> str | None:
    """Why drying should not act now, or None if it may."""
    humidity = ambient.room_humidity
    if humidity is None:
        return DRYING_NO_SENSOR
    if humidity <= s.target_humidity:
        return DRYING_BELOW_TARGET
    if room_temperature is not None and room_temperature < s.drying_min_room_temperature:
        return DRYING_ROOM_COLD
    if outdoor_holds_more_water(
        room_temperature, humidity, ambient.outdoor_temperature, ambient.outdoor_humidity
    ):
        return DRYING_OUTDOOR_HUMID
    return None


def decide(s, ambient: Ambient, room_temperature: float | None, was_exhaust_only: bool) -> Drying:
    """What drying wants, given the settings, the readings and the room temperature (°C, best known)."""
    if not s.drying:
        return OFF
    if (reason := _stand_down(s, ambient, room_temperature)) is not None:
        return Drying(reason)
    humidity = ambient.room_humidity
    if _wants_exhaust_only(s, humidity, was_exhaust_only):
        return Drying(DRYING_EXHAUST_ONLY, exhaust_only=True)
    return Drying(DRYING_ACTIVE, intake_share(s, humidity))
