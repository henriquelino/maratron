"""Pure control math (no I/O) extracted from the original treadmill.py loop.

The per-iteration pipeline (deltas -> pulses/sec -> normalize -> gain -> clamp ->
EMA smoothing -> snap-to-zero -> deadzone -> curve -> joy_y -> sprint decision) is
copied from treadmill.py:199-273 so behavior is preserved. The one change: the
hardcoded two-segment curve is replaced by ``interpolate_curve`` over the profile's
editable ``curve_points``.

``interpolate_curve`` is mirrored ~line-for-line by the JS in web/index.html.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import CurvePoint, Profile, SprintMethod

JOY_MAX = 32767


def interpolate_curve(x: float, points: list[CurvePoint]) -> float:
    """Piecewise-linear interpolation over sorted control points, clamped to endpoints.

    x and the returned y are both in normalized 0..1 space. Mirror of the JS
    ``interpolateCurve`` in web/index.html — keep the two in sync.
    """
    if not points:
        return x
    pts = sorted(points, key=lambda p: p[0])
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        if x1 <= x <= x2:
            if x2 == x1:
                return y2
            t = (x - x1) / (x2 - x1)
            return y1 + t * (y2 - y1)
    return pts[-1][1]


def two_segment_curve(x: float, walk_threshold: float = 0.30) -> float:
    """The original hardcoded two-segment curve, kept only to seed default curve_points."""
    if x < walk_threshold:
        return (x / walk_threshold) ** 2 * walk_threshold
    high = (x - walk_threshold) / (1.0 - walk_threshold)
    return walk_threshold + (high ** 0.6) * (1.0 - walk_threshold)


def sample_two_segment_points(walk_threshold: float = 0.30, n: int = 9) -> list[CurvePoint]:
    """Sample the legacy curve into control points (for the migrated default profile)."""
    pts: list[CurvePoint] = []
    for i in range(n):
        x = i / (n - 1)
        pts.append((round(x, 4), round(two_segment_curve(x, walk_threshold), 4)))
    return pts


@dataclass
class ControlState:
    filtered_speed: float = 0.0
    is_running: bool = False
    button_press_time: float | None = None
    last_pulse_count: int = 0
    last_arduino_ms: int = 0
    total_pulses: int = 0
    initialized: bool = False
    # rolling (arduino_ms, cumulative_pulses) samples for windowed pace averaging
    samples: list = field(default_factory=list)


@dataclass
class StepResult:
    joy_y: int = 0
    sprint_action: str | None = None  # "press" | "release" | "tap" | None
    pulses_per_sec: float = 0.0
    filtered_speed: float = 0.0
    curved: float = 0.0
    new_pulses: int = 0


def step(
    state: ControlState,
    current_pulses: int,
    arduino_ms: int,
    profile: Profile,
    poll_interval: float,
    now: float,
) -> StepResult:
    """Advance one control iteration. Mutates ``state``; returns the decision + metrics.

    ``now`` is the current wall-clock time (time.time()) used only for the
    click_release sprint timing. No serial or gamepad I/O happens here.
    """
    # First reading establishes a baseline so we don't emit a huge initial delta.
    if not state.initialized:
        state.last_pulse_count = current_pulses
        state.last_arduino_ms = arduino_ms
        state.initialized = True
        return StepResult(filtered_speed=state.filtered_speed)

    new_pulses = current_pulses - state.last_pulse_count
    time_delta_ms = arduino_ms - state.last_arduino_ms

    state.last_pulse_count = current_pulses
    state.last_arduino_ms = arduino_ms
    state.total_pulses += max(0, new_pulses)

    interval_s = time_delta_ms / 1000.0 if time_delta_ms > 0 else poll_interval
    instant_pps = new_pulses / interval_s if interval_s > 0 else 0.0

    # Rolling-window pace: average pulses over ~speed_window_s so a steady stride
    # (push spike + recovery dip) reads as one steady speed instead of oscillating.
    window_s = getattr(profile, "speed_window_s", 0.0) or 0.0
    if window_s > 0:
        state.samples.append((arduino_ms, current_pulses))
        cutoff = arduino_ms - window_s * 1000.0
        while len(state.samples) > 2 and state.samples[0][0] < cutoff:
            state.samples.pop(0)
        span_ms = arduino_ms - state.samples[0][0]
        span_pulses = current_pulses - state.samples[0][1]
        pulses_per_sec = span_pulses / (span_ms / 1000.0) if span_ms > 0 else instant_pps
    else:
        pulses_per_sec = instant_pps

    speed = pulses_per_sec / profile.max_pulses_per_second if profile.max_pulses_per_second else 0.0
    speed_with_gain = speed * profile.gain
    speed_with_gain = max(0.0, min(speed_with_gain, 1.0))

    state.filtered_speed = (
        state.filtered_speed * (1.0 - profile.smoothing) + speed_with_gain * profile.smoothing
    )

    if new_pulses == 0 and state.filtered_speed < 0.01:
        state.filtered_speed = 0.0
    if state.filtered_speed < profile.deadzone:
        state.filtered_speed = 0.0

    curved = interpolate_curve(state.filtered_speed, profile.curve_points)
    joy_y = int(curved * JOY_MAX)

    result = StepResult(
        joy_y=joy_y,
        pulses_per_sec=pulses_per_sec,
        filtered_speed=state.filtered_speed,
        curved=curved,
        new_pulses=new_pulses,
    )

    # Sprint state machine (mirrors treadmill.py:244-272).
    if profile.sprint_method != SprintMethod.NONE:
        breached = joy_y > (JOY_MAX * profile.run_threshold)

        if profile.sprint_method == SprintMethod.HOLD:
            if not state.is_running and breached:
                result.sprint_action = "press"
                state.is_running = True
            elif state.is_running and not breached:
                result.sprint_action = "release"
                state.is_running = False

        elif profile.sprint_method == SprintMethod.CLICK_RELEASE:
            if not state.is_running and breached:
                result.sprint_action = "press"
                state.is_running = True
                state.button_press_time = now
            elif state.button_press_time is not None and (now - state.button_press_time >= 0.1):
                result.sprint_action = "release"
                state.button_press_time = None
            elif state.is_running and not breached:
                state.is_running = False

    return result
