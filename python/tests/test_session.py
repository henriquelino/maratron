"""Tests for session idle-trim (Part A) and per-profile output resolution (Part B)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from maratron.session import Context, SessionTracker, trim_zero_ends  # noqa: E402
from maratron.models import AppConfig, Profile, Treadmill  # noqa: E402


# --------------------------------------------------------------------------- #
# Part A — trimming zeroed head/tail
# --------------------------------------------------------------------------- #
def test_trim_zero_ends_drops_head_tail_keeps_middle():
    samples = [
        [0.0, 0.0, 0.0],   # leading idle
        [3.0, 0.0, 0.0],   # leading idle
        [6.0, 5.0, 50.0],  # first movement
        [9.0, 0.0, 0.0],   # mid-run pause (must be KEPT)
        [12.0, 6.0, 60.0],
        [15.0, 0.0, 0.0],  # trailing idle
        [18.0, 0.0, 0.0],  # trailing idle
    ]
    out = trim_zero_ends(samples)
    assert out[0][1] == 5.0 and out[-1][1] == 6.0        # ends are non-zero
    assert any(r[1] == 0.0 for r in out)                 # mid-run zero retained
    assert out[0][0] == 0.0                              # rebased to start at 0
    assert [r[0] for r in out] == [0.0, 3.0, 6.0]        # spacing preserved (6,9,12 -> 0,3,6)


def test_trim_zero_ends_all_zero_or_empty():
    assert trim_zero_ends([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0]]) == []
    assert trim_zero_ends([]) == []


def test_session_uses_active_duration_not_wallclock():
    """A manual session that idles, walks, then idles: duration/pace should reflect the
    moving span, not the full wall-clock elapsed, and samples should be head/tail-trimmed."""
    cfg = AppConfig(mock=True, session_auto=True, session_idle_timeout_s=100.0,
                    session_min_distance_m=0.0)
    tracker = SessionTracker(cfg, on_finalize=lambda s: None)
    ctx = Context(weight_kg=80.0, grade_pct=0.0, stride_cm=70.0)
    tracker.start_manual(0.0, "t0", 0.0, "p", None, ctx)

    v = 5.0 / 3.6  # 5 km/h in m/s
    dist = 0.0
    for now in range(0, 31):          # 0..30 s at 1 Hz
        if 6 <= now < 24:             # walking window (~18 s)
            pps, speed = 10.0, 5.0
            dist += v
        else:                          # idle head (0-6s) and tail (24-30s)
            pps, speed = 0.0, 0.0
        tracker.update(float(now), f"t{now}", pps, dist, speed, 80.0, "p", None, "", ctx)

    session, persisted = tracker.stop(30.0, force_save=True)
    assert persisted
    # active span (~first move 6s -> last move 23s ≈ 17s) — far below the ~30s wall clock
    assert 14.0 <= session.duration_s <= 20.0
    # avg pace reflects the moving portion (~5 km/h), NOT the diluted ~3 km/h full-elapsed
    assert session.avg_speed > 4.5
    # samples exist and are trimmed: neither end is a zero-speed idle sample
    assert session.samples
    assert session.samples[0][1] > 0 and session.samples[-1][1] > 0
    assert session.samples[0][0] == 0.0


# --------------------------------------------------------------------------- #
# Part B — per-profile output mode resolution
# --------------------------------------------------------------------------- #
def test_engine_resolves_output_mode_from_active_profile():
    from maratron.engine import TreadmillEngine

    tm = Treadmill(name="T", serial_port="COM_TEST")
    profiles = {
        "g": Profile(name="g", output_mode="gamepad", treadmill="T"),
        "v": Profile(name="v", output_mode="vr", treadmill="T"),
    }
    cfg = AppConfig(mock=True, active_profile="g")
    eng = TreadmillEngine(cfg, profiles, {"T": tm}, {}, lambda *a: None, lambda s: None)
    try:
        assert eng._effective_output_mode() == "gamepad"
        eng.set_active_profile("v")
        assert eng._effective_output_mode() == "vr"
    finally:
        eng.stop()
