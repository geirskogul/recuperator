"""A simulated hour of two synced recuperators, driven by the integration's own cycle logic.

Each unit gets a small physics model: a ceramic core in slices that stores and
returns heat, a hose on the outdoor side whose air comes back first on every
intake, and two DS18B20-like probes (lagging, 1/16 °C steps, a reading every
2 s). Their readings feed BreathingLogic and sync.step_pair exactly as the
controller does, once a second, so every switch in the result is one the
integration would have made.

Home Assistant does not need to be installed: the pure modules are loaded on
their own, with a stand-in for the two names they import from homeassistant.const.

    python tools/pair_sim.py            # prints every switch and the cycle lengths
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import enum
import importlib
import math
from pathlib import Path
import random
import sys
import types

import numpy as np

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "recuperator"
PACKAGE = "recuperator_sim"  # the integration's folder, imported without its __init__.py

SUBSTEP = 0.25  # s, the physics step
PROBE_EVERY = 2  # s between probe reports, like a typical ESPHome DS18B20
PROBE_RESOLUTION = 0.0625  # °C, a DS18B20 at 12 bits
PROBE_NOISE = 0.02  # °C


# -- loading the integration's pure modules ------------------------------------------


def _stub_homeassistant_const() -> None:
    """The two names const.py takes from Home Assistant, when it is not installed."""
    try:
        importlib.import_module("homeassistant.const")
        return
    except ImportError:
        pass

    class Platform(enum.StrEnum):
        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"
        IMAGE = "image"
        NUMBER = "number"
        SELECT = "select"
        SENSOR = "sensor"
        SWITCH = "switch"
        TEXT = "text"

    class UnitOfTemperature(enum.StrEnum):
        CELSIUS = "°C"
        FAHRENHEIT = "°F"
        KELVIN = "K"

    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    const = types.ModuleType("homeassistant.const")
    const.Platform, const.UnitOfTemperature = Platform, UnitOfTemperature
    sys.modules.update({"homeassistant": ha, "homeassistant.const": const})


def integration(module: str) -> types.ModuleType:
    """One of the integration's pure modules (logic, sync, replay, gif, ...)."""
    if PACKAGE not in sys.modules:
        _stub_homeassistant_const()
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(COMPONENT)]
        sys.modules[PACKAGE] = package
    return importlib.import_module(f"{PACKAGE}.{module}")


logic_mod = integration("logic")
sync_mod = integration("sync")
const = integration("const")


# -- the weather ----------------------------------------------------------------------


@dataclass(frozen=True)
class Weather:
    """A 20 °C room, and outdoor air cooling over the hour (with a little gusting)."""

    room: float = 20.0
    outdoor_start: float = 10.0
    outdoor_end: float = 8.0
    hour: float = 3600.0

    def room_at(self, t: float) -> float:
        """The room, with the heating's gentle swing."""
        return self.room + 0.08 * math.sin(2 * math.pi * t / 1500)

    def outdoor_at(self, t: float) -> float:
        """Eases from outdoor_start (before the hour) to outdoor_end (at its end)."""
        share = min(max(t / self.hour, 0.0), 1.0)
        eased = 0.5 - 0.5 * math.cos(math.pi * share)
        gust = 0.12 * math.sin(2 * math.pi * t / 410) + 0.07 * math.sin(2 * math.pi * t / 97 + 1.3)
        return self.outdoor_start + (self.outdoor_end - self.outdoor_start) * eased + gust


# -- one unit's hardware ----------------------------------------------------------------


@dataclass(frozen=True)
class CoreSpec:
    """The physical unit: a ceramic core, its fans' airflow, the outdoor hose and the probes."""

    capacity: float = 4200.0  # J/K stored by the ceramic (about 5 kg)
    flow: float = 9.0  # W/K carried by the air with a fan on (about 27 m³/h)
    ntu: float = 12.0  # how well the core's channels exchange heat with the air
    cells: int = 36  # slices along the core
    conduction: float = 0.004  # heat shared between neighbouring slices per step
    duct_seconds: float = 20.0  # how long air takes to travel the outdoor hose
    duct_tau: float = 150.0  # s for air standing in the hose to reach the outdoor temperature
    probe_tau_flow: float = 8.0  # s, a probe in moving air
    probe_tau_still: float = 45.0  # s, a probe in still air (during the pause)


