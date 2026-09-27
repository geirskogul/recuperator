"""The breathing cycle on its own (no Home Assistant needed)."""

from __future__ import annotations

import pytest

from custom_components.recuperator.const import (
    PHASE_EXHAUST,
    PHASE_INTAKE,
    PHASE_PAUSE,
    REASON_COLD_LIMIT,
    REASON_MAX_TIME,
    REASON_PHASE_LIMIT,
    REASON_RECOVERED,
    REASON_SUPPLY_DROP,
    REASON_TIMED,
    TIMED_BY_MODE,
    TIMED_SIMILAR,
)
from custom_components.recuperator.logic import BreathingLogic, Settings


def run(logic: BreathingLogic, mode: str, s: Settings, start: int, seconds: int, probes) -> list[tuple[int, str]]:
    """Step once a second; probes(t, phase) gives (inside, outside). Returns the phase changes."""
    changes = []
    for t in range(start, start + seconds):
        inside, outside = probes(t, logic.phase)
        if logic.step(t, mode, s, inside, outside):
            changes.append((t, logic.phase))
    return changes


def steady(inside: float, outside: float):
    return lambda _t, _phase: (inside, outside)


def test_timed_mode_uses_separate_exhaust_and_intake_lengths() -> None:
    s = Settings.from_mapping({"timed_exhaust_seconds": 30, "timed_intake_seconds": 90, "pause_seconds": 1})
    logic = BreathingLogic()
    logic.start(0, "timed", s, 20, 5)
    assert logic.timed_reason == TIMED_BY_MODE
    run(logic, "timed", s, 1, 300, steady(20, 5))
    assert logic.last_exhaust_seconds == 30
    assert logic.last_intake_seconds == 90
    assert logic.last_reason == REASON_TIMED


def test_legacy_timed_phase_seeds_both_lengths() -> None:
    s = Settings.from_mapping({"timed_phase_seconds": 45})
    assert s.timed_exhaust_seconds == 45
    assert s.timed_intake_seconds == 45
    assert s.timed_seconds(PHASE_INTAKE) == 45


def test_new_lengths_win_over_legacy() -> None:
    s = Settings.from_mapping({"timed_phase_seconds": 45, "timed_intake_seconds": 70})
    assert s.timed_exhaust_seconds == 45
    assert s.timed_intake_seconds == 70


def test_similar_temperatures_give_timed_phases() -> None:
    s = Settings.from_mapping({"similar_band": 2.0})
    logic = BreathingLogic()
    logic.start(0, "automatic", s, 20.0, 19.5)
    assert logic.timed_reason == TIMED_SIMILAR


def test_exhaust_ends_when_far_probe_recovers() -> None:
    """Exhaust: the outside probe warms towards the room; 80 % of the gap ends the phase."""
    s = Settings.from_mapping({"recovery_percent": 80, "min_phase_seconds": 5, "max_phase_seconds": 600})
    logic = BreathingLogic()
    logic.start(0, "automatic", s, 20.0, 5.0)

    def probes(t, _phase):
        return 20.0, min(20.0, 5.0 + 0.5 * t)  # far end warms 0.5 °C a second

    changes = run(logic, "automatic", s, 1, 60, probes)
    assert changes[0][1] == PHASE_PAUSE
    assert logic.last_reason == REASON_RECOVERED
    # 80 % of 15 °C = 12 °C, reached after 24 s
    assert logic.last_exhaust_seconds == 24


def test_intake_protects_the_room_from_cold_supply() -> None:
    s = Settings.from_mapping({"min_phase_seconds": 5, "max_supply_drop": 3, "pause_seconds": 0})
    logic = BreathingLogic()
    logic.basement_estimate, logic.outdoor_estimate = 20.0, 0.0
    logic.start(0, "automatic", s, 20.0, 0.0)
    logic._end(1, "test", 20.0, 0.0, next_phase=PHASE_INTAKE)
    logic.step(1, "automatic", s, 20.0, 0.0)
    assert logic.phase == PHASE_INTAKE

    def probes(t, _phase):
        return 20.0 - 0.2 * t, 0.0  # air entering the room gets colder

    run(logic, "automatic", s, 2, 60, probes)
    assert logic.last_reason == REASON_SUPPLY_DROP


def test_cold_weather_caps_the_intake() -> None:
    s = Settings.from_mapping(
        {"cold_threshold": -5, "cold_intake_max_seconds": 30, "max_phase_seconds": 600, "min_phase_seconds": 5,
         "max_supply_drop": 20}
    )
    logic = BreathingLogic()
    logic.basement_estimate, logic.outdoor_estimate = 20.0, -15.0
    logic.start(0, "automatic", s, 20.0, -15.0)
    logic._end(1, "test", 20.0, -15.0, next_phase=PHASE_INTAKE)
    run(logic, "automatic", s, 2, 120, steady(20.0, -15.0))
    assert logic.cold
    assert logic.last_intake_seconds == 30
    assert logic.last_reason == REASON_COLD_LIMIT


