"""Pydantic v2 data models for Maratron.

These are decoupled from vgamepad on purpose: ``run_button`` is stored as a
string enum *name* (mapped to a ``vg.XUSB_BUTTON`` lazily in ``hardware.py``) so
loading/persisting profiles never imports vgamepad. That lets the web server run
on machines without the ViGEmBus driver.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def grade_pct(front_cm: float, back_cm: float, span_cm: float) -> float:
    """Incline grade % from front/back heights and the straight-line span between them.
    rise = |front - back|, run = sqrt(span^2 - rise^2), grade = rise/run * 100."""
    rise = abs((front_cm or 0.0) - (back_cm or 0.0))
    if not span_cm or span_cm <= rise:
        return 0.0
    run = math.sqrt(span_cm * span_cm - rise * rise)
    return rise / run * 100.0 if run > 0 else 0.0


class SprintMethod(str, Enum):
    NONE = "none"
    HOLD = "hold"
    CLICK_RELEASE = "click_release"


# Friendly, output-aware button catalog for the UI. ``value`` is the stored
# XUSB_GAMEPAD_* name (unchanged, resolved in hardware.py); ``label`` is the
# gamepad-friendly name; ``vr`` names the real SteamVR controller input this maps to
# (via vr_ipc.BUTTON_BITS), or None when the driver has no distinct input for it. The
# driver exposes only two usable button inputs, so only two entries carry a vr label.
BUTTON_CATALOG = [
    {"value": "XUSB_GAMEPAD_LEFT_SHOULDER",  "label": "Left Shoulder (LB)",    "vr": "Grip click"},
    {"value": "XUSB_GAMEPAD_RIGHT_SHOULDER", "label": "Right Shoulder (RB)",   "vr": None},
    {"value": "XUSB_GAMEPAD_LEFT_THUMB",     "label": "Left Stick click (L3)", "vr": "Thumbstick click"},
    {"value": "XUSB_GAMEPAD_RIGHT_THUMB",    "label": "Right Stick click (R3)", "vr": None},
    {"value": "XUSB_GAMEPAD_A",              "label": "A",                     "vr": None},
    {"value": "XUSB_GAMEPAD_B",              "label": "B",                     "vr": None},
    {"value": "XUSB_GAMEPAD_X",              "label": "X",                     "vr": None},
    {"value": "XUSB_GAMEPAD_Y",              "label": "Y",                     "vr": None},
]

# Curve points: list of [x, y] pairs in normalized 0..1 space.
# x = filtered speed (0..1), y = joystick output (0..1). Sorted by x, endpoints
# pinned at x=0 and x=1.
CurvePoint = tuple[float, float]


class Person(BaseModel):
    """A user whose body metrics drive calories/steps. Metrics aggregate per person."""

    name: str = Field(..., description="Unique person name / key")
    weight_kg: float | None = None
    height_cm: float | None = None
    stride_cm: float | None = Field(
        None, description="Walking stride length; set directly or via the calibrator."
    )

    def effective_stride_cm(self) -> float:
        if self.stride_cm and self.stride_cm > 0:
            return self.stride_cm
        if self.height_cm and self.height_cm > 0:
            return 0.43 * self.height_cm  # rough estimate from height
        return 70.0  # neutral fallback


class InclinePreset(BaseModel):
    """A back-height notch on a treadmill (front height + span are fixed per treadmill)."""

    label: str
    back_height_cm: float


class Treadmill(BaseModel):
    """A physical treadmill: pulse geometry, sizing, serial port, and incline presets.
    Shared across people; game profiles reference a treadmill + one preset."""

    name: str = Field(..., description="Unique treadmill name / key")
    amount_of_magnets: int = 11
    one_revolution_cm: float = 12.9
    serial_port: str = "COM7"
    front_height_cm: float = 19.0        # fixed; front edge of the board
    span_cm: float = 86.6                # bed/board length (front-to-back edge distance)
    presets: list[InclinePreset] = Field(default_factory=list)

    @property
    def distance_per_pulse_cm(self) -> float:
        return self.one_revolution_cm / self.amount_of_magnets if self.amount_of_magnets else 0.0

    def grade_for(self, label: str | None) -> float:
        p = next((x for x in self.presets if x.label == label), None)
        if p is None:
            return 0.0
        return grade_pct(self.front_height_cm, p.back_height_cm, self.span_cm)


class Profile(BaseModel):
    """A per-game control configuration, owned by a person and bound to a treadmill+preset."""

    name: str = Field(..., description="Unique profile name / key")
    person: str | None = Field(None, description="Owning person name")
    treadmill: str | None = Field(None, description="Treadmill this profile uses")
    incline_preset: str | None = Field(None, description="Chosen incline preset label on that treadmill")
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

    output_mode: Literal["gamepad", "vr", "both"] = Field(
        "gamepad", description="Where this profile's control output goes (per game)."
    )

    curve_points: list[CurvePoint] = Field(
        default_factory=lambda: [(0.0, 0.0), (1.0, 1.0)],
        description="Speed->joystick response curve, normalized 0..1, sorted by x.",
    )


class AppConfig(BaseModel):
    """Global app settings. Per-treadmill hardware lives on Treadmill; per-user body
    metrics live on Person. Active selection picks a person + game profile (the profile
    references its treadmill + incline preset)."""

    baudrate: int = 115200
    poll_interval_s: float = 0.1

    active_person: str | None = None
    active_profile: str | None = None   # active game profile

    mock: bool = False
    auto_switch: bool = False

    output_mode: Literal["gamepad", "vr", "both"] = "gamepad"  # default/fallback; per-profile overrides
    vr_invert_y: bool = False             # flip forward direction of the VR thumbstick
    vr_role: Literal["optout", "left", "right", "treadmill"] = "treadmill"  # keep-both default; set via --vr-role

    session_auto: bool = True             # auto start/stop sessions on movement
    session_idle_timeout_s: float = 15.0  # stop a session after this long with no movement
    session_min_distance_m: float = 50.0  # discard sessions shorter than this (junk/test)

    metric_units: bool = True


class Session(BaseModel):
    """One activity-log row, with a downsampled pace time-series."""

    started_at: str
    ended_at: str | None = None
    duration_s: float = 0.0
    distance_m: float = 0.0
    calories: float = 0.0
    steps: int = 0
    avg_speed: float = 0.0          # km/h
    max_speed: float = 0.0          # km/h
    grade_pct: float = 0.0          # incline while walking
    person: str | None = None       # who walked it (metrics aggregate per person)
    treadmill: str | None = None
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
    grade_pct: float = 0.0
    active_profile: str | None = None
    active_person: str | None = None
    active_treadmill: str | None = None
    game_focused: bool = False
    error: str | None = None

    # current (in-progress) session
    session_active: bool = False
    session_auto: bool = False
    session_duration_s: float = 0.0
    session_distance_m: float = 0.0
    session_avg_speed: float = 0.0
    session_calories: float = 0.0
    session_steps: int = 0