class Core:
    """The core in slices (index 0 at the room end), the hose's air, and what the probes feel.

    Exhaust pushes room air through the slices towards the hose; intake pulls
    the hose's air (the last exhaust's first, then outdoor air) back through
    them into the room. In each slice the air approaches the ceramic's
    temperature and the ceramic takes up the difference.
    """

    def __init__(self, spec: CoreSpec, room: float, outdoor: float) -> None:
        self.spec = spec
        self.solid = np.linspace(room, outdoor, spec.cells)
        self.duct = np.full(max(1, round(spec.duct_seconds / SUBSTEP)), outdoor)  # index 0 at the core
        self.inside_air, self.outside_air = room, outdoor  # the air at each probe
        self.inside_probe, self.outside_probe = room, outdoor  # what each probe's sensor feels
        self._keep = math.exp(-spec.ntu / spec.cells)  # the share of the air-to-ceramic gap left per slice
        self._gain = spec.flow * SUBSTEP / (spec.capacity / spec.cells)

    def advance(self, seconds: float, direction: int, room: float, outdoor: float) -> None:
        """Run the physics for a while; direction +1 exhaust, -1 intake, 0 fans off."""
        for _ in range(round(seconds / SUBSTEP)):
            self._substep(direction, room, outdoor)

    def _substep(self, direction: int, room: float, outdoor: float) -> None:
        self.duct += (outdoor - self.duct) * SUBSTEP / self.spec.duct_tau
        if direction > 0:
            self.inside_air, self.outside_air = room, self._exhaust(room)
        elif direction < 0:
            self.outside_air = self.duct[0]
            self.inside_air = self._intake(outdoor)
        else:
            self.inside_air = 0.7 * self.solid[0] + 0.3 * room
            self.outside_air = 0.7 * self.solid[-1] + 0.3 * self.duct[0]
        self._conduct()
        tau = self.spec.probe_tau_flow if direction else self.spec.probe_tau_still
        self.inside_probe += (self.inside_air - self.inside_probe) * SUBSTEP / tau
        self.outside_probe += (self.outside_air - self.outside_probe) * SUBSTEP / tau

    def _blow(self, air: float, order: range) -> float:
        """Pass one step's air through the slices in this order; returns the air leaving the core."""
        solid = self.solid
        for i in order:
            leaving = solid[i] + (air - solid[i]) * self._keep
            solid[i] += self._gain * (air - leaving)
            air = leaving
        return air

    def _exhaust(self, room: float) -> float:
        leaving = self._blow(room, range(len(self.solid)))
        self.duct = np.roll(self.duct, 1)
        self.duct[0] = leaving
        return leaving

    def _intake(self, outdoor: float) -> float:
        arriving = self.duct[0]
        self.duct = np.roll(self.duct, -1)
        self.duct[-1] = outdoor
        return self._blow(arriving, range(len(self.solid) - 1, -1, -1))

    def _conduct(self) -> None:
        s = self.solid
        s[1:-1] += self.spec.conduction * (s[:-2] - 2 * s[1:-1] + s[2:])


def probe_report(feels: float, rng: random.Random) -> float:
    """What a DS18B20 reports: a little noise, in 1/16 °C steps."""
    noisy = feels + rng.gauss(0, PROBE_NOISE)
    return round(round(noisy / PROBE_RESOLUTION) * PROBE_RESOLUTION, 4)


# -- one recuperator: hardware plus the integration's logic -------------------------------


