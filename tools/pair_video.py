"""The simulated hour of a synced pair (see pair_sim.py) as a video: MP4, and optionally WebM and GIF.

Drawn with the integration's own GIF replay code (gif.py), so it looks like a
replay of the pair, at 30 frames a second, plus what only a simulation knows:

* the pipe's body shows the ceramic's real temperature, slice by slice, so the
  warm and cold fronts can be seen moving through the core (a real replay can
  only blend between the two probes);
* streaks of air flow through the pipe, faster in its narrow ends, slowing to
  a stop in each pause and setting off the other way;
* a header with the clock, the outdoor temperature and the last whole breath,
  and on each unit its Heat recovery and why the last phase ended.

Needs ffmpeg on the path.

    python tools/pair_video.py --out pair.mp4 --webm --gif   # about five minutes on four cores
    python tools/pair_video.py --still 900 --out frame.png   # one frame, to check the look
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta
import math
import multiprocessing
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image, ImageDraw

from pair_sim import REASON_TEXT, Run, UnitSample, integration, simulate

gif = integration("gif")
replay = integration("replay")
diagram = integration("diagram")

DRAW_SCALE = 4  # drawn at four times the 640-wide layout...
OUTPUT_SCALE = 2  # ...and halved: 1280 px wide, with smooth edges
gif.SS = DRAW_SCALE  # gif.py's pens and fonts follow this

HEADER = 72  # the band above the two panels
PANEL = replay.HEIGHT
WIDTH = gif.WIDTH
PIPE_TOP = gif.PIPE_TOP + replay.PIPE_DY  # within a panel
PIPE_W, PIPE_H = gif.PIPE_RIGHT - gif.PIPE_LEFT, gif.PIPE_BOTTOM - gif.PIPE_TOP

# The pipe's shape along its length (diagram._PIPE): narrow ends, tapers, the body holding the core.
NECK_HALF, BODY_HALF = 20.0, 60.0
LEFT_TAPER, RIGHT_TAPER = (70.0, 130.0), (510.0, 570.0)
PIPE_MID_Y = 120.0

# Air streaks
STREAKS = 70  # per pipe
STREAK_SPEED = 5.5  # px per frame in the body at full flow; the narrow ends are three times faster
STREAK_SPREAD = 0.2  # each streak is up to this much faster or slower, so they mix like real air
STREAK_BLUR = 2.0  # frames of motion each streak shows
STREAK_MAX = 18.0  # px, the longest streak
STREAK_COLOUR = (255, 255, 255, 170)
STREAK_WIDTH = 2.2
BADGE_CLEARANCE = 4  # px kept clear of streaks around each reading
FAN_TAU = 2.5  # s for a fan to spin up or down

BACKGROUND = (255, 255, 255, 255)
PHASE_NAMES = {"exhaust": "Exhaust", "intake": "Intake", "pause": "Pause", "stopped": "Stopped"}


# -- the pipe's shape and colours -----------------------------------------------------------


def _smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def half_height(x: np.ndarray) -> np.ndarray:
    """Half the pipe's inner height at each x (layout units)."""
    rise = _smoothstep((x - LEFT_TAPER[0]) / (LEFT_TAPER[1] - LEFT_TAPER[0]))
    fall = _smoothstep((RIGHT_TAPER[1] - x) / (RIGHT_TAPER[1] - RIGHT_TAPER[0]))
    return NECK_HALF + (BODY_HALF - NECK_HALF) * np.minimum(rise, fall)


COLUMN_X = gif.PIPE_LEFT + (np.arange(PIPE_W * DRAW_SCALE) + 0.5) / DRAW_SCALE  # every pixel column's x
_PAL_T = np.array([t for t, _c in diagram.PALETTE])
_PAL_RGB = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for _t, c in diagram.PALETTE], dtype=float)


def colours(temperatures: np.ndarray) -> np.ndarray:
    """The diagram palette's colour for each temperature, as uint8 RGB rows."""
    return np.stack([np.interp(temperatures, _PAL_T, _PAL_RGB[:, k]) for k in range(3)], axis=-1).round().astype(np.uint8)


def pipe_temperatures(sample: UnitSample) -> np.ndarray:
    """The temperature at every pixel column: probes in the narrow ends, the ceramic in the body, blended in the tapers."""
    inside = sample.inside if sample.inside is not None else sample.profile[0]
    outside = sample.outside if sample.outside is not None else sample.profile[-1]
    core_x = np.linspace(LEFT_TAPER[1], RIGHT_TAPER[0], len(sample.profile))
    core = np.interp(COLUMN_X, core_x, sample.profile)
    to_core = _smoothstep((COLUMN_X - LEFT_TAPER[0]) / (LEFT_TAPER[1] - LEFT_TAPER[0]))
    to_outside = _smoothstep((COLUMN_X - RIGHT_TAPER[0]) / (RIGHT_TAPER[1] - RIGHT_TAPER[0]))
    left = inside + (core - inside) * to_core
    return left + (outside - left) * to_outside