def test_exhaust_only_mode_stays_on_exhaust() -> None:
    s = Settings()
    logic = BreathingLogic()
    logic.start(0, "exhaust_only", s, 20, 5)
    run(logic, "exhaust_only", s, 1, 600, steady(20, 5))
    assert logic.phase == PHASE_EXHAUST


def test_intake_only_mode_starts_with_intake() -> None:
    logic = BreathingLogic()
    logic.start(0, "intake_only", Settings(), 20, 5)
    assert logic.phase == PHASE_INTAKE


def test_limited_intake_in_timed_mode() -> None:
    """Timed lengths 60/60: with limited intake at 50 %, each intake lasts half the exhaust."""
    s = Settings.from_mapping(
        {"timed_exhaust_seconds": 60, "timed_intake_seconds": 60, "phase_limit": "limited_intake", "phase_limit_percent": 50}
    )
    logic = BreathingLogic()
    logic.start(0, "timed", s, 20, 5)
    run(logic, "timed", s, 1, 300, steady(20, 5))
    assert logic.last_exhaust_seconds == 60
    assert logic.last_intake_seconds == 30
    assert logic.last_reason in (REASON_PHASE_LIMIT, REASON_TIMED)


def test_limited_intake_in_automatic_mode() -> None:
    """Probes that never recover: every phase would run to Maximum phase; the intake is cut to 90 % of the exhaust."""
    s = Settings.from_mapping(
        {"max_phase_seconds": 100, "min_phase_seconds": 10, "phase_limit": "limited_intake", "similar_band": 0}
    )
    logic = BreathingLogic()
    logic.start(0, "automatic", s, 20.0, 5.0)
    run(logic, "automatic", s, 1, 150, steady(20.0, 5.0))  # exhaust ends at max time, intake runs
    assert logic.last_exhaust_seconds == 100
    run(logic, "automatic", s, 151, 150, steady(20.0, 5.0))
    assert logic.last_intake_seconds == 90
    assert logic.last_reason in (REASON_PHASE_LIMIT, REASON_MAX_TIME)


def test_limited_exhaust_keeps_exhaust_shorter() -> None:
    s = Settings.from_mapping(
        {"timed_exhaust_seconds": 60, "timed_intake_seconds": 40, "phase_limit": "limited_exhaust", "phase_limit_percent": 50}
    )
    logic = BreathingLogic()
    logic.start(0, "timed", s, 20, 5)
    run(logic, "timed", s, 1, 300, steady(20, 5))
    assert logic.last_intake_seconds == 40
    assert logic.last_exhaust_seconds == 20  # after the first full-length exhaust


def test_phase_limit_never_below_minimum_phase() -> None:
    s = Settings.from_mapping(
        {"timed_exhaust_seconds": 20, "timed_intake_seconds": 60, "min_phase_seconds": 15,
         "phase_limit": "limited_intake", "phase_limit_percent": 10}
    )
    logic = BreathingLogic()
    logic.start(0, "timed", s, 20, 5)
    run(logic, "timed", s, 1, 200, steady(20, 5))
    assert logic.last_intake_seconds == 15  # 10 % of 20 s would be 2 s


def test_phase_limit_off_by_default() -> None:
    s = Settings.from_mapping({"timed_exhaust_seconds": 30, "timed_intake_seconds": 90})
    assert s.phase_limit == "off"
    logic = BreathingLogic()
    logic.start(0, "timed", s, 20, 5)
    run(logic, "timed", s, 1, 300, steady(20, 5))
    assert logic.last_intake_seconds == 90


def test_cold_exhaust_rule_wins_over_limited_exhaust() -> None:
    """Frost protection: in the cold the exhaust still runs at least as long as the last intake."""
    s = Settings.from_mapping(
        {"max_phase_seconds": 100, "min_phase_seconds": 10, "similar_band": 0,
         "phase_limit": "limited_exhaust", "phase_limit_percent": 50}
    )
    logic = BreathingLogic()
    logic.start(0, "automatic", s, 20.0, -10.0)
    run(logic, "automatic", s, 1, 400, steady(20.0, -10.0))
    assert logic.cold
    assert logic.last_exhaust_seconds >= logic.last_intake_seconds


def test_limited_intake_in_the_cold_does_not_shrink_the_phases() -> None:
    """Limited intake with the cold exhaust rule: the exhaust is not held to the shortened intake.

    Before 0.5.1 each exhaust was capped at the last intake + Cold exhaust extra, and each intake
    at a share of the exhaust, so the phases shrank breath by breath towards extra / (1 - share).
    """
    s = Settings.from_mapping(
        {"max_phase_seconds": 100, "min_phase_seconds": 10, "similar_band": 0, "cold_intake_max_seconds": 600,
         "phase_limit": "limited_intake", "phase_limit_percent": 50}
    )
    logic = BreathingLogic()
    logic.start(0, "automatic", s, 20.0, -10.0)
    run(logic, "automatic", s, 1, 2000, steady(20.0, -10.0))  # about 13 breaths
    assert logic.cold
    assert logic.last_exhaust_seconds == 100  # still the full Maximum phase
    assert logic.last_intake_seconds == 50
    assert logic.last_intake_limited