@dataclass
class Recuperator:
    """A simulated unit and the BreathingLogic that runs it, with its probes' latest reports."""

    name: str
    core: Core
    settings: logic_mod.Settings
    logic: logic_mod.BreathingLogic = field(default_factory=logic_mod.BreathingLogic)
    mode: str = const.MODE_AUTOMATIC
    inside: float | None = None
    outside: float | None = None

    def unit(self) -> sync_mod.Unit:
        return sync_mod.Unit(self.logic, self.mode, self.settings, self.inside, self.outside)

    def direction(self) -> int:
        """Which way the fans blow: the exhaust fan +1, the intake fan -1, none 0."""
        return {const.PHASE_EXHAUST: 1, const.PHASE_INTAKE: -1}.get(self.logic.phase, 0)

    def report_probes(self, now: float, rng: random.Random) -> None:
        """New probe states; the logic records the ones that changed, as the controller does."""
        for probe, feels, attr in (
            (logic_mod.INSIDE, self.core.inside_probe, "inside"),
            (logic_mod.OUTSIDE, self.core.outside_probe, "outside"),
        ):
            value = probe_report(feels, rng)
            if value != getattr(self, attr):
                setattr(self, attr, value)
                self.logic.record(probe, now, value)


# -- the run ------------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitSample:
    """One unit at one second."""

    phase: str
    inside: float | None
    outside: float | None
    profile: np.ndarray  # the ceramic's temperature per slice, room end first
    flow: int  # +1 exhaust, -1 intake, 0 off
    elapsed: float  # seconds into the running phase
    last_reason: str | None
    heat_recovery: float | None
    recovered: float | None  # % of the gap the far end has closed in the running phase


@dataclass(frozen=True)
class Switch:
    """A phase change of one unit."""

    t: float
    unit: str
    ended: str
    reason: str | None
    seconds: float


@dataclass
class Run:
    """The recorded hour: a sample per second for each unit, and every switch."""

    start: datetime
    weather: Weather
    names: tuple[str, str]
    times: list[float] = field(default_factory=list)
    room: list[float] = field(default_factory=list)
    outdoor: list[float] = field(default_factory=list)
    samples: tuple[list[UnitSample], list[UnitSample]] = field(default_factory=lambda: ([], []))
    switches: list[Switch] = field(default_factory=list)
    exhaust_starts: list[float] = field(default_factory=list)  # the first unit's, warm-up included

    def last_cycle(self, t: float) -> float | None:
        """The length of the last whole breath (exhaust start to exhaust start) finished by time t."""
        done = [s for s in self.exhaust_starts if s <= t]
        return done[-1] - done[-2] if len(done) > 1 else None


def default_settings() -> logic_mod.Settings:
    """The integration's defaults, except a Maximum phase long enough for the core to set the rhythm."""
    return logic_mod.Settings.from_mapping({"max_phase_seconds": 300, "pause_seconds": 5})


def make_pair(
    weather: Weather, specs: tuple[CoreSpec, CoreSpec], names: tuple[str, str], start: float
) -> tuple[Recuperator, Recuperator]:
    """Two units whose cores start between the room and the outdoor temperature."""
    room, outdoor = weather.room_at(start), weather.outdoor_at(start)
    a, b = (Recuperator(name, Core(spec, room, outdoor), default_settings()) for name, spec in zip(names, specs))
    return a, b


def _recovered(unit: Recuperator) -> float | None:
    """How far the running phase's far end has got, as the Recovered rule sees it (%)."""
    logic = unit.logic
    if logic.phase not in (const.PHASE_EXHAUST, const.PHASE_INTAKE) or None in (unit.inside, unit.outside):
        return None
    ref, far = logic._ref_far(logic.phase, unit.inside, unit.outside)
    return logic._recovered(ref, far) * 100


def _sample(unit: Recuperator, now: float) -> UnitSample:
    logic = unit.logic
    started = now if logic.phase_started is None else logic.phase_started
    return UnitSample(
        phase=logic.phase,
        inside=unit.inside,
        outside=unit.outside,
        profile=unit.core.solid.copy(),
        flow=unit.direction(),
        elapsed=now - started,
        last_reason=logic.last_reason,
        heat_recovery=logic.heat_recovery,
        recovered=_recovered(unit),
    )


def _note_switches(run: Run, now: float, pair: tuple[Recuperator, Recuperator], before: list[tuple]) -> None:
    t = now - run.start.timestamp()
    for unit, (phase, started) in zip(pair, before):
        if unit.logic.phase != phase and phase in (const.PHASE_EXHAUST, const.PHASE_INTAKE):
            run.switches.append(Switch(t, unit.name, phase, unit.logic.last_reason, now - started))


