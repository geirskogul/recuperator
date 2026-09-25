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

from .diagram import _PIPE, NEUTRAL, OUTLINE, PALETTE, TEXT, Palette, temperature_colour

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


def render_replay_svg(
    frames: list[Frame],
    playback_seconds: float = 60,
    title: str = "",
    palette: Palette = PALETTE,
) -> str:
    """An animated SVG of the frames, looping every playback_seconds."""
    if len(frames) < 2:
        raise ValueError("at least two frames are needed")
    n = len(frames)
    kt = _key_times(n)
    dur = float(playback_seconds)

    # The gradient: every colour stop animates through its colour in each frame.
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
            f'<animate attributeName="stop-color" values="{";".join(colours)}" keyTimes="{kt}" '
            f'dur="{dur:g}s" calcMode="linear" repeatCount="indefinite"/></stop>'
        )

    # Phase labels and arrows: shown only while their phase is on.
    def visible(test) -> list[str]:
        return ["1" if test(f) else "0" for f in frames]

    labels = []
    for phase, text in (("exhaust", "Exhaust"), ("intake", "Intake"), ("pause", "Pause"), ("stopped", "Stopped")):
        labels.append(
            f'<text x="320" y="22" text-anchor="middle" font-size="16" font-weight="bold" fill="{TEXT}" opacity="0">'
            f"{text}{_discrete(visible(lambda f, p=phase: f.phase == p), kt, dur)}</text>"
        )
    # exhaust flows inside -> outside (left to right), intake the other way
    arrows = (
        f'<line x1="180" y1="40" x2="460" y2="40" stroke="{TEXT}" stroke-width="4" stroke-linecap="round" '
        f'marker-end="url(#head)" opacity="0">{_discrete(visible(lambda f: f.phase == "exhaust"), kt, dur)}</line>'
        f'<line x1="460" y1="40" x2="180" y2="40" stroke="{TEXT}" stroke-width="4" stroke-linecap="round" '
        f'marker-end="url(#head)" opacity="0">{_discrete(visible(lambda f: f.phase == "intake"), kt, dur)}</line>'
    )

    start, end = frames[0].when, frames[-1].when
    same_day = start.date() == end.date()
    fmt = "%H:%M" if same_day else "%d %b %H:%M"
    title_text = escape(title or "Replay")
    period = f"{start.strftime('%d %b %Y')}" if same_day else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 270" width="640" height="270" font-family="sans-serif">
<defs>
<linearGradient id="temp" x1="20" y1="0" x2="620" y2="0" gradientUnits="userSpaceOnUse">{"".join(stops)}</linearGradient>
<marker id="head" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 Z" fill="{TEXT}"/></marker>
</defs>
<text x="20" y="22" font-size="13" fill="{TEXT}">{title_text}</text>
<text x="620" y="22" text-anchor="end" font-size="13" fill="{TEXT}">{escape(period)}</text>
{"".join(labels)}
{arrows}
<g transform="translate(0,10)">
<path d="{_PIPE}" fill="url(#temp)" stroke="{OUTLINE}" stroke-width="3" stroke-linejoin="round"/>
<text x="20" y="92" font-size="15" font-weight="bold" fill="{TEXT}">Inside</text>
<text x="620" y="92" text-anchor="end" font-size="15" font-weight="bold" fill="{TEXT}">Outside</text>
</g>
<line x1="40" y1="228" x2="600" y2="228" stroke="{OUTLINE}" stroke-width="2"/>
<circle cx="40" cy="228" r="6" fill="{TEXT}"><animate attributeName="cx" from="40" to="600" dur="{dur:g}s" repeatCount="indefinite"/></circle>
<text x="40" y="252" font-size="13" fill="{TEXT}">{escape(start.strftime(fmt))}</text>
<text x="600" y="252" text-anchor="end" font-size="13" fill="{TEXT}">{escape(end.strftime(fmt))}</text>
<text x="320" y="252" text-anchor="middle" font-size="12" fill="{TEXT}">{n} frames, {dur:g} s loop</text>
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
<text x="320" y="80" text-anchor="middle" font-size="13" fill="{TEXT}">Run the action "Recuperator: Create replay" to make one.</text>
</svg>"""
