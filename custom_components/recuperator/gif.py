"""The replay as an animated GIF, for places where an animated SVG does not play (chats, e-mail, phones).

Draws the same picture as replay.py, frame by frame, with Pillow (which comes
with Home Assistant): the pipe's gradient, the phase and arrow, the readings,
and the history graph with its sweeping cursor. A GIF has no transparency to
speak of, so it is drawn on a white background.

Pure Python (no Home Assistant), so it can be tested directly.
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from .diagram import (
    BADGE_HEIGHT,
    BADGE_WIDTH,
    BADGE_Y,
    INSIDE_BADGE_X,
    NEUTRAL,
    OUTLINE,
    OUTSIDE_BADGE_X,
    PALETTE,
    TEXT,
    Palette,
    format_temperature,
    temperature_colour,
)
from .replay import (
    BAND_HEIGHT,
    BAND_TOP,
    CHART_TOP,
    CHART_X0,
    CHART_X1,
    HEIGHT,
    INSIDE_COLOUR,
    LEGEND_Y,
    OUTSIDE_COLOUR,
    PHASE_COLOURS,
    PIPE_DY,
    STOPS,
    Frame,
    Scale,
    _display,
    _runs,
    chart_scale,
    header_texts,
    time_format,
)

SS = 2  # drawn at twice the size, then halved: smooth edges and text
WIDTH = 640
MAX_FRAMES = 200  # every GIF frame is stored nearly whole: more makes files of many MB
PALETTE_SAMPLES = 12  # frames the shared colour palette is made from
MIN_FRAME_MS = 20  # browsers slow anything faster down to 100 ms a frame
BACKGROUND = (255, 255, 255, 255)
PHASE_TEXT = {"exhaust": "Exhaust", "intake": "Intake", "pause": "Pause", "stopped": "Stopped"}


@lru_cache
def _font(size: float, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """DejaVu Sans where installed, otherwise Pillow's own font."""
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", round(size * SS))
    except OSError:
        return ImageFont.load_default(round(size * SS))


def _rgba(colour: str, opacity: float = 1.0) -> tuple[int, int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16), round(255 * opacity)


def _bezier(p0, p1, p2, p3, steps: int = 16) -> list[tuple[float, float]]:
    out = []
    for k in range(1, steps + 1):
        t = k / steps
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def _pipe_outline() -> list[tuple[float, float]]:
    """diagram._PIPE as a polygon (the same necks, tapers and body)."""
    pts = [(20, 100), (70, 100)]
    pts += _bezier((70, 100), (95, 100), (100, 60), (130, 60))
    pts += [(510, 60)]
    pts += _bezier((510, 60), (540, 60), (545, 100), (570, 100))
    pts += [(620, 100), (620, 140), (570, 140)]
    pts += _bezier((570, 140), (545, 140), (540, 180), (510, 180))
    pts += [(130, 180)]
    pts += _bezier((130, 180), (100, 180), (95, 140), (70, 140))
    pts += [(20, 140)]
    return pts


PIPE = _pipe_outline()
PIPE_LEFT, PIPE_TOP, PIPE_RIGHT, PIPE_BOTTOM = 20, 60, 620, 180


class _Pen:
    """Draws in the SVG's coordinates (640 wide), on the panel at height `top`, at SS times the size."""

    def __init__(self, image: Image.Image, top: float = 0) -> None:
        self.draw = ImageDraw.Draw(image, "RGBA")
        self.top = top

    def xy(self, x: float, y: float) -> tuple[float, float]:
        return x * SS, (y + self.top) * SS

    def text(self, x, y, text: str, size: float, fill=TEXT, anchor: str = "ls", bold: bool = False) -> None:
        colour = _rgba(fill) if isinstance(fill, str) else fill
        self.draw.text(self.xy(x, y), text, font=_font(size, bold), fill=colour, anchor=anchor)

    def line(self, points, fill, width: float, opacity: float = 1.0) -> None:
        self.draw.line([self.xy(*p) for p in points], fill=_rgba(fill, opacity), width=round(width * SS), joint="curve")

    def rect(self, x0, y0, x1, y1, fill, opacity: float = 1.0, radius: float = 0) -> None:
        box = [self.xy(x0, y0), self.xy(x1, y1)]
        self.draw.rounded_rectangle(box, radius=radius * SS, fill=_rgba(fill, opacity))

    def dot(self, x, y, r: float, fill: str) -> None:
        (cx, cy), r = self.xy(x, y), r * SS
        self.draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_rgba(fill), outline=(255, 255, 255, 255), width=round(1.5 * SS))

    def arrow(self, x1: float, x2: float, y: float) -> None:
        """A line with an arrowhead at x2, like the SVG's marker."""
        self.line([(x1, y), (x2, y)], TEXT, 4)
        tip, back = x2 + (10 if x2 > x1 else -10), x2 - (4 if x2 > x1 else -4)
        self.draw.polygon([self.xy(tip, y), self.xy(back, y - 10), self.xy(back, y + 10)], fill=_rgba(TEXT))


