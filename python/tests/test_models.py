"""Tests for the people/treadmill model helpers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from maratron.models import InclinePreset, Person, Treadmill, grade_pct  # noqa: E402
from maratron.session import estimate_calories, estimate_steps  # noqa: E402


def test_grade_pct():
    # front 19, back 9, span 86.6 (straight-line) -> ~11.6%
    assert abs(grade_pct(19, 9, 86.6) - 11.62) < 0.1
    # flatter as back rises toward front
    assert grade_pct(19, 15, 86.6) < grade_pct(19, 9, 86.6)
    # equal heights -> flat
    assert grade_pct(19, 19, 86.6) == 0.0
    # degenerate span -> 0
    assert grade_pct(19, 9, 5) == 0.0


def test_treadmill_grade_and_distance():
    t = Treadmill(name="t", amount_of_magnets=11, one_revolution_cm=12.9, front_height_cm=19,
                  span_cm=86.6, presets=[InclinePreset(label="steep", back_height_cm=9),
                                          InclinePreset(label="gentle", back_height_cm=15)])
    assert abs(t.distance_per_pulse_cm - 12.9 / 11) < 1e-9
    assert t.grade_for("steep") > t.grade_for("gentle") > 0
    assert t.grade_for("nonexistent") == 0.0


def test_person_stride():
    assert Person(name="p", stride_cm=72).effective_stride_cm() == 72
    assert abs(Person(name="p", height_cm=180).effective_stride_cm() - 0.43 * 180) < 1e-9
    assert Person(name="p").effective_stride_cm() == 70.0  # fallback


def test_incline_increases_calories():
    flat = estimate_calories(1000, 600, 75, grade_pct=0)
    steep = estimate_calories(1000, 600, 75, grade_pct=11.6)
    assert steep > flat > 0
    assert estimate_calories(1000, 600, None) == 0.0


def test_steps():
    assert estimate_steps(100, 70) == int(100 / 0.70)
    assert estimate_steps(0, 70) == 0
    assert estimate_steps(100, None) == 0
