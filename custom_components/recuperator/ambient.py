"""The room and the outdoors: optional sensor readings, and humidity arithmetic.

Pure Python (no Home Assistant), so it can be tested directly.

Whether ventilating dries a room depends on how much water the two airs hold,
not on their relative humidity. Absolute humidity (g/m³) is the familiar
figure and is what the sensors show, but it changes when air is warmed or
cooled (the air expands or shrinks) without any water coming or going. The
vapour pressure does not, so the drying decision compares vapour pressures:
outdoor air with a lower vapour pressure than indoor air holds less water per
kilogram of air, and brings the room's humidity down once it has warmed up.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

# Magnus formula over water (Sonntag 1990): what humidity sensors report against.
_MAGNUS_A = 6.112  # hPa
_MAGNUS_B = 17.62
_MAGNUS_C = 243.12  # °C
_WATER_GAS_CONSTANT = 461.5  # J/(kg·K)


@dataclass(frozen=True)
class Ambient:
    """Readings from the optional room and outdoor sensors (°C and % RH); None if not set up or unavailable."""

    room_temperature: float | None = None
    outdoor_temperature: float | None = None
    room_humidity: float | None = None
    outdoor_humidity: float | None = None


def saturation_vapour_pressure(celsius: float) -> float:
    """The most water vapour air at this temperature can hold, as a pressure (hPa)."""
    return _MAGNUS_A * math.exp(_MAGNUS_B * celsius / (_MAGNUS_C + celsius))


def vapour_pressure(celsius: float, relative_humidity: float) -> float:
    """The water vapour pressure (hPa) of air at this temperature and relative humidity (%)."""
    return saturation_vapour_pressure(celsius) * relative_humidity / 100


def absolute_humidity(celsius: float, relative_humidity: float) -> float:
    """Grams of water per cubic metre of air."""
    pascals = vapour_pressure(celsius, relative_humidity) * 100
    return pascals / (_WATER_GAS_CONSTANT * (celsius + 273.15)) * 1000


def dew_point(celsius: float, relative_humidity: float) -> float:
    """The temperature (°C) at which this air would start to condense."""
    gamma = math.log(max(relative_humidity, 0.1) / 100) + _MAGNUS_B * celsius / (_MAGNUS_C + celsius)
    return _MAGNUS_C * gamma / (_MAGNUS_B - gamma)


def outdoor_holds_more_water(
    room_temperature: float | None,
    room_humidity: float | None,
    outdoor_temperature: float | None,
    outdoor_humidity: float | None,
) -> bool:
    """True if outdoor air holds at least as much water as the room's, so ventilating cannot dry it.

    False when any reading is missing: without them, ventilation is assumed to help.
    """
    if None in (room_temperature, room_humidity, outdoor_temperature, outdoor_humidity):
        return False
    return vapour_pressure(outdoor_temperature, outdoor_humidity) >= vapour_pressure(
        room_temperature, room_humidity
    )