def simulate(
    weather: Weather = Weather(),
    specs: tuple[CoreSpec, CoreSpec] = (CoreSpec(), CoreSpec(capacity=4000.0, flow=8.4, duct_seconds=35.0)),
    names: tuple[str, str] = ("West recuperator", "East recuperator"),
    rule: str = const.SYNC_EITHER,
    warmup: float = 7200.0,
    start: datetime = datetime(2026, 10, 10, 18, 0),
    seed: int = 7,
) -> Run:
    """Breathe the pair from `warmup` seconds before the hour, and record the hour itself."""
    rng = random.Random(seed)
    pair = make_pair(weather, specs, names, -warmup)
    epoch = start.timestamp()
    run = Run(start, weather, names)
    for second in range(-int(warmup), int(weather.hour) + 1):
        _tick(run, pair, rule, epoch, second, rng)
    return run


def _tick(run: Run, pair: tuple[Recuperator, Recuperator], rule: str, epoch: float, second: int, rng: random.Random) -> None:
    """One second: the physics, the probes' reports, then the pair's step (as the controller's tick)."""
    t, now = float(second), epoch + second
    room, outdoor = run.weather.room_at(t), run.weather.outdoor_at(t)
    for unit in pair:
        unit.core.advance(1.0, unit.direction(), room, outdoor)
        if second % PROBE_EVERY == 0:
            unit.report_probes(now, rng)
    before = [(u.logic.phase, u.logic.phase_started or now) for u in pair]
    _breathe(pair, rule, now)
    if pair[0].logic.phase == const.PHASE_EXHAUST and before[0][0] != const.PHASE_EXHAUST:
        run.exhaust_starts.append(t)
    if t >= 0:
        _note_switches(run, now, pair, before)
        run.times.append(t)
        run.room.append(room)
        run.outdoor.append(outdoor)
        for samples, unit in zip(run.samples, pair):
            samples.append(_sample(unit, now))


def _breathe(pair: tuple[Recuperator, Recuperator], rule: str, now: float) -> None:
    a, b = pair
    for unit in pair:
        if unit.logic.phase == const.PHASE_STOPPED:
            unit.logic.start(now, unit.mode, unit.settings, unit.inside, unit.outside)
    sync_mod.step_pair(now, a.unit(), b.unit(), rule)


# -- a summary on the terminal ----------------------------------------------------------------


REASON_TEXT = {
    "recovered": "Recovered",
    "settled": "Settled",
    "max_time": "Maximum time",
    "cold_limit": "Cold limit",
    "timed": "Timed",
    "started": "Started",
    "stopped": "Stopped",
    "supply_cold": "Supply below floor",
    "supply_drop": "Supply colder than room",
    "phase_limit": "Phase limit",
    "drying": "Drying",
    "synced": "Synced recuperator switched",
}


def cycle_lengths(run: Run) -> list[float]:
    """Seconds between the first unit's successive exhaust starts within the hour (out, pause, in, pause)."""
    starts = [t for t in run.exhaust_starts if t >= 0]
    return [b - a for a, b in zip(starts, starts[1:])]


def summary(run: Run) -> str:
    lines = [f"{'time':>6}  {'unit':<18} {'ended':<8} {'after':>6}  reason"]
    for s in run.switches:
        lines.append(f"{s.t:6.0f}  {s.unit:<18} {s.ended:<8} {s.seconds:5.0f}s  {REASON_TEXT.get(s.reason, s.reason)}")
    cycles = cycle_lengths(run)
    lines.append("cycles: " + ", ".join(f"{c:.0f}" for c in cycles))
    if cycles:
        lines.append(f"mean cycle {sum(cycles) / len(cycles):.0f} s over {len(cycles)} cycles")
    for name, samples in zip(run.names, run.samples):
        hr = samples[-1].heat_recovery
        lines.append(f"{name}: heat recovery {hr:.0f} %" if hr is not None else f"{name}: heat recovery unknown")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary(simulate()))
