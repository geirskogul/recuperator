"""Draws the recuperator as an SVG: a symmetric "muffler" pipe filled with a
temperature gradient from the inside (room) end on the left to the outside
end on the right, in weather-map colours, with the current airflow shown.

Pure Python (no Home Assistant), so it can be tested and previewed directly.
"""

from __future__ import annotations

from html import escape

# Temperature colour scale approximating the U.S. National Weather Service
# temperature-map palette: lavender and purple for extreme cold, blues for
# freezing, greens for cool, yellow and orange for warm, reds for hot.
# (°C, hex colour), in rising order; colours in between are interpolated.
PALETTE: tuple[tuple[float, str], ...] = (
    (-40, "#e3c6f5"),
    (-30, "#b07ad6"),
    (-20, "#6f3fb4"),
    (-12, "#3a3fbf"),
    (-5, "#2f6fdc"),
    (0, "#4fa3ec"),
    (5, "#3cc4c6"),
    (10, "#46bf62"),
    (15, "#9fd34a"),
    (20, "#f1e344"),
    (25, "#f6b637"),
    (30, "#ee7f25"),
    (35, "#d9401f"),
    (40, "#a8161f"),
    (46, "#7b0b43"),
)

NEUTRAL = "#9aa0a6"  # pipe fill when a probe is unavailable
TEXT = "#8a9199"  # readable on both light and dark dashboards
OUTLINE = "#6b7280"


def _hex(c: str) -> tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def temperature_colour(celsius: float) -> str:
    """The palette colour for a temperature, interpolated between steps."""
    if celsius <= PALETTE[0][0]:
        return PALETTE[0][1]
    if celsius >= PALETTE[-1][0]:
        return PALETTE[-1][1]
    for (t0, c0), (t1, c1) in zip(PALETTE, PALETTE[1:]):
        if t0 <= celsius <= t1:
            f = (celsius - t0) / (t1 - t0)
            a, b = _hex(c0), _hex(c1)
            return "#%02x%02x%02x" % tuple(round(x + (y - x) * f) for x, y in zip(a, b))
    return NEUTRAL


def _stops(left: float | None, right: float | None, n: int = 16) -> list[tuple[float, str]]:
    """Gradient stops from the left temperature to the right one.

    The temperature is interpolated in °C and each step mapped through the
    palette, so a -20 to +20 pipe passes through blue and green as it should.
    """
    if left is None or right is None:
        return [(0.0, NEUTRAL), (1.0, NEUTRAL)]
    return [(i / (n - 1), temperature_colour(left + (right - left) * i / (n - 1))) for i in range(n)]


# Pipe outline (viewBox 0 0 640 240): necks, tapers ("pinched" ends) and body.
_PIPE = (
    "M 20,100 L 70,100 C 95,100 100,60 130,60 L 510,60 "
    "C 540,60 545,100 570,100 L 620,100 L 620,140 L 570,140 "
    "C 545,140 540,180 510,180 L 130,180 C 100,180 95,140 70,140 L 20,140 Z"
)

PHASE_TEXT = {
    "exhaust": "Exhaust",
    "intake": "Intake",
    "pause": "Pause",
    "stopped": "Stopped",
}


def _fmt(t: float | None) -> str:
    return "–" if t is None else f"{t:.1f} °C"


def render_svg(
    outside: float | None,
    inside: float | None,
    phase: str = "stopped",
    title: str = "",
) -> str:
    """The whole picture as an SVG document.

    Left end: inside (room side of the core, inside probe).
    Right end: outside (outdoor side of the core, outside probe).
    """
    stops = "".join(
        f'<stop offset="{o:.3f}" stop-color="{c}"/>' for o, c in _stops(inside, outside)
    )
    # Airflow arrow above the pipe: exhaust runs inside to outside (left to right),
    # intake runs outside to inside (right to left).
    arrow = ""
    if phase in ("intake", "exhaust"):
        if phase == "exhaust":
            x1, x2 = 180, 460
        else:
            x1, x2 = 460, 180
        arrow = (
            f'<line x1="{x1}" y1="34" x2="{x2}" y2="34" stroke="{TEXT}" stroke-width="4" '
            f'stroke-linecap="round" marker-end="url(#head)"/>'
        )
    label = PHASE_TEXT.get(phase, phase)
    title_el = (
        f'<text x="320" y="234" text-anchor="middle" font-size="13" fill="{TEXT}">{escape(title)}</text>'
        if title
        else ""
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 240" width="640" height="240" font-family="sans-serif">
<defs>
<linearGradient id="temp" x1="20" y1="0" x2="620" y2="0" gradientUnits="userSpaceOnUse">{stops}</linearGradient>
<marker id="head" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 Z" fill="{TEXT}"/></marker>
</defs>
{arrow}
<text x="320" y="22" text-anchor="middle" font-size="16" font-weight="bold" fill="{TEXT}">{escape(label)}</text>
<path d="{_PIPE}" fill="url(#temp)" stroke="{OUTLINE}" stroke-width="3" stroke-linejoin="round"/>
<text x="20" y="92" font-size="15" font-weight="bold" fill="{TEXT}">Inside</text>
<text x="20" y="206" font-size="15" fill="{TEXT}">{_fmt(inside)}</text>
<text x="620" y="92" text-anchor="end" font-size="15" font-weight="bold" fill="{TEXT}">Outside</text>
<text x="620" y="206" text-anchor="end" font-size="15" fill="{TEXT}">{_fmt(outside)}</text>
{title_el}
</svg>"""
