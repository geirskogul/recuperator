"""Constants for the Recuperator integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.const import Platform, UnitOfTemperature

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
CONF_PHASE_LIMIT = "phase_limit"  # keep one phase shorter than the other (see PHASE_LIMITS)

# Every entity a recuperator is wired to (kept up to date when one is renamed).
WIRING_KEYS = (
    CONF_EXHAUST_SWITCH,
    CONF_INTAKE_SWITCH,
    CONF_INSIDE_SENSOR,
    CONF_OUTSIDE_SENSOR,
    "link_exhaust_switch",
    "link_intake_switch",
)


def unique_id_for(data) -> str:
    """A recuperator's unique ID: its exhaust and intake fans."""
    return f"{data[CONF_EXHAUST_SWITCH]}|{data[CONF_INTAKE_SWITCH]}"


# -- modes ---------------------------------------------------------------------

MODE_AUTOMATIC = "automatic"
MODE_TIMED = "timed"
MODE_EXHAUST_ONLY = "exhaust_only"
MODE_INTAKE_ONLY = "intake_only"
MODES = [MODE_AUTOMATIC, MODE_TIMED, MODE_EXHAUST_ONLY, MODE_INTAKE_ONLY]

# -- phase limit: one phase kept shorter than the other --------------------------

LIMIT_OFF = "off"
LIMIT_INTAKE = "limited_intake"  # intake at most Phase limit share of the last exhaust
LIMIT_EXHAUST = "limited_exhaust"  # exhaust at most Phase limit share of the last intake
PHASE_LIMITS = [LIMIT_OFF, LIMIT_INTAKE, LIMIT_EXHAUST]

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
REASON_PHASE_LIMIT = "phase_limit"  # the phase limit kept it shorter than the other phase
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
    REASON_PHASE_LIMIT,
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
    """One tunable setting: its default and the range the UI allows.

    temperature: an absolute temperature (°C), shown in the user's units.
    Temperature *differences* stay in °C, which Home Assistant cannot convert.
    advanced: its number entity starts disabled on new installs (still in
    Configure, Settings), to keep the device page to the everyday settings.
    """

    key: str
    default: float
    minimum: float
    maximum: float
    step: float
    unit: str | None
    icon: str
    temperature: bool = False
    advanced: bool = False


C = UnitOfTemperature.CELSIUS

SETTINGS: tuple[Setting, ...] = (
    Setting("recovery_percent", 80, 5, 100, 1, "%", "mdi:percent"),
    Setting("settle_seconds", 15, 5, 1800, 1, "s", "mdi:timer-sand", advanced=True),
    Setting("settle_delta", 0.2, 0.05, 2.0, 0.05, C, "mdi:thermometer-minus", advanced=True),
    Setting("min_phase_seconds", 20, 5, 3600, 1, "s", "mdi:timer-outline"),
    Setting("max_phase_seconds", 120, 10, 86400, 1, "s", "mdi:timer-alert-outline"),
    Setting("pause_seconds", 1, 0, 30, 0.5, "s", "mdi:pause-circle-outline"),
    Setting("timed_exhaust_seconds", 60, 10, 86400, 1, "s", "mdi:timer-arrow-up-outline"),
    Setting("timed_intake_seconds", 60, 10, 86400, 1, "s", "mdi:timer-arrow-down-outline"),
    Setting("similar_band", 2.0, 0, 20, 0.1, C, "mdi:approximately-equal", advanced=True),
    Setting("cold_threshold", -5, -40, 15, 0.5, C, "mdi:snowflake-thermometer", temperature=True),
    Setting("cold_intake_max_seconds", 300, 10, 86400, 1, "s", "mdi:snowflake-alert"),
    Setting("cold_exhaust_extra_seconds", 7, 0, 60, 1, "s", "mdi:snowflake-melt", advanced=True),
    Setting("max_supply_drop", 3.0, 0.5, 20, 0.5, C, "mdi:thermometer-chevron-down"),
    Setting(
        "min_supply_temperature", -30, -30, 25, 0.5, C, "mdi:home-thermometer-outline",
        temperature=True, advanced=True,
    ),
    Setting("passive_intake_max_seconds", 1800, 60, 86400, 60, "s", "mdi:timer-sand-complete"),
    Setting("passive_flow_delta", 0.3, 0.05, 5, 0.05, C, "mdi:weather-windy", advanced=True),
    Setting("phase_limit_percent", 90, 10, 100, 1, "%", "mdi:scale-unbalanced"),
    # Replay: used by the Create replay button, and saved from the last Create replay action.
    Setting("replay_hours", 24, 0.25, 168, 0.25, "h", "mdi:history"),
    Setting("replay_playback_seconds", 60, 5, 900, 1, "s", "mdi:play-speed"),
    Setting("replay_frames", 0, 0, 1440, 1, None, "mdi:filmstrip"),
)

SETTINGS_BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}
DEFAULTS: dict[str, float] = {s.key: s.default for s in SETTINGS}

# Settings shown on the Replay device and Configure page, not with the cycle settings.
REPLAY_KEYS = ("replay_hours", "replay_playback_seconds", "replay_frames")

# Before 0.2.0 one "Timed phase" length served both phases; it seeds the two new ones.
LEGACY_TIMED_PHASE = "timed_phase_seconds"

# -- a linked unit: a second recuperator or a single fan, breathing opposite ------

CONF_LINK_TYPE = "link_type"
CONF_LINK_EXHAUST_SWITCH = "link_exhaust_switch"
CONF_LINK_INTAKE_SWITCH = "link_intake_switch"

LINK_NONE = "none"
LINK_RECUPERATOR = "recuperator"  # its own exhaust and intake fans (biphasic)
LINK_INTAKE_FAN = "intake_fan"  # one fan blowing outdoor air in
LINK_EXHAUST_FAN = "exhaust_fan"  # one fan blowing indoor air out
LINK_TYPES = [LINK_NONE, LINK_RECUPERATOR, LINK_INTAKE_FAN, LINK_EXHAUST_FAN]

# What the linked unit is doing (the state of the "Linked unit" sensor).
LINKED_EXHAUST = "exhaust"
LINKED_INTAKE = "intake"
LINKED_IDLE = "idle"
LINKED_NOT_LINKED = "not_linked"
LINKED_STATES = [LINKED_EXHAUST, LINKED_INTAKE, LINKED_IDLE, LINKED_NOT_LINKED]
