"""The live diagram and the animated replay (pure rendering)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.recuperator.diagram import format_temperature, parse_palette, render_svg
from custom_components.recuperator.replay import (
    CHART_BOTTOM,
    CHART_TOP,
    Frame,
    _held,
    _line_path,
    _runs,
    _scale,
    render_replay_svg,
    sample,
)


def frames(n: int = 10) -> list[Frame]:
    t0 = datetime(2026, 9, 25, 8, 0)
    phases = ["exhaust", "exhaust", "pause", "intake", "intake"]
    return [
        Frame(t0 + timedelta(minutes=i), 20.0 - i * 0.1, 5.0 + i, phases[i % len(phases)])
        for i in range(n)
    ]


def test_format_temperature() -> None:
    assert format_temperature(None) == "–"
    assert format_temperature(20.0) == "20.0 °C"
    assert format_temperature(20.0, "°F") == "68.0 °F"
    assert format_temperature(-40.0, "°F") == "-40.0 °F"


def test_diagram_shows_readings_in_display_unit() -> None:
    svg = render_svg(5.0, 20.0, "exhaust", "Breather", unit="°F")
    assert "68.0 °F" in svg and "41.0 °F" in svg
    assert "Exhaust" in svg


def test_diagram_readings_sit_in_boxes_in_the_pipe_ends() -> None:
    svg = render_svg(5.0, 20.0, "exhaust")
    # one half-opaque box per end, the reading centred in it
    assert svg.count('fill="#000" fill-opacity="0.5"') == 2
    assert '<text x="64" y="125" text-anchor="middle"' in svg
    assert '<text x="576" y="125" text-anchor="middle"' in svg
    assert 'y="206"' not in svg  # no longer under the pipe


def test_palette_round_trip_and_errors() -> None:
    assert parse_palette("-10 #2f6fdc; 20 #f1e344") == ((-10.0, "#2f6fdc"), (20.0, "#f1e344"))
    with pytest.raises(ValueError):
        parse_palette("20 #f1e344")
    with pytest.raises(ValueError):
        parse_palette("20 #f1e344; 10 #2f6fdc")


def test_runs_group_equal_values() -> None:
    assert _runs(["a", "a", "b", "a"]) == [(0, 1, "a"), (2, 2, "b"), (3, 3, "a")]


def test_replay_shows_temperatures_and_clock() -> None:
    svg = render_replay_svg(frames(), 30, "Breather")
    assert "20.0 °C" in svg  # first inside reading
    assert "14.0 °C" in svg  # last outside reading
    assert ">08:00<" in svg and ">08:09<" in svg
    assert svg.count("<animate") > 10


def test_replay_readings_sit_in_boxes() -> None:
    svg = render_replay_svg(frames(), 30)
    assert svg.count('fill="#000" fill-opacity="0.5"') == 2
    assert '<text x="64" y="135" text-anchor="middle"' in svg


def test_replay_draws_a_history_graph_with_a_sweeping_cursor() -> None:
    svg = render_replay_svg(frames(), 30)
    assert svg.count('fill="none" stroke=') == 2  # the inside and outside lines
    assert 'attributeName="transform" type="translate" from="0 0" to="540 0" dur="30s"' in svg
    assert svg.count('attributeName="cy"') == 2  # a dot riding each line
    assert ">Exhaust</text>" in svg and ">Intake</text>" in svg  # legend
    assert ">5 °C</text>" in svg and ">20 °C</text>" in svg  # grid labels, every 5 °C


def test_replay_graph_in_fahrenheit() -> None:
    svg = render_replay_svg(frames(), 30, unit="°F")
    assert "°F</text>" in svg


def test_scale_covers_the_values_with_round_steps() -> None:
    scale = _scale([-3.2, 21.4])
    assert (scale.low, scale.high, scale.step) == (-10, 30, 10)
    assert scale.y(scale.low) == CHART_BOTTOM and scale.y(scale.high) == CHART_TOP
    flat = _scale([20.0, 20.0])
    assert flat.high > flat.low
    assert _scale([]).high > _scale([]).low


def test_line_breaks_where_a_reading_is_missing() -> None:
    path = _line_path([1.0, None, 2.0, 3.0], _scale([1.0, 3.0]))
    assert path.count("M") == 2 and path.count("L") == 1


def test_held_fills_gaps_with_the_last_reading() -> None:
    assert _held([None, 1.0, None, 2.0]) == [1.0, 1.0, 1.0, 2.0]
    assert _held([None, None]) == [None, None]


def test_replay_without_readings_has_no_cursor_dots() -> None:
    f = [Frame(x.when, None, None, x.phase) for x in frames()]
    svg = render_replay_svg(f, 30)
    assert 'attributeName="cy"' not in svg


def test_replay_in_fahrenheit() -> None:
    svg = render_replay_svg(frames(), 30, "Breather", unit="°F")
    assert "68.0 °F" in svg
    assert "°C" not in svg


def test_replay_marks_missing_readings() -> None:
    f = frames()
    f[3] = Frame(f[3].when, None, f[3].outside, f[3].phase)
    assert ">–<" in render_replay_svg(f, 30)


def test_replay_needs_two_frames() -> None:
    with pytest.raises(ValueError):
        render_replay_svg(frames(1), 30)


def test_sample_is_a_step_function() -> None:
    t0 = datetime(2026, 1, 1)
    history = [(t0 + timedelta(minutes=1), "a"), (t0 + timedelta(minutes=3), "b")]
    times = [t0 + timedelta(minutes=m) for m in range(5)]
    assert sample(history, times) == [None, "a", "a", "b", "b"]
