"""Animated replay of the diagram from recorded history ("watch the house breathe").

Builds a self-animating SVG (SMIL animation, no scripts), so it plays in any
browser and in a Picture Entity card: the pipe's gradient and the readings in its
ends change frame by frame, the phase label and airflow arrow switch with each
phase, and under the pipe a history graph of the two probes has a cursor
sweeping across it in step.

Pure Python (no Home Assistant), so it can be tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
import math

from .diagram import (
    _PIPE,
    BADGE_Y,
    FAHRENHEIT,
    INSIDE_BADGE_X,
    NEUTRAL,
    OUTLINE,
    OUTSIDE_BADGE_X,
    PALETTE,
    TEXT,
    Palette,
    badge_box,
    badge_text_attrs,
    format_temperature,
    temperature_colour,
)

STOPS = 16  # colour positions along the pipe
MAX_FRAMES = 1440

# Layout (viewBox 640 wide): the pipe on top, the history graph under it.
PIPE_DY = 10  # the pipe is drawn this much lower than in the live diagram
CHART_X0, CHART_X1 = 56, 596
CHART_TOP, CHART_BOTTOM = 232, 332
BAND_TOP, BAND_HEIGHT = 340, 10  # the phase strip under the graph
LEGEND_Y = 370
HEIGHT = 380

INSIDE_COLOUR = "#f28e2b"
OUTSIDE_COLOUR = "#3b8ede"
# Exhaust carries inside air out, intake brings outside air in.
PHASE_COLOURS = {"exhaust": INSIDE_COLOUR, "intake": OUTSIDE_COLOUR}
NICE_STEPS = (0.5, 1, 2, 5, 10, 20, 50)


@dataclass(frozen=True)
class Frame:
    """One moment of history."""

    when: datetime
    inside: float | None
    outside: float | None
    phase: str


def _key_times(n: int) -> str:
    return ";".join(f"{i / (n - 1):.5f}" for i in range(n))


def _discrete(values: list[str], key_times: str, dur: float, attr: str = "opacity") -> str:
    return (
        f'<animate attributeName="{attr}" values="{";".join(values)}" keyTimes="{key_times}" '
        f'dur="{dur:g}s" calcMode="discrete" repeatCount="indefinite"/>'
    )


def _runs(values: list[str]) -> list[tuple[int, int, str]]:
    """Consecutive equal values grouped as (first index, last index, value)."""
    runs: list[tuple[int, int, str]] = []
    for i, value in enumerate(values):
        if runs and runs[-1][2] == value:
            runs[-1] = (runs[-1][0], i, value)
        else:
            runs.append((i, i, value))
    return runs


def _run_visibility(first: int, last: int, n: int, dur: float) -> str:
    """An animation showing an element from frame `first` until after frame `last`.

    Frame i is on screen from i/(n-1) until (i+1)/(n-1) of the loop. One element
    per run of equal values keeps the SVG small, however many frames there are.
    """
    if first == 0 and last == n - 1:
        return ""  # shown the whole time
    on, off = first / (n - 1), (last + 1) / (n - 1)
    if first == 0:
        values, times = "1;0", f"0;{off:.5f}"
    elif last == n - 1:
        values, times = "0;1", f"0;{on:.5f}"
    else:
        values, times = "0;1;0", f"0;{on:.5f};{off:.5f}"
    return (
        f'<animate attributeName="opacity" values="{values}" keyTimes="{times}" '
        f'dur="{dur:g}s" calcMode="discrete" repeatCount="indefinite"/>'
    )


def _changing_text(values: list[str], dur: float, attrs: str) -> str:
    """A text whose content changes with the frames (one element per run)."""
    n = len(values)
    out = []
    for first, last, value in _runs(values):
        anim = _run_visibility(first, last, n, dur)
        opacity = ' opacity="0"' if first > 0 else ""
        out.append(f"<text {attrs}{opacity}>{escape(value)}{anim}</text>")
    return "".join(out)


def _gradient_stops(frames: list[Frame], palette: Palette, key_times: str, dur: float) -> str:
    """Every colour stop along the pipe animates through its colour in each frame."""
    stops = []
    for k in range(STOPS):
        pos = k / (STOPS - 1)
        colours = [
            NEUTRAL
            if f.inside is None or f.outside is None
            else temperature_colour(f.inside + (f.outside - f.inside) * pos, palette)
            for f in frames
        ]
        stops.append(
            f'<stop offset="{pos:.3f}" stop-color="{colours[0]}">'
            f'<animate attributeName="stop-color" values="{";".join(colours)}" keyTimes="{key_times}" '
            f'dur="{dur:g}s" calcMode="linear" repeatCount="indefinite"/></stop>'
        )
    return "".join(stops)


def _phase_labels_and_arrows(frames: list[Frame], key_times: str, dur: float) -> str:
    """Phase labels and airflow arrows, each shown only while its phase is on."""

    def visible(test) -> list[str]:
        return ["1" if test(f) else "0" for f in frames]

    labels = []
    for phase, text in (("exhaust", "Exhaust"), ("intake", "Intake"), ("pause", "Pause"), ("stopped", "Stopped")):
        labels.append(
            f'<text x="320" y="22" text-anchor="middle" font-size="16" font-weight="bold" fill="{TEXT}" opacity="0">'
            f"{text}{_discrete(visible(lambda f, p=phase: f.phase == p), key_times, dur)}</text>"
        )
    # exhaust flows inside -> outside (left to right), intake the other way
    arrows = (
        f'<line x1="180" y1="40" x2="460" y2="40" stroke="{TEXT}" stroke-width="4" stroke-linecap="round" '
        f'marker-end="url(#head)" opacity="0">{_discrete(visible(lambda f: f.phase == "exhaust"), key_times, dur)}</line>'
        f'<line x1="460" y1="40" x2="180" y2="40" stroke="{TEXT}" stroke-width="4" stroke-linecap="round" '
        f'marker-end="url(#head)" opacity="0">{_discrete(visible(lambda f: f.phase == "intake"), key_times, dur)}</line>'
    )
    return "".join(labels) + arrows


def _temperature_texts(frames: list[Frame], dur: float, unit: str) -> str:
    """The two probe readings in the pipe's ends, changing with the frames."""
    y = BADGE_Y + PIPE_DY
    inside = [format_temperature(f.inside, unit) for f in frames]
    outside = [format_temperature(f.outside, unit) for f in frames]
    return (
        badge_box(INSIDE_BADGE_X, y)
        + badge_box(OUTSIDE_BADGE_X, y)
        + _changing_text(inside, dur, badge_text_attrs(INSIDE_BADGE_X, y))
        + _changing_text(outside, dur, badge_text_attrs(OUTSIDE_BADGE_X, y))
    )


