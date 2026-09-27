"""Humidity arithmetic and the drying decision (pure, no Home Assistant)."""

from __future__ import annotations

import pytest

from custom_components.recuperator.ambient import (
    Ambient,
    absolute_humidity,
    dew_point,
    outdoor_holds_more_water,
)
from custom_components.recuperator.drying import decide, intake_share
from custom_components.recuperator.logic import Settings


def test_absolute_humidity_and_dew_point() -> None:
    assert absolute_humidity(20, 50) == pytest.approx(8.6, abs=0.1)
    assert absolute_humidity(0, 100) == pytest.approx(4.8, abs=0.1)
    assert dew_point(20, 50) == pytest.approx(9.3, abs=0.1)


def test_cold_damp_outdoor_air_still_dries_a_warm_room() -> None:
    # 5 °C at 90 % holds far less water than 20 °C at 60 %
    assert not outdoor_holds_more_water(20, 60, 5, 90)
    # a muggy summer day brings water in
    assert outdoor_holds_more_water(18, 60, 25, 80)
    # unknown: assume ventilating helps
    assert not outdoor_holds_more_water(20, 60, None, 80)


def test_decision_is_by_vapour_pressure_not_grams_per_cubic_metre() -> None:
    """Cold air is denser: g/m³ alone would call this outdoor air wetter than it is."""
    # outdoor at 5 °C with a vapour pressure 2 % below the room's (20 °C, 35 %): it dries the room,
    # although per cubic metre it holds more water (cold air is denser)
    room_t, room_rh, out_t = 20.0, 35.0, 5.0
    out_rh = 35.0 * 23.37 / 8.72 * 0.98
    assert not outdoor_holds_more_water(room_t, room_rh, out_t, out_rh)
    assert absolute_humidity(out_t, out_rh) > absolute_humidity(room_t, room_rh)


def settings(**values) -> Settings:
    return Settings.from_mapping({"drying": True, **values})


def test_intake_share_slides_over_the_drying_range() -> None:
    s = settings(target_humidity=60, drying_band=10, drying_min_intake_share=50)
    assert intake_share(s, 60) == 100
    assert intake_share(s, 65) == 75
    assert intake_share(s, 90) == 50


@pytest.mark.parametrize(
    ("values", "ambient", "room_t", "status"),
    [
        ({"drying": False}, Ambient(room_humidity=80), 20, "off"),
        ({}, Ambient(), 20, "no_humidity_sensor"),
        ({}, Ambient(room_humidity=55), 20, "below_target"),
        ({}, Ambient(room_humidity=70), 5, "room_too_cold"),
        ({}, Ambient(room_humidity=65, outdoor_temperature=25, outdoor_humidity=85), 18, "outdoor_too_humid"),
        ({}, Ambient(room_humidity=70), 20, "drying"),
        ({"drying_exhaust_only": True}, Ambient(room_humidity=80), 20, "exhaust_only"),
        ({}, Ambient(room_humidity=80), 20, "drying"),  # exhaust only needs its switch
    ],
)
def test_drying_decisions(values, ambient, room_t, status) -> None:
    assert decide(settings(**values), ambient, room_t, False).status == status


def test_exhaust_only_has_hysteresis() -> None:
    s = settings(drying_exhaust_only=True, drying_exhaust_only_humidity=75)
    assert decide(s, Ambient(room_humidity=73), 20, False).status == "drying"
    assert decide(s, Ambient(room_humidity=73), 20, True).status == "exhaust_only"  # stays on until 72
    assert decide(s, Ambient(room_humidity=71.5), 20, True).status == "drying"
