"""Parity + unit tests for maratron.control.

Guards that the extracted pipeline reproduces the original treadmill.py loop math,
and that interpolate_curve behaves.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from maratron.control import (  # noqa: E402
    ControlState,
    interpolate_curve,
    sample_two_segment_points,
    step,
    two_segment_curve,
)
from maratron.models import Profile, SprintMethod  # noqa: E402


def _old_loop_joy(pulse_seq, profile, poll_interval=0.1):
    """Reference: the original treadmill.py scalar math (two-segment curve, HOLD sprint)."""
    filtered_speed = 0.0
    is_running = False
    last_pc = pulse_seq[0][0]
    last_ms = pulse_seq[0][1]
    out = []
    for current_pulses, arduino_ms in pulse_seq[1:]:
        new_pulses = current_pulses - last_pc
        time_delta_ms = arduino_ms - last_ms
        last_pc, last_ms = current_pulses, arduino_ms

        interval_s = time_delta_ms / 1000.0 if time_delta_ms > 0 else poll_interval
        pps = new_pulses / interval_s if interval_s > 0 else 0.0
        speed = pps / profile.max_pulses_per_second
        speed = max(0.0, min(speed * profile.gain, 1.0))
        filtered_speed = filtered_speed * (1 - profile.smoothing) + speed * profile.smoothing
        if new_pulses == 0 and filtered_speed < 0.01:
            filtered_speed = 0.0
        if filtered_speed < profile.deadzone:
            filtered_speed = 0.0
        curved = two_segment_curve(filtered_speed, 0.30)
        joy_y = int(curved * 32767)

        breached = joy_y > 32767 * profile.run_threshold
        sprint = None
        if not is_running and breached:
            sprint, is_running = "press", True
        elif is_running and not breached:
            sprint, is_running = "release", False
        out.append((joy_y, sprint))
    return out


def test_step_parity_with_old_loop():
    """step() with densely-sampled two-segment curve_points matches the old loop closely."""
    profile = Profile(
        name="p",
        max_pulses_per_second=110,
        gain=1.2,
        deadzone=0.01,
        smoothing=0.6,
        run_threshold=0.80,
        sprint_method=SprintMethod.HOLD,
        speed_window_s=0,  # instantaneous, to compare against the original loop
        curve_points=sample_two_segment_points(0.30, n=201),  # dense -> near-exact curve
    )

    # Simulated: ramp up then hold then stop. (pulse_count, arduino_ms) pairs @100ms.
    seq = []
    pc, ms = 0, 1000
    deltas = [0, 2, 5, 9, 12, 12, 12, 8, 3, 0, 0]
    for d in deltas:
        pc += d
        ms += 100
        seq.append((pc, ms))

    ref = _old_loop_joy(seq, profile)

    state = ControlState()
    got = []
    now = 0.0
    for current_pulses, arduino_ms in seq:
        now += 0.1
        r = step(state, current_pulses, arduino_ms, profile, 0.1, now)
        if state.initialized and (current_pulses, arduino_ms) != seq[0]:
            got.append((r.joy_y, r.sprint_action))

    assert len(got) == len(ref)
    for (jy, sp), (rjy, rsp) in zip(got, ref):
        assert abs(jy - rjy) <= 40, (jy, rjy)  # tiny curve-sampling tolerance
        assert sp == rsp


def test_interpolate_curve_endpoints_and_midpoint():
    pts = [(0.0, 0.0), (1.0, 1.0)]
    assert interpolate_curve(-0.5, pts) == 0.0
    assert interpolate_curve(1.5, pts) == 1.0
    assert abs(interpolate_curve(0.5, pts) - 0.5) < 1e-9


def test_interpolate_curve_segments():
    pts = [(0.0, 0.0), (0.5, 0.1), (1.0, 1.0)]
    assert abs(interpolate_curve(0.25, pts) - 0.05) < 1e-9
    assert abs(interpolate_curve(0.75, pts) - 0.55) < 1e-9


def test_interpolate_curve_unsorted_input():
    pts = [(1.0, 1.0), (0.0, 0.0), (0.5, 0.2)]
    assert abs(interpolate_curve(0.25, pts) - 0.1) < 1e-9


def test_speed_window_smooths_oscillation():
    """A steady average pace with alternating push/recovery pulses should read steadier
    with a rolling window than instantaneously."""
    # Alternating deltas around a mean of 6 pulses/100ms (push 10, recovery 2).
    seq = []
    pc, ms = 0, 1000
    for d in [0, 10, 2, 10, 2, 10, 2, 10, 2]:
        pc += d
        ms += 100
        seq.append((pc, ms))

    def run(window):
        p = Profile(name="w", max_pulses_per_second=100, gain=1.0, smoothing=1.0,
                    deadzone=0.0, speed_window_s=window, curve_points=[(0, 0), (1, 1)])
        st = ControlState()
        pps = []
        for cur, t in seq:
            r = step(st, cur, t, p, 0.1, 0.0)
            if st.initialized and (cur, t) != seq[0]:
                pps.append(r.pulses_per_sec)
        return pps[3:]  # skip warm-up

    def spread(xs):
        return max(xs) - min(xs)

    assert spread(run(0.0)) > spread(run(0.8))  # window reduces oscillation


def test_first_reading_is_baseline():
    profile = Profile(name="p")
    state = ControlState()
    r = step(state, 500, 5000, profile, 0.1, 0.0)
    assert r.joy_y == 0
    assert state.last_pulse_count == 500
    assert state.total_pulses == 0