def _display(celsius: float | None, unit: str) -> float | None:
    """A °C value in the display unit, for the chart."""
    if celsius is None:
        return None
    return celsius * 9 / 5 + 32 if unit == FAHRENHEIT else celsius


@dataclass(frozen=True)
class Scale:
    """The chart's temperature axis: bottom and top values and the grid step."""

    low: float
    high: float
    step: float

    def y(self, value: float) -> float:
        """The height in the SVG of a temperature (display unit)."""
        return CHART_BOTTOM - (value - self.low) / (self.high - self.low) * (CHART_BOTTOM - CHART_TOP)

    def ticks(self) -> list[float]:
        count = round((self.high - self.low) / self.step)
        return [self.low + self.step * i for i in range(count + 1)]


def _scale(values: list[float]) -> Scale:
    """An axis with round grid lines covering all the values, four steps or fewer."""
    if not values:
        return Scale(0.0, 1.0, 1.0)
    low, high = min(values), max(values)
    step = next((s for s in NICE_STEPS if (high - low) / s <= 4), NICE_STEPS[-1])
    bottom = math.floor(low / step) * step
    top = math.ceil(high / step) * step
    if top <= bottom:
        top = bottom + step
    return Scale(bottom, top, step)


def _x(i: int, n: int) -> float:
    """The chart's horizontal position of frame i (the frames are evenly spaced in time)."""
    return CHART_X0 + (CHART_X1 - CHART_X0) * i / (n - 1)


def _line_path(values: list[float | None], scale: Scale) -> str:
    """A path through the values, broken where a value is missing."""
    n = len(values)
    parts, pen_down = [], False
    for i, value in enumerate(values):
        if value is None:
            pen_down = False
            continue
        parts.append(f"{'L' if pen_down else 'M'}{_x(i, n):.1f},{scale.y(value):.1f}")
        pen_down = True
    return " ".join(parts)


def _grid(scale: Scale, unit: str) -> str:
    """Horizontal grid lines with their temperatures, left of the chart."""
    out = []
    for value in scale.ticks():
        y = scale.y(value)
        out.append(
            f'<line x1="{CHART_X0}" y1="{y:.1f}" x2="{CHART_X1}" y2="{y:.1f}" stroke="{OUTLINE}" '
            f'stroke-opacity="0.35" stroke-width="1"/>'
            f'<text x="{CHART_X0 - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="{TEXT}">'
            f"{value:g} {escape(unit)}</text>"
        )
    return "".join(out)