# -- heat recovery, room and outdoor sensors, drying ------------------------------------

from custom_components.recuperator.ambient import Ambient  # noqa: E402


def test_heat_recovery_is_the_supply_temperature_efficiency() -> None:
    """Room 20, outdoor 0; supply air at 15 for the whole intake: 75 % recovered."""
    s = Settings.from_mapping({"timed_exhaust_seconds": 30, "timed_intake_seconds": 30, "pause_seconds": 1})
    logic = BreathingLogic()
    logic.ambient = Ambient(room_temperature=20.0, outdoor_temperature=0.0)

    def probes(_t, phase):
        return (15.0, 0.0) if phase == PHASE_INTAKE else (20.0, 10.0)

    logic.start(0, "timed", s, 20.0, 10.0)
    run(logic, "timed", s, 1, 200, probes)
    assert logic.heat_recovery == pytest.approx(75, abs=2)  # the first second still reads room air
    assert logic.last_recovery_percent is None  # Core used: only for probe-driven phases


def test_heat_recovery_averages_over_the_intake() -> None:
    """Supply falls from 20 to 10 during a 40 s intake: the average (about 15) counts, not the end."""
    s = Settings.from_mapping({"timed_exhaust_seconds": 30, "timed_intake_seconds": 40, "max_supply_drop": 20})
    logic = BreathingLogic()
    logic.ambient = Ambient(room_temperature=20.0, outdoor_temperature=0.0)
    started = {}

    def probes(t, phase):
        if phase != PHASE_INTAKE:
            started.pop("intake", None)
            return 20.0, 10.0
        start = started.setdefault("intake", t)
        return 20.0 - (t - start) / 4, 0.0

    logic.start(0, "timed", s, 20.0, 10.0)
    run(logic, "timed", s, 1, 75, probes)
    assert logic.heat_recovery == pytest.approx(75, abs=4)


def test_heat_recovery_not_judged_when_room_and_outdoors_are_alike() -> None:
    s = Settings.from_mapping({"timed_exhaust_seconds": 30, "timed_intake_seconds": 30})
    logic = BreathingLogic()
    logic.ambient = Ambient(room_temperature=20.0, outdoor_temperature=19.0)
    logic.start(0, "timed", s, 20.0, 19.0)
    run(logic, "timed", s, 1, 200, steady(20.0, 19.0))
    assert logic.heat_recovery is None


def test_outdoor_sensor_decides_cold_weather() -> None:
    s = Settings.from_mapping({"cold_threshold": -5})
    logic = BreathingLogic()
    logic.ambient = Ambient(outdoor_temperature=-12.0)
    logic.start(0, "automatic", s, 20.0, 5.0)
    run(logic, "automatic", s, 1, 5, steady(20.0, 5.0))  # exhaust: the probes say nothing about outdoors
    assert logic.cold


def test_room_and_outdoor_sensors_decide_similar_temperatures() -> None:
    s = Settings.from_mapping({"similar_band": 2.0})
    logic = BreathingLogic()
    logic.ambient = Ambient(room_temperature=18.0, outdoor_temperature=17.0)
    logic.start(0, "automatic", s, 20.0, 5.0)  # probes far apart, but the air is alike
    assert logic.timed_reason == TIMED_SIMILAR


def test_drying_shortens_intakes() -> None:
    """Room at 70 % with a 60 % target and 10 % range: intakes get half the exhaust."""
    s = Settings.from_mapping(
        {"drying": True, "timed_exhaust_seconds": 60, "timed_intake_seconds": 60, "drying_min_intake_share": 50}
    )
    logic = BreathingLogic()
    logic.ambient = Ambient(room_humidity=70.0)
    logic.update_drying(s)
    assert logic.drying.status == "drying"
    logic.start(0, "timed", s, 20, 5)
    run(logic, "timed", s, 1, 200, steady(20, 5))
    assert logic.last_intake_seconds == 30
    assert logic.last_intake_limited


def test_drying_can_run_exhaust_only_and_hand_back() -> None:
    s = Settings.from_mapping({"drying": True, "drying_exhaust_only": True, "timed_exhaust_seconds": 30})
    logic = BreathingLogic()

    def humidity(value: float) -> None:
        logic.ambient = Ambient(room_humidity=value)
        logic.update_drying(s)

    humidity(55.0)  # below target: normal timed breathing
    logic.start(0, "timed", s, 20, 5)
    run(logic, "timed", s, 1, 40, steady(20, 5))
    assert logic.phase == PHASE_INTAKE

    humidity(85.0)  # very humid: the intake is ended and exhaust runs on
    run(logic, "timed", s, 41, 300, steady(20, 5))
    assert logic.phase == PHASE_EXHAUST
    assert logic.last_reason == "drying"

    humidity(55.0)  # dry again: breathing resumes
    changes = run(logic, "timed", s, 341, 100, steady(20, 5))
    assert PHASE_INTAKE in [phase for _t, phase in changes]
