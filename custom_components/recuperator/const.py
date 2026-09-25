"""Constants for the Recuperator integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.const import Platform

DOMAIN = "recuperator"
MANUFACTURER = "geirskogul"
MODEL = "Breathing controller"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

# -- what the user picks when adding the integration ---------------------------

CONF_EXHAUST_SWITCH = "exhaust_switch"  # the fan that blows basement air out (inside-out)
CONF_INTAKE_SWITCH = "intake_switch"  # the fan that blows outdoor air in (outside-in)
CONF_INSIDE_SENSOR = "inside_sensor"  # probe at the basement end of the core
CONF_OUTSIDE_SENSOR = "outside_sensor"  # probe at the outdoor end of the core
CONF_PASSIVE_INTAKE = "passive_intake"  # intake with the intake fan off (passive re-ventilation)
CONF_PALETTE = "diagram_palette"  # the diagram's colour scale, as text (see diagram.py)

# -- modes ---------------------------------------------------------------------

MODE_AUTOMATIC = "automatic"
MODE_TIMED = "timed"
MODE_EXHAUST_ONLY = "exhaust_only"
MODE_INTAKE_ONLY = "intake_only"
MODES = [MODE_AUTOMATIC, MODE_TIMED, MODE_EXHAUST_ONLY, MODE_INTAKE_ONLY]

# -- phases (the state of the "Phase" sensor) ----------------------------------

PHASE_STOPPED = "stopped"
PHASE_EXHAUST = "exhaust"
PHASE_INTAKE = "intake"
PHASE_PAUSE = "pause"
PHASES = [PHASE_STOPPED, PHASE_EXHAUST, PHASE_PAUSE, PHASE_INTAKE]

# -- why a phase ended (the state of the "Last change reason" sensor) ----------

REASON_RECOVERED = "recovered"  # the far probe got close enough to the near one
REASON_SETTLED = "settled"  # the far probe stopped changing
REASON_MAX_TIME = "max_time"  # the phase hit its maximum length
REASON_COLD_LIMIT = "cold_limit"  # a cold-weather limit ended the phase
REASON_TIMED = "timed"  # timed breathing: the fixed phase length ran out
REASON_SUPPLY_DROP = "supply_drop"  # air entering the room fell too far below room temperature
REASON_SUPPLY_COLD = "supply_cold"  # air entering the room got colder than the hard floor
REASON_STARTED = "started"  # breathing was switched on
REASON_STOPPED = "stopped"  # breathing was switched off
REASONS = [
    REASON_RECOVERED,
    REASON_SETTLED,
    REASON_MAX_TIME,
    REASON_COLD_LIMIT,
    REASON_TIMED,
    REASON_SUPPLY_DROP,
    REASON_SUPPLY_COLD,
    REASON_STARTED,
    REASON_STOPPED,
]

STARTUP_WAIT_SECONDS = 60  # wait this long for the probes after switching on / a restart
FROST_FACE_TEMPERATURE = 0.5  # outdoor face never above this during a cold exhaust = frost risk

# -- why a phase is timed instead of temperature-driven ------------------------

TIMED_NOT = "no"
TIMED_BY_MODE = "mode"
TIMED_SIMILAR = "temperatures_similar"
TIMED_SENSOR = "sensor_unavailable"


# -- user-tunable settings -------------------------------------------------------


@dataclass(frozen=True)
class Setting:
    """One tunable setting: its default and the range the UI allows."""

    key: str
    default: float
    minimum: float
    maximum: float
    step: float
    unit: str | None
    icon: str


SETTINGS: tuple[Setting, ...] = (
    Setting("recovery_percent", 80, 5, 100, 1, "%", "mdi:percent"),
    Setting("settle_seconds", 15, 5, 1800, 1, "s", "mdi:timer-sand"),
    Setting("settle_delta", 0.2, 0.05, 2.0, 0.05, "°C", "mdi:thermometer-minus"),
    Setting("min_phase_seconds", 20, 5, 3600, 1, "s", "mdi:timer-outline"),
    Setting("max_phase_seconds", 120, 10, 86400, 1, "s", "mdi:timer-alert-outline"),
    Setting("pause_seconds", 1, 0, 30, 0.5, "s", "mdi:pause-circle-outline"),
    Setting("timed_phase_seconds", 60, 10, 86400, 1, "s", "mdi:timer-cog-outline"),
    Setting("similar_band", 2.0, 0, 20, 0.1, "°C", "mdi:approximately-equal"),
    Setting("cold_threshold", -5, -40, 15, 0.5, "°C", "mdi:snowflake-thermometer"),
    Setting("cold_intake_max_seconds", 300, 10, 86400, 1, "s", "mdi:snowflake-alert"),
    Setting("cold_exhaust_extra_seconds", 7, 0, 60, 1, "s", "mdi:snowflake-melt"),
    Setting("max_supply_drop", 3.0, 0.5, 20, 0.5, "°C", "mdi:thermometer-chevron-down"),
    Setting("min_supply_temperature", -30, -30, 25, 0.5, "°C", "mdi:home-thermometer-outline"),
    Setting("passive_intake_max_seconds", 1800, 60, 86400, 60, "s", "mdi:timer-sand-complete"),
    Setting("passive_flow_delta", 0.3, 0.05, 5, 0.05, "°C", "mdi:weather-windy"),
)

SETTINGS_BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}
DEFAULTS: dict[str, float] = {s.key: s.default for s in SETTINGS}