def _chart_x(i: int, n: int) -> float:
    return CHART_X0 + (CHART_X1 - CHART_X0) * i / (n - 1)


def _static_layer(
    size: tuple[int, int], panels: list[tuple[list[Frame], str]], header: tuple[str, str], scale: Scale, unit: str, fmt: str
) -> Image.Image:
    """Everything that stays put, on a transparent layer: titles, pipe outline, badge boxes, the graph."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    for p, (frames, title) in enumerate(panels):
        pen = _Pen(layer, p * HEIGHT)
        n = len(frames)
        if p:
            pen.line([(20, 0), (620, 0)], OUTLINE, 1, 0.35)
        pen.text(20, 22, title, 13)
        if p == 0:
            pen.text(620, 22, header[0], 13, anchor="rs")
            pen.text(620, 40, header[1], 11, anchor="rs")
        pipe = [(x, y + PIPE_DY) for x, y in PIPE]
        pen.line([*pipe, pipe[0], pipe[1]], OUTLINE, 3)
        pen.text(20, 92 + PIPE_DY, "Inside", 15, bold=True)
        pen.text(620, 92 + PIPE_DY, "Outside", 15, anchor="rs", bold=True)
        for x in (INSIDE_BADGE_X, OUTSIDE_BADGE_X):
            y = BADGE_Y + PIPE_DY
            pen.rect(x - BADGE_WIDTH / 2, y - BADGE_HEIGHT / 2, x + BADGE_WIDTH / 2, y + BADGE_HEIGHT / 2, "#000000", 0.5, 6)
        # the graph: grid, phase strip, the two lines, the period's ends and the legend
        for value in scale.ticks():
            y = scale.y(value)
            pen.line([(CHART_X0, y), (CHART_X1, y)], OUTLINE, 1, 0.35)
            pen.text(CHART_X0 - 6, y + 4, f"{value:g} {unit}", 11, anchor="rs")
        pen.rect(CHART_X0, BAND_TOP, CHART_X1, BAND_TOP + BAND_HEIGHT, OUTLINE, 0.2)
        for first, last, phase in _runs([f.phase for f in frames]):
            if colour := PHASE_COLOURS.get(phase):
                x1 = _chart_x(last + 1, n) if last + 1 < n else CHART_X1
                pen.rect(_chart_x(first, n), BAND_TOP, max(x1, _chart_x(first, n) + 0.5), BAND_TOP + BAND_HEIGHT, colour)
        for values, colour in (
            ([_display(f.inside, unit) for f in frames], INSIDE_COLOUR),
            ([_display(f.outside, unit) for f in frames], OUTSIDE_COLOUR),
        ):
            run: list[tuple[float, float]] = []
            for i, v in enumerate([*values, None]):
                if v is None:
                    if len(run) > 1:
                        pen.line(run, colour, 2)
                    run = []
                else:
                    run.append((_chart_x(i, n), scale.y(v)))
        pen.text(CHART_X0, LEGEND_Y, frames[0].when.strftime(fmt), 12)
        pen.text(CHART_X1, LEGEND_Y, frames[-1].when.strftime(fmt), 12, anchor="rs")
        x = 212
        for kind, colour, text in (
            ("line", INSIDE_COLOUR, "Inside"),
            ("line", OUTSIDE_COLOUR, "Outside"),
            ("bar", PHASE_COLOURS["exhaust"], "Exhaust"),
            ("bar", PHASE_COLOURS["intake"], "Intake"),
        ):
            if kind == "line":
                pen.line([(x, LEGEND_Y - 4), (x + 16, LEGEND_Y - 4)], colour, 3)
            else:
                pen.rect(x, LEGEND_Y - 9, x + 16, LEGEND_Y + 1, colour)
            pen.text(x + 21, LEGEND_Y, text, 11)
            x += 26 + 7 * len(text)
    return layer


def _pipe_mask(size: tuple[int, int], panels: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for p in range(panels):
        draw.polygon([(x * SS, (y + PIPE_DY + p * HEIGHT) * SS) for x, y in PIPE], fill=255)
    return mask


def _gradient(frame: Frame, palette: Palette) -> Image.Image:
    """The pipe's fill for one frame: STOPS colours from the inside to the outside reading, blended in between."""
    if frame.inside is None or frame.outside is None:
        colours = [NEUTRAL] * STOPS
    else:
        colours = [
            temperature_colour(frame.inside + (frame.outside - frame.inside) * k / (STOPS - 1), palette)
            for k in range(STOPS)
        ]
    row = Image.new("RGBA", (STOPS, 1))
    row.putdata([_rgba(c) for c in colours])
    width, height = (PIPE_RIGHT - PIPE_LEFT) * SS, (PIPE_BOTTOM - PIPE_TOP) * SS
    return row.resize((width, 1), Image.Resampling.BILINEAR).resize((width, height), Image.Resampling.NEAREST)


