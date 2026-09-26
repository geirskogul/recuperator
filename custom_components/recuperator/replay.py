"""Animated replay of the diagram from recorded history ("watch the house breathe").

Builds a self-animating SVG (SMIL animation, no scripts), so it plays in any
browser and in a Picture Entity card: the pipe's gradient changes frame by frame,
the phase label and airflow arrow switch with each phase, and a marker moves
along a timeline between the start and end times.

Pure Python (no Home Assistant), so it can be tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

from .diagram import _PIPE, NEUTRAL, OUTLINE, PALETTE, TEXT, Palette, format_temperature, temperature_colour

STOPS = 16  # colour positions along the pipe
MAX_FRAMES = 1440


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
    """The two probe readings under the pipe ends, changing with the frames."""
    inside = [format_temperature(f.inside, unit) for f in frames]
    outside = [format_temperature(f.outside, unit) for f in frames]
    return _changing_text(inside, dur, f'x="20" y="216" font-size="15" fill="{TEXT}"') + _changing_text(
        outside, dur, f'x="620" y="216" text-anchor="end" font-size="15" fill="{TEXT}"'
    )


def _clock_text(frames: list[Frame], dur: float, fmt: str) -> str:
    """The time of the frame on screen, under the middle of the timeline."""
    times = [f.when.strftime(fmt) for f in frames]
    return _changing_text(times, dur, f'x="320" y="264" text-anchor="middle" font-size="13" font-weight="bold" fill="{TEXT}"')


def render_replay_svg(
    frames: list[Frame],
    playback_seconds: float = 60,
    title: str = "",
    palette: Palette = PALETTE,
    unit: str = "°C",
) -> str:
    """An animated SVG of the frames, looping every playback_seconds.

    Frame temperatures are in °C; they are shown in `unit` (°C or °F).
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
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 285" width="640" height="285" font-family="sans-serif">
<defs>
<linearGradient id="temp" x1="20" y1="0" x2="620" y2="0" gradientUnits="userSpaceOnUse">{_gradient_stops(frames, palette, kt, dur)}</linearGradient>
<marker id="head" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 Z" fill="{TEXT}"/></marker>
</defs>
<text x="20" y="22" font-size="13" fill="{TEXT}">{title_text}</text>
<text x="620" y="22" text-anchor="end" font-size="13" fill="{TEXT}">{escape(period)}</text>
<text x="620" y="40" text-anchor="end" font-size="11" fill="{TEXT}">{n} frames, {dur:g} s loop</text>
{_phase_labels_and_arrows(frames, kt, dur)}
<g transform="translate(0,10)">
<path d="{_PIPE}" fill="url(#temp)" stroke="{OUTLINE}" stroke-width="3" stroke-linejoin="round"/>
<text x="20" y="92" font-size="15" font-weight="bold" fill="{TEXT}">Inside</text>
<text x="620" y="92" text-anchor="end" font-size="15" font-weight="bold" fill="{TEXT}">Outside</text>
</g>
{_temperature_texts(frames, dur, unit)}
<line x1="40" y1="240" x2="600" y2="240" stroke="{OUTLINE}" stroke-width="2"/>
<circle cx="40" cy="240" r="6" fill="{TEXT}"><animate attributeName="cx" from="40" to="600" dur="{dur:g}s" repeatCount="indefinite"/></circle>
<text x="40" y="264" font-size="13" fill="{TEXT}">{escape(start.strftime(fmt))}</text>
<text x="600" y="264" text-anchor="end" font-size="13" fill="{TEXT}">{escape(end.strftime(fmt))}</text>
{_clock_text(frames, dur, fmt)}
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