def pipe_fill(sample: UnitSample) -> Image.Image:
    """The pipe's colours for one moment, as a block the size of the pipe's bounding box."""
    row = colours(pipe_temperatures(sample))
    block = np.broadcast_to(row, (PIPE_H * DRAW_SCALE, *row.shape))
    return Image.fromarray(np.ascontiguousarray(block), "RGB").convert("RGBA")


# -- air streaks -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Streaks:
    """Where every streak in one pipe is in one frame: head x, lane (-1..1 across the pipe), speed (px/frame)."""

    x: np.ndarray
    lane: np.ndarray
    speed: np.ndarray


def fan_flow(samples: list[UnitSample]) -> np.ndarray:
    """The airflow each second (+1 out, -1 in), easing as the fans spin up and down."""
    keep = math.exp(-1.0 / FAN_TAU)
    flow, out = 0.0, np.empty(len(samples))
    for i, s in enumerate(samples):
        flow = s.flow + (flow - s.flow) * keep
        out[i] = flow
    return out


def streak_frames(flows: np.ndarray, seed: int) -> list[Streaks]:
    """Advance the streaks frame by frame; one that leaves the pipe comes back in at the other end."""
    rng = np.random.default_rng(seed)
    x = rng.uniform(gif.PIPE_LEFT, gif.PIPE_RIGHT, STREAKS)
    lane = rng.uniform(-0.88, 0.88, STREAKS)
    pace = rng.uniform(1 - STREAK_SPREAD, 1 + STREAK_SPREAD, STREAKS)
    frames = []
    for flow in flows:
        speed = STREAK_SPEED * pace * flow * BODY_HALF / half_height(x)
        x = x + speed
        lane = _respawn(x, lane, rng)
        x = gif.PIPE_LEFT + np.mod(x - gif.PIPE_LEFT, PIPE_W)
        frames.append(Streaks(x.copy(), lane.copy(), speed.copy()))
    return frames


