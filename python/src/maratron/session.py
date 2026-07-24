"""Session tracking: auto start/stop on movement, pace time-series, in-game time.

The engine feeds one `update()` per loop iteration with the current metrics plus the
active context (person / treadmill / weight / grade / stride). A session auto-starts when
movement begins and auto-stops after `session_idle_timeout_s` of no movement (unless
started manually). On stop it builds a `Session` and hands it to `on_finalize`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import AppConfig, Session

MOVE_PPS = 1.0          # pulses/sec above which we consider the belt "moving"
SAMPLE_INTERVAL_S = 3.0  # how often to record a pace sample


def estimate_calories(distance_m: float, duration_s: float, weight_kg: float | None,
                      grade_pct: float = 0.0) -> float:
    """ACSM walking equation (incline-aware).
    VO2 = 0.1*S + 1.8*S*G + 3.5 (ml/kg/min), S = m/min, G = grade fraction;
    kcal/min = VO2 * weight / 200."""
    if not weight_kg or distance_m <= 0 or duration_s <= 0:
        return 0.0
    speed_m_min = (distance_m / duration_s) * 60.0
    g = (grade_pct or 0.0) / 100.0
    vo2 = 0.1 * speed_m_min + 1.8 * speed_m_min * g + 3.5
    kcal_min = vo2 * weight_kg / 200.0
    return kcal_min * (duration_s / 60.0)


def estimate_steps(distance_m: float, stride_cm: float | None) -> int:
    if distance_m <= 0 or not stride_cm or stride_cm <= 0:
        return 0
    return int(distance_m / (stride_cm / 100.0))


def trim_zero_ends(samples: list) -> list:
    """Drop leading/trailing zero-speed samples (index 1 = speed_kmh) and rebase the
    time offset (index 0) so the first kept sample starts at 0. Mid-run zero samples
    (intentional pauses) are kept."""
    lo, hi = 0, len(samples)
    while lo < hi and samples[lo][1] <= 0:
        lo += 1
    while hi > lo and samples[hi - 1][1] <= 0:
        hi -= 1
    kept = samples[lo:hi]
    if not kept:
        return []
    t0 = kept[0][0]
    return [[round(t - t0, 1), sp, jp] for t, sp, jp in kept]


@dataclass
class Context:
    """Who/what is active — captured when a session starts."""
    person: str | None = None
    treadmill: str | None = None
    weight_kg: float | None = None
    grade_pct: float = 0.0
    stride_cm: float | None = None


class SessionTracker:
    def __init__(self, config: AppConfig, on_finalize) -> None:
        self.config = config
        self.on_finalize = on_finalize
        self._s: dict | None = None  # in-progress accumulator

    # ------------------------------------------------------------------ #
    def update(self, now: float, iso_now: str, pulses_per_sec: float, total_distance_m: float,
               speed_kmh: float, joystick_pct: float, profile_name: str | None,
               game_window: str | None, focused_title: str | None, ctx: Context) -> None:
        moving = pulses_per_sec > MOVE_PPS

        if self._s is None:
            if self.config.session_auto and moving:
                self._start(now, iso_now, total_distance_m, profile_name, game_window, ctx, auto=True)
            else:
                return

        s = self._s
        s["prev_dt"] = now - s["prev_t"]
        s["prev_t"] = now
        if moving:
            s["last_move_t"] = now
        if moving and s["first_move_t"] is None:
            s["first_move_t"] = now      # first real movement — start of active time
        s["duration_s"] = now - s["start_t"]
        s["distance_m"] = max(0.0, total_distance_m - s["start_dist_m"])
        s["max_speed"] = max(s["max_speed"], speed_kmh)
        s["grade_pct"] = ctx.grade_pct  # follow the current grade

        gw = (s["game"] or "").strip().lower()
        if gw and gw in (focused_title or "").lower():
            s["game_focused_s"] += s["prev_dt"]

        if now - s["last_sample_t"] >= SAMPLE_INTERVAL_S:
            s["last_sample_t"] = now
            s["samples"].append([round(s["duration_s"], 1), round(speed_kmh, 2), round(joystick_pct, 1)])

        if s["auto"] and (now - s["last_move_t"] > self.config.session_idle_timeout_s):
            self.stop(now)

    # ------------------------------------------------------------------ #
    def _start(self, now, iso_now, dist, profile_name, game_window, ctx: Context, auto) -> None:
        self._s = {
            "start_t": now, "prev_t": now, "prev_dt": 0.0, "last_move_t": now,
            "first_move_t": None,  # set on first movement; anchors active (trimmed) duration
            "last_sample_t": now, "started_at": iso_now,
            "start_dist_m": dist, "duration_s": 0.0, "distance_m": 0.0, "max_speed": 0.0,
            "game_focused_s": 0.0, "samples": [], "profile": profile_name,
            "game": game_window, "auto": auto,
            "person": ctx.person, "treadmill": ctx.treadmill, "weight_kg": ctx.weight_kg,
            "grade_pct": ctx.grade_pct, "stride_cm": ctx.stride_cm,
        }

    def start_manual(self, now, iso_now, dist, profile_name, game_window, ctx: Context) -> None:
        if self._s is not None:
            return
        self._start(now, iso_now, dist, profile_name, game_window, ctx, auto=False)

    def _active_duration(self, s) -> float:
        """Elapsed minus the idle head/tail: the span from first to last movement.
        Mid-run pauses stay counted. Falls back to full duration if never moved."""
        fm, lm = s.get("first_move_t"), s.get("last_move_t")
        if fm is not None and lm is not None and lm > fm:
            return lm - fm
        return max(0.0, s["duration_s"])

    def _calc(self, s):
        dur = self._active_duration(s)
        dist = s["distance_m"]
        avg = ((dist / 1000.0) / (dur / 3600.0)) if dur > 0 else 0.0
        cal = estimate_calories(dist, dur, s["weight_kg"], s["grade_pct"])
        steps = estimate_steps(dist, s["stride_cm"])
        return dur, dist, avg, cal, steps

    def stop(self, now: float, force_save: bool | None = None):
        """Returns (session, persisted). force_save None = auto rule (min distance),
        True = always keep (manual Save & Stop), False = discard (manual Discard)."""
        if self._s is None:
            return None, False
        s = self._s
        self._s = None
        dur, dist, avg, cal, steps = self._calc(s)
        session = Session(
            started_at=s["started_at"],
            ended_at=datetime.now(timezone.utc).isoformat(),
            duration_s=round(dur, 1),
            distance_m=round(dist, 1),
            calories=round(cal, 1),
            steps=steps,
            avg_speed=round(avg, 2),
            max_speed=round(s["max_speed"], 2),
            grade_pct=round(s["grade_pct"], 2),
            person=s["person"],
            treadmill=s["treadmill"],
            profile=s["profile"],
            game=s["game"],
            game_focused_s=round(s["game_focused_s"], 1),
            auto=s["auto"],
            samples=trim_zero_ends(s["samples"]),
        )
        # Auto rule: keep only if above the minimum distance. Manual stop overrides it.
        keep = (dist >= getattr(self.config, "session_min_distance_m", 50.0)) \
            if force_save is None else bool(force_save)
        if keep:
            try:
                self.on_finalize(session)
            except Exception:  # noqa: BLE001
                pass
        return session, keep

    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        if self._s is None:
            return {"session_active": False, "session_duration_s": 0.0,
                    "session_distance_m": 0.0, "session_avg_speed": 0.0,
                    "session_calories": 0.0, "session_steps": 0, "session_auto": False}
        s = self._s
        dur, dist, avg, cal, steps = self._calc(s)
        return {
            "session_active": True,
            "session_auto": s["auto"],
            "session_duration_s": round(dur, 1),
            "session_distance_m": round(dist, 1),
            "session_avg_speed": round(avg, 2),
            "session_calories": round(cal, 1),
            "session_steps": steps,
        }
