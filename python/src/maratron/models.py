"""Pydantic v2 data models for Maratron.

These are decoupled from vgamepad on purpose: ``run_button`` is stored as a
string enum *name* (mapped to a ``vg.XUSB_BUTTON`` lazily in ``hardware.py``) so
loading/persisting profiles never imports vgamepad. That lets the web server run
on machines without the ViGEmBus driver.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SprintMethod(str, Enum):
    NONE = "none"
    HOLD = "hold"
    CLICK_RELEASE = "click_release"


# Button names offered in the UI dropdown. These mirror ``vg.XUSB_BUTTON`` member
# names; hardware.py resolves them via getattr so this list stays dependency-free.
RUN_BUTTONS = [
    "XUSB_GAMEPAD_LEFT_SHOULDER",
    "XUSB_GAMEPAD_RIGHT_SHOULDER",
    "XUSB_GAMEPAD_LEFT_THUMB",
    "XUSB_GAMEPAD_RIGHT_THUMB",
    "XUSB_GAMEPAD_A",
    "XUSB_GAMEPAD_B",
    "XUSB_GAMEPAD_X",
    "XUSB_GAMEPAD_Y",
]

# Curve points: list of [x, y] pairs in normalized 0..1 space.
# x = filtered speed (0..1), y = joystick output (0..1). Sorted by x, endpoints
# pinned at x=0 and x=1.
CurvePoint = tuple[float, float]


class Profile(BaseModel):
    """A per-game control configuration."""

    name: str = Field(..., description="Unique profile name / key")
    game_window: str | None = Field(
        None, description="Substring matched against the foreground window title for auto-switch"
    )

    max_pulses_per_second: float = Field(
        30.0,
        description="Pulses/sec that maps to 100% speed. Lower = more sensitive (walk less to max out).",
    )
    gain: float = Field(1.1, description="Direct multiplier on movement speed before curve mapping.")
    deadzone: float = Field(0.02, description="Speeds below this (0..1) are forced to zero.")
    smoothing: float = Field(
        0.3, description="EMA responsiveness 0..1 (higher = snappier, lower = smoother)."
    )
    speed_window_s: float = Field(
        0.8,
        description="Rolling window (seconds) to average pulses over before smoothing. Longer = "
        "steadier at a constant pace (evens out per-stride push/recovery spikes), but slower to "
        "react. ~0.6-1.0s covers one stride. 0 = instantaneous (old behavior).",
    )
    run_threshold: float = Field(
        0.90, description="Joystick output (0..1) at which the sprint button activates."
    )

    sprint_method: SprintMethod = Field(SprintMethod.HOLD)
    run_button: str = Field("XUSB_GAMEPAD_LEFT_SHOULDER", description="vg.XUSB_BUTTON member name")

    curve_points: list[CurvePoint] = Field(
        default_factory=lambda: [(0.0, 0.0), (1.0, 1.0)],
        description="Speed->joystick response curve, normalized 0..1, sorted by x.",
    )


class AppConfig(BaseModel):
    """App-level settings (single treadmill / single user)."""

    serial_port: str = "COM7"
    baudrate: int = 115200
    active_profile: str | None = None

    amount_of_magnets: int = 11
    one_revolution_cm: float = 12.9
    poll_interval_s: float = 0.1

    mock: bool = False
    auto_switch: bool = False

    session_auto: bool = True             # auto start/stop sessions on movement
    session_idle_timeout_s: float = 15.0  # stop a session after this long with no movement

    weight_kg: float | None = None
    incline_pct: float = 0.0
    metric_units: bool = True

    @property
    def distance_per_pulse_cm(self) -> float:
        return self.one_revolution_cm / self.amount_of_magnets


class Session(BaseModel):
    """One activity-log row, with a downsampled pace time-series."""

    started_at: str
    ended_at: str | None = None
    duration_s: float = 0.0
    distance_m: float = 0.0
    calories: float = 0.0
    avg_speed: float = 0.0          # km/h
    max_speed: float = 0.0          # km/h
    profile: str | None = None
    game: str | None = None         # the profile's game_window at start
    game_focused_s: float = 0.0     # seconds the matching game window was focused
    auto: bool = True               # started automatically vs manually
    # [[t_offset_s, speed_kmh, joystick_pct], ...] sampled ~every few seconds
    samples: list = Field(default_factory=list)


class EngineStatus(BaseModel):
    """Live-metrics payload pushed over the WebSocket."""

    connected: bool = False
    mock: bool = False
    running: bool = False

    pulses_per_sec: float = 0.0
    filtered_speed: float = 0.0
    curved: float = 0.0
    joystick_pct: float = 0.0
    sprinting: bool = False

    total_pulses: int = 0
    distance_m: float = 0.0
    distance_km: float = 0.0
    speed_kmh: float = 0.0
    active_profile: str | None = None
    game_focused: bool = False
    error: str | None = None

    # current (in-progress) session
    session_active: bool = False
    session_auto: bool = False
    session_duration_s: float = 0.0
    session_distance_m: float = 0.0
    session_avg_speed: float = 0.0
    session_calories: float = 0.0