def _respawn(x: np.ndarray, lane: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A new lane for every streak that just left the pipe."""
    gone = (x < gif.PIPE_LEFT) | (x > gif.PIPE_RIGHT)
    lane = lane.copy()
    lane[gone] = rng.uniform(-0.88, 0.88, int(gone.sum()))
    return lane


def draw_streaks(block: Image.Image, streaks: Streaks) -> None:
    """Each streak as a short line trailing behind it, longer the faster it goes; none over the readings."""
    layer = Image.new("RGBA", block.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    length = np.clip(np.abs(streaks.speed) * STREAK_BLUR, 1.2, STREAK_MAX)
    tail = streaks.x - np.sign(streaks.speed) * length
    for head_x, tail_x, lane in zip(streaks.x, tail, streaks.lane):
        points = [_block_xy(head_x, lane), _block_xy(tail_x, lane)]
        draw.line(points, fill=STREAK_COLOUR, width=round(STREAK_WIDTH * DRAW_SCALE))
    for box in BADGE_BOXES:
        draw.rectangle(box, fill=(0, 0, 0, 0))
    block.alpha_composite(layer)


def _badge_boxes() -> list[tuple[float, float, float, float]]:
    """The two readings' boxes, with some clearance, in the pipe block's pixels."""
    boxes = []
    for x in (diagram.INSIDE_BADGE_X, diagram.OUTSIDE_BADGE_X):
        half_w = diagram.BADGE_WIDTH / 2 + BADGE_CLEARANCE
        half_h = diagram.BADGE_HEIGHT / 2 + BADGE_CLEARANCE
        x0, y0 = _block_xy(x - half_w, 0)[0], (diagram.BADGE_Y - half_h - gif.PIPE_TOP) * DRAW_SCALE
        x1, y1 = _block_xy(x + half_w, 0)[0], (diagram.BADGE_Y + half_h - gif.PIPE_TOP) * DRAW_SCALE
        boxes.append((x0, y0, x1, y1))
    return boxes


def _block_xy(x: float, lane: float) -> tuple[float, float]:
    """A point on a lane, in the pipe block's pixels."""
    y = PIPE_MID_Y + lane * float(half_height(np.array(x))) * 0.84
    return (x - gif.PIPE_LEFT) * DRAW_SCALE, (y - gif.PIPE_TOP) * DRAW_SCALE


BADGE_BOXES = _badge_boxes()


# -- the frames -------------------------------------------------------------------------------


@dataclass
class Movie:
    """Everything a frame is drawn from, worked out once before drawing starts."""

    run: Run
    picks: list[int]  # the sample (simulated second) shown in each frame
    replay_frames: list[list]  # per unit, the replay.Frame of each frame (for gif.py's graph and cursor)
    streaks: list[list[Streaks]]  # per unit, per frame
    scale: replay.Scale
    fmt: str
    static: Image.Image
    masks: list[tuple[tuple[int, int], Image.Image]]  # per unit: the pipe's corner on the canvas and its mask
    size: tuple[int, int]


def plan(run: Run, playback_seconds: float, fps: int) -> Movie:
    count = round(playback_seconds * fps)
    hour = run.times[-1]
    times = [hour * k / (count - 1) for k in range(count)]
    picks = [min(round(t), len(run.times) - 1) for t in times]
    replay_frames = [
        [replay.Frame(run.start + timedelta(seconds=run.times[i]), s[i].inside, s[i].outside, s[i].phase) for i in picks]
        for s in run.samples
    ]
    streaks = [
        streak_frames(fan_flow(samples)[picks], seed=11 + n) for n, samples in enumerate(run.samples)
    ]
    scale = replay.chart_scale(replay_frames, "°C")
    fmt = replay.time_format(replay_frames[0])
    size = (WIDTH * DRAW_SCALE, (HEADER + PANEL * len(run.samples)) * DRAW_SCALE)
    panels = list(zip(replay_frames, run.names))
    static = _static(size, panels, scale, fmt)
    return Movie(run, picks, replay_frames, streaks, scale, fmt, static, _masks(len(panels)), size)


def _static(size: tuple[int, int], panels: list, scale: replay.Scale, fmt: str) -> Image.Image:
    """gif.py's still layer for the two panels (titles, pipe outlines, graphs) under a header band."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    below = gif._static_layer((size[0], size[1] - HEADER * DRAW_SCALE), panels, ("", ""), scale, "°C", fmt)
    layer.paste(below, (0, HEADER * DRAW_SCALE))
    pen = gif._Pen(layer)
    pen.line([(20, HEADER), (620, HEADER)], diagram.OUTLINE, 1, 0.35)
    pen.text(20, 28, "Two synced recuperators, one simulated hour", 17, fill="#3c4148", bold=True)
    pen.text(20, 47, "20 °C room · outdoor air cooling from 10 to 8 °C · sync rule: Either is done", 11)
    pen.text(20, 63, "The integration's own cycle logic; simulated cores, hoses and probes", 11)
    return layer


def _masks(units: int) -> list[tuple[tuple[int, int], Image.Image]]:
    """For each pipe: where its bounding box sits on the canvas, and its outline as a mask of that box."""
    out = []
    for p in range(units):
        top = HEADER + p * PANEL + PIPE_TOP
        mask = Image.new("L", (PIPE_W * DRAW_SCALE, PIPE_H * DRAW_SCALE), 0)
        outline = [((x - gif.PIPE_LEFT) * DRAW_SCALE, (y - gif.PIPE_TOP) * DRAW_SCALE) for x, y in gif.PIPE]
        ImageDraw.Draw(mask).polygon(outline, fill=255)
        out.append(((gif.PIPE_LEFT * DRAW_SCALE, top * DRAW_SCALE), mask))
    return out


MOVIE: Movie | None = None  # set before the workers start, so they share it


def render(k: int) -> bytes:
    """Frame k as raw RGB at the output size."""
    movie = MOVIE
    canvas = Image.new("RGBA", movie.size, BACKGROUND)
    for p, samples in enumerate(movie.run.samples):
        block = pipe_fill(samples[movie.picks[k]])
        draw_streaks(block, movie.streaks[p][k])
        corner, mask = movie.masks[p]
        canvas.paste(block, corner, mask)
    canvas.alpha_composite(movie.static)
    _draw_header(gif._Pen(canvas), movie, k)
    for p, samples in enumerate(movie.run.samples):
        pen = gif._Pen(canvas, HEADER + p * PANEL)
        gif._moving(pen, movie.replay_frames[p], k, movie.scale, "°C", movie.fmt)
        _draw_unit(pen, samples[movie.picks[k]])
    return canvas.convert("RGB").reduce(OUTPUT_SCALE).tobytes()


def _draw_header(pen: gif._Pen, movie: Movie, k: int) -> None:
    run, i = movie.run, movie.picks[k]
    when = run.start + timedelta(seconds=run.times[i])
    pen.text(620, 30, when.strftime("%H:%M"), 20, fill="#3c4148", anchor="rs", bold=True)
    pen.text(620, 47, f"Outdoor {run.outdoor[i]:.1f} °C", 11, anchor="rs")
    cycle = run.last_cycle(run.times[i])
    pen.text(620, 63, f"Last breath {cycle:.0f} s" if cycle else "", 11, anchor="rs")


def _draw_unit(pen: gif._Pen, s: UnitSample) -> None:
    """A unit's read-outs: time into the phase, Heat recovery, and why the last phase ended."""
    if s.phase in ("exhaust", "intake"):
        pen.text(20, 40, f"{s.elapsed:.0f} s into the {PHASE_NAMES[s.phase].lower()}", 11)
    recovery = "–" if s.heat_recovery is None else f"{s.heat_recovery:.0f} %"
    pen.text(620, 22, f"Heat recovery {recovery}", 13, anchor="rs")
    reason = REASON_TEXT.get(s.last_reason, s.last_reason or "–")
    pen.text(320, 207, f"Last switch: {reason}", 11, anchor="ms")


# -- writing the files --------------------------------------------------------------------------


def _ffmpeg(*args: str) -> list[str]:
    return ["ffmpeg", "-y", "-loglevel", "error", *args]


def encode_master(movie: Movie, out: Path, fps: int, workers: int) -> None:
    """Every frame, losslessly (FFV1), so each format is made from clean frames.

    Made from a lossy MP4 instead, a GIF would store the compression noise:
    every frame would differ everywhere, and the file would be several times larger.
    """
    global MOVIE
    MOVIE = movie
    width, height = movie.size[0] // OUTPUT_SCALE, movie.size[1] // OUTPUT_SCALE
    command = _ffmpeg(
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
        "-c:v", "ffv1", "-pix_fmt", "bgr0", str(out),
    )
    ffmpeg = subprocess.Popen(command, stdin=subprocess.PIPE)
    with multiprocessing.get_context("fork").Pool(workers) as pool:
        for n, frame in enumerate(pool.imap(render, range(len(movie.picks)), chunksize=4)):
            ffmpeg.stdin.write(frame)
            if n % 150 == 0:
                print(f"frame {n}/{len(movie.picks)}", flush=True)
    ffmpeg.stdin.close()
    if ffmpeg.wait():
        raise SystemExit("ffmpeg failed")


def encode_mp4(master: Path, out: Path) -> None:
    command = _ffmpeg(
        "-i", str(master), "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-tune", "animation",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
    )
    subprocess.run(command, check=True)


def encode_webm(master: Path, out: Path) -> None:
    command = _ffmpeg(
        "-i", str(master), "-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-row-mt", "1", "-pix_fmt", "yuv420p", str(out),
    )
    subprocess.run(command, check=True)


def encode_gif(master: Path, out: Path, width: int, fps: int) -> None:
    """A GIF on one palette for the whole film, storing only what changes between frames."""
    graph = (
        f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
        "[a]palettegen=max_colors=128:stats_mode=diff[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
    )
    subprocess.run(_ffmpeg("-i", str(master), "-vf", graph, "-loop", "0", str(out)), check=True)


def write_still(movie: Movie, frame: int, out: Path) -> None:
    global MOVIE
    MOVIE = movie
    width, height = movie.size[0] // OUTPUT_SCALE, movie.size[1] // OUTPUT_SCALE
    Image.frombytes("RGB", (width, height), render(frame)).save(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=Path("synced-pair-hour.mp4"))
    parser.add_argument("--seconds", type=float, default=60.0, help="length of the film (the hour plays in this)")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--webm", action="store_true", help="also write a WebM next to the MP4")
    parser.add_argument("--gif", action="store_true", help="also write a GIF next to the MP4")
    parser.add_argument("--gif-width", type=int, default=480)
    parser.add_argument("--gif-fps", type=int, default=12)
    parser.add_argument("--still", type=int, help="write only this frame, as a PNG, to --out")
    parser.add_argument("--workers", type=int, default=multiprocessing.cpu_count())
    args = parser.parse_args()

    movie = plan(simulate(), args.seconds, args.fps)
    if args.still is not None:
        write_still(movie, args.still, args.out)
        return
    master = args.out.with_suffix(".master.mkv")
    encode_master(movie, master, args.fps, args.workers)
    encode_mp4(master, args.out)
    if args.webm:
        encode_webm(master, args.out.with_suffix(".webm"))
    if args.gif:
        encode_gif(master, args.out.with_suffix(".gif"), args.gif_width, args.gif_fps)
    master.unlink()


if __name__ == "__main__":
    main()