def _moving(pen: _Pen, frames: list[Frame], i: int, scale: Scale, unit: str, fmt: str) -> None:
    """What changes from frame to frame: the phase, the readings, and the cursor on the graph."""
    f, n = frames[i], len(frames)
    inside, outside = _display(f.inside, unit), _display(f.outside, unit)
    pen.text(320, 22, PHASE_TEXT.get(f.phase, f.phase), 16, anchor="ms", bold=True)
    if f.phase == "exhaust":
        pen.arrow(180, 460, 40)
    elif f.phase == "intake":
        pen.arrow(460, 180, 40)
    y = BADGE_Y + PIPE_DY + 5
    pen.text(INSIDE_BADGE_X, y, format_temperature(f.inside, unit), 14, fill=(255, 255, 255, 255), anchor="ms", bold=True)
    pen.text(OUTSIDE_BADGE_X, y, format_temperature(f.outside, unit), 14, fill=(255, 255, 255, 255), anchor="ms", bold=True)
    x = _chart_x(i, n)
    pen.line([(x, CHART_TOP - 4), (x, BAND_TOP + BAND_HEIGHT)], TEXT, 1.5)
    for value, colour in ((inside, INSIDE_COLOUR), (outside, OUTSIDE_COLOUR)):
        if value is not None:
            pen.dot(x, scale.y(value), 4.5, colour)
    pen.text(x, CHART_TOP - 8, f.when.strftime(fmt), 12, anchor="ms", bold=True)


def _quantize(frames: list[Image.Image]) -> list[Image.Image]:
    """All frames on one shared 256-colour palette, made from a sample of them.

    A palette per frame would shift colours from frame to frame, so every frame
    would differ everywhere and be stored whole. With one palette, only what
    really changed is stored, and median cut keeps the pipe's gradient smooth.
    """
    step = max(1, len(frames) // PALETTE_SAMPLES)
    sample = frames[::step][:PALETTE_SAMPLES]
    sheet = Image.new("RGB", (frames[0].width, frames[0].height * len(sample)))
    for k, frame in enumerate(sample):
        sheet.paste(frame, (0, frame.height * k))
    palette = sheet.quantize(256, Image.Quantize.MEDIANCUT)
    return [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]


def gif_frame_count(frames: int, playback_seconds: float) -> int:
    """How many frames the GIF keeps: at most MAX_FRAMES, and none shorter than MIN_FRAME_MS."""
    return max(2, min(frames, MAX_FRAMES, int(playback_seconds * 1000 / MIN_FRAME_MS)))


def render_replay_gif(
    frames: list[Frame],
    playback_seconds: float = 60,
    title: str = "",
    palette: Palette = PALETTE,
    unit: str = "°C",
    linked: list[Frame] | None = None,
    linked_title: str = "",
) -> bytes:
    """The replay (and a synced recuperator's panel under it) as a looping GIF of up to MAX_FRAMES frames."""
    if len(frames) < 2:
        raise ValueError("at least two frames are needed")
    if linked is not None and len(linked) != len(frames):
        raise ValueError("the linked unit needs one frame per frame")
    count = gif_frame_count(len(frames), playback_seconds)
    picks = [round(k * (len(frames) - 1) / (count - 1)) for k in range(count)]
    panels = [([frames[i] for i in picks], title or "Replay")]
    if linked:
        panels.append(([linked[i] for i in picks], linked_title or "Synced recuperator"))
    # The graph is drawn from the picked frames too, so the dots stay on its lines.
    fmt = time_format(frames)
    scale = chart_scale([p[0] for p in panels], unit)
    header = header_texts(panels[0][0], playback_seconds)
    size = (WIDTH * SS, HEIGHT * len(panels) * SS)
    static = _static_layer(size, panels, header, scale, unit, fmt)
    mask = _pipe_mask(size, len(panels))
    rendered: list[Image.Image] = []
    for i in range(count):
        canvas = Image.new("RGBA", size, BACKGROUND)
        fill = Image.new("RGBA", size, BACKGROUND)
        for p, (unit_frames, _title) in enumerate(panels):
            fill.paste(_gradient(unit_frames[i], palette), (PIPE_LEFT * SS, (PIPE_TOP + PIPE_DY + p * HEIGHT) * SS))
        canvas.paste(fill, (0, 0), mask)
        canvas.alpha_composite(static)
        for p, (unit_frames, _title) in enumerate(panels):
            _moving(_Pen(canvas, p * HEIGHT), unit_frames, i, scale, unit, fmt)
        rendered.append(canvas.convert("RGB").reduce(SS))
    images = _quantize(rendered)
    out = BytesIO()
    duration = round(playback_seconds * 1000 / count)
    images[0].save(out, "GIF", save_all=True, append_images=images[1:], duration=duration, loop=0)
    return out.getvalue()