def _phase_band(frames: list[Frame]) -> str:
    """A strip under the chart, coloured while exhausting or taking air in (like a history timeline)."""
    n = len(frames)
    out = [
        f'<rect x="{CHART_X0}" y="{BAND_TOP}" width="{CHART_X1 - CHART_X0}" height="{BAND_HEIGHT}" '
        f'fill="{OUTLINE}" fill-opacity="0.2"/>'
    ]
    for first, last, phase in _runs([f.phase for f in frames]):
        colour = PHASE_COLOURS.get(phase)
        if colour is None:
            continue
        x0 = _x(first, n)
        x1 = _x(last + 1, n) if last + 1 < n else CHART_X1
        out.append(
            f'<rect x="{x0:.1f}" y="{BAND_TOP}" width="{max(x1 - x0, 0.5):.1f}" height="{BAND_HEIGHT}" '
            f'fill="{colour}"/>'
        )
    return "".join(out)


def _held(values: list[float | None]) -> list[float | None]:
    """Missing values replaced by the last known one (the first known one at the start)."""
    known = next((v for v in values if v is not None), None)
    out = []
    for value in values:
        known = value if value is not None else known
        out.append(known)
    return out


def _cursor_dot(values: list[float | None], scale: Scale, colour: str, key_times: str, dur: float) -> str:
    """A dot riding a line under the sweeping cursor; hidden while the value is missing."""
    held = _held(values)
    if held[0] is None:
        return ""  # never measured
    heights = ";".join(f"{scale.y(v):.1f}" for v in held)
    shown = [("1" if v is not None else "0") for v in values]
    hide = _discrete(shown, key_times, dur) if "0" in shown else ""
    return (
        f'<circle cx="{CHART_X0}" cy="{scale.y(held[0]):.1f}" r="4.5" fill="{colour}" stroke="#fff" stroke-width="1.5">'
        f'<animate attributeName="cy" values="{heights}" keyTimes="{key_times}" dur="{dur:g}s" '
        f'calcMode="linear" repeatCount="indefinite"/>{hide}</circle>'
    )


def _sweep(
    frames: list[Frame], scale: Scale, series: tuple[list, list], key_times: str, dur: float, fmt: str
) -> str:
    """The cursor sweeping across the chart in step with the animation, with the time on screen above it.

    The frames are evenly spaced in time, so a steady sweep from the first to
    the last frame's position is always over the frame being shown.
    """
    inside, outside = series
    times = [f.when.strftime(fmt) for f in frames]
    clock = _changing_text(
        times, dur, f'x="{CHART_X0}" y="{CHART_TOP - 8}" text-anchor="middle" font-size="12" font-weight="bold" fill="{TEXT}"'
    )
    return (
        "<g>"
        f'<animateTransform attributeName="transform" type="translate" from="0 0" to="{CHART_X1 - CHART_X0} 0" '
        f'dur="{dur:g}s" repeatCount="indefinite"/>'
        f'<line x1="{CHART_X0}" y1="{CHART_TOP - 4}" x2="{CHART_X0}" y2="{BAND_TOP + BAND_HEIGHT}" '
        f'stroke="{TEXT}" stroke-width="1.5"/>'
        f"{_cursor_dot(inside, scale, INSIDE_COLOUR, key_times, dur)}"
        f"{_cursor_dot(outside, scale, OUTSIDE_COLOUR, key_times, dur)}"
        f"{clock}</g>"
    )


def _legend() -> str:
    """What the lines and the phase strip's colours mean, under the chart."""
    items = (
        ("line", INSIDE_COLOUR, "Inside"),
        ("line", OUTSIDE_COLOUR, "Outside"),
        ("bar", PHASE_COLOURS["exhaust"], "Exhaust"),
        ("bar", PHASE_COLOURS["intake"], "Intake"),
    )
    out, x = [], 212
    for kind, colour, text in items:
        if kind == "line":
            out.append(f'<line x1="{x}" y1="{LEGEND_Y - 4}" x2="{x + 16}" y2="{LEGEND_Y - 4}" stroke="{colour}" stroke-width="3"/>')
        else:
            out.append(f'<rect x="{x}" y="{LEGEND_Y - 9}" width="16" height="10" fill="{colour}"/>')
        out.append(f'<text x="{x + 21}" y="{LEGEND_Y}" font-size="11" fill="{TEXT}">{text}</text>')
        x += 26 + 7 * len(text)
    return "".join(out)


