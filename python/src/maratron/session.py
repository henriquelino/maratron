"""Session tracking: auto start/stop on movement, pace time-series, in-game time.

The engine feeds one `update()` per loop iteration with the current metrics and the
focused window title. A session auto-starts when movement begins and auto-stops after
`session_idle_timeout_s` of no movement (unless started manually). On stop it builds a
`Session` and hands it to `on_finalize` for persistence.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .models import AppConfig, Session

MOVE_PPS = 1.0          # pulses/sec above which we consider the belt "moving"
SAMPLE_INTERVAL_S = 3.0  # how often to record a pace sample


def estimate_calories(distance_m: float, duration_s: float, weight_kg: float | None) -> float:
    if not weight_kg or distance_m <= 0 or duration_s <= 0:
        return 0.0
    speed_kmh = (distance_m / 1000.0) / (duration_s / 3600.0)
    met = 3.5 if speed_kmh < 6.4 else 8.3 if speed_kmh < 8.0 else 11.0
    return met * weight_kg * (duration_s / 3600.0)


class SessionTracker:
    def __init__(self, config: AppConfig, on_finalize) -> None:
        self.config = config
        self.on_finalize = on_finalize
        self._s: dict | None = None  # in-progress accumulator

    # ------------------------------------------------------------------ #
    def update(self, now: float, iso_now: str, pulses_per_sec: float, total_distance_m: float,
               speed_kmh: float, joystick_pct: float, profile_name: str | None,
               game_window: str | None, focused_title: str | None) -> None:
        moving = pulses_per_sec > MOVE_PPS

        if self._s is None:
            if self.config.session_auto and moving:
                self._start(now, iso_now, total_distance_m, profile_name, game_window, auto=True)
            else:
                return

        s = self._s
        s["prev_dt"] = now - s["prev_t"]
        s["prev_t"] = now
        if moving:
            s["last_move_t"] = now
        s["duration_s"] = now - s["start_t"]
        s["distance_m"] = max(0.0, total_distance_m - s["start_dist_m"])
        s["max_speed"] = max(s["max_speed"], speed_kmh)

        gw = (s["game"] or "").strip().lower()
        if gw and gw in (focused_title or "").lower():
            s["game_focused_s"] += s["prev_dt"]

        if now - s["last_sample_t"] >= SAMPLE_INTERVAL_S:
            s["last_sample_t"] = now
            s["samples"].append([round(s["duration_s"], 1), round(speed_kmh, 2), round(joystick_pct, 1)])

        if s["auto"] and (now - s["last_move_t"] > self.config.session_idle_timeout_s):
            self.stop(now)

    # ------------------------------------------------------------------ #
    def _start(self, now, iso_now, dist, profile_name, game_window, auto) -> None:
        self._s = {
            "start_t": now, "prev_t": now, "prev_dt": 0.0, "last_move_t": now,
            "last_sample_t": now, "started_at": iso_now,
            "start_dist_m": dist, "duration_s": 0.0, "distance_m": 0.0, "max_speed": 0.0,
            "game_focused_s": 0.0, "samples": [], "profile": profile_name,
            "game": game_window, "auto": auto,
        }

    def start_manual(self, now, iso_now, dist, profile_name, game_window) -> None:
        if self._s is not None:
            return
        self._start(now, iso_now, dist, profile_name, game_window, auto=False)

    def stop(self, now: float) -> Session | None:
        if self._s is None:
            return None
        s = self._s
        self._s = None
        dur = max(0.0, s["duration_s"])
        dist = s["distance_m"]
        avg = ((dist / 1000.0) / (dur / 3600.0)) if dur > 0 else 0.0
        session = Session(
            started_at=s["started_at"],
            ended_at=datetime.now(timezone.utc).isoformat(),
            duration_s=round(dur, 1),
            distance_m=round(dist, 1),
            avg_speed=round(avg, 2),
            max_speed=round(s["max_speed"], 2),
            calories=round(estimate_calories(dist, dur, self.config.weight_kg), 1),
            profile=s["profile"],
            game=s["game"],
            game_focused_s=round(s["game_focused_s"], 1),
            auto=s["auto"],
            samples=s["samples"],
        )
        # Discard trivially short/empty sessions so idle blips don't spam the log.
        if dur >= 3.0 and dist > 0.0:
            try:
                self.on_finalize(session)
            except Exception:  # noqa: BLE001
                pass
        return session

    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        if self._s is None:
            return {"session_active": False, "session_duration_s": 0.0,
                    "session_distance_m": 0.0, "session_avg_speed": 0.0,
                    "session_calories": 0.0, "session_auto": False}
        s = self._s
        dur = s["duration_s"]
        dist = s["distance_m"]
        avg = ((dist / 1000.0) / (dur / 3600.0)) if dur > 0 else 0.0
        return {
            "session_active": True,
            "session_auto": s["auto"],
            "session_duration_s": round(dur, 1),
            "session_distance_m": round(dist, 1),
            "session_avg_speed": round(avg, 2),
            "session_calories": round(estimate_calories(dist, dur, self.config.weight_kg), 1),
        }
