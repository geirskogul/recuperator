"""The breathing cycle on its own (no Home Assistant needed)."""

from __future__ import annotations

from custom_components.recuperator.const import (
    PHASE_EXHAUST,
    PHASE_INTAKE,
    PHASE_PAUSE,
    REASON_COLD_LIMIT,
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