def _chart(frames: list[Frame], unit: str, key_times: str, dur: float, fmt: str) -> str:
    """The inside and outside temperatures over the period, like a history graph, with the sweeping cursor."""
    inside = [_display(f.inside, unit) for f in frames]
    outside = [_display(f.outside, unit) for f in frames]
    scale = _scale([v for v in inside + outside if v is not None])
    start, end = frames[0].when, frames[-1].when
    return (
        _grid(scale, unit)
        + _phase_band(frames)
        + f'<path d="{_line_path(inside, scale)}" fill="none" stroke="{INSIDE_COLOUR}" stroke-width="2" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
        + f'<path d="{_line_path(outside, scale)}" fill="none" stroke="{OUTSIDE_COLOUR}" stroke-width="2" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
        + _sweep(frames, scale, (inside, outside), key_times, dur, fmt)
        + f'<text x="{CHART_X0}" y="{LEGEND_Y}" font-size="12" fill="{TEXT}">{escape(start.strftime(fmt))}</text>'
        + f'<text x="{CHART_X1}" y="{LEGEND_Y}" text-anchor="end" font-size="12" fill="{TEXT}">{escape(end.strftime(fmt))}</text>'
        + _legend()
    )


def render_replay_svg(
    frames: list[Frame],
    playback_seconds: float = 60,
    title: str = "",
    palette: Palette = PALETTE,
    unit: str = "°C",
) -> str:
    """An animated SVG of the frames, looping every playback_seconds.

    The pipe on top, and under it a history graph of the two probes with a
    cursor sweeping across in step with the pipe. Frame temperatures are in °C;
    they are shown in `unit` (°C or °F).
    """
    if len(frames) < 2:
        raise ValueError("at least two frames are needed")
    n = len(frames)
    kt = _key_times(n)
    dur = float(playback_seconds)

    start, end = frames[0].when, frames[-1].when
    same_day = start.date() == end.date()
    fmt = "%H:%M" if same_day else "%d %b %H:%M"
    title_text = escape(title or "Replay")
    period = f"{start.strftime('%d %b %Y')}" if same_day else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 {HEIGHT}" width="640" height="{HEIGHT}" font-family="sans-serif">
<defs>
<linearGradient id="temp" x1="20" y1="0" x2="620" y2="0" gradientUnits="userSpaceOnUse">{_gradient_stops(frames, palette, kt, dur)}</linearGradient>
<marker id="head" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 Z" fill="{TEXT}"/></marker>
</defs>
<text x="20" y="22" font-size="13" fill="{TEXT}">{title_text}</text>
<text x="620" y="22" text-anchor="end" font-size="13" fill="{TEXT}">{escape(period)}</text>
<text x="620" y="40" text-anchor="end" font-size="11" fill="{TEXT}">{n} frames, {dur:g} s loop</text>
{_phase_labels_and_arrows(frames, kt, dur)}
<g transform="translate(0,{PIPE_DY})">
<path d="{_PIPE}" fill="url(#temp)" stroke="{OUTLINE}" stroke-width="3" stroke-linejoin="round"/>
<text x="20" y="92" font-size="15" font-weight="bold" fill="{TEXT}">Inside</text>
<text x="620" y="92" text-anchor="end" font-size="15" font-weight="bold" fill="{TEXT}">Outside</text>
</g>
{_temperature_texts(frames, dur, unit)}
{_chart(frames, unit, kt, dur, fmt)}
</svg>"""


def sample(history: list[tuple[datetime, object]], times: list[datetime]) -> list[object]:
    """The last value at or before each time (a step function); None before the first."""
    out: list[object] = []
    i, current = 0, None
    for t in times:
        while i < len(history) and history[i][0] <= t:
            current = history[i][1]
            i += 1
        out.append(current)
    return out


def frame_times(start: datetime, end: datetime, count: int) -> list[datetime]:
    """count evenly spaced times from start to end."""
    count = max(2, min(MAX_FRAMES, count))
    step = (end - start) / (count - 1)
    return [start + step * i for i in range(count)]


PLACEHOLDER = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 120" width="640" height="120" font-family="sans-serif">
<text x="320" y="55" text-anchor="middle" font-size="16" fill="{TEXT}">No replay yet</text>
<text x="320" y="80" text-anchor="middle" font-size="13" fill="{TEXT}">Press Create replay (or run the action "Recuperator: Create replay") to make one.</text>
</svg>"""
