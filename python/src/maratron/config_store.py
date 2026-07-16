"""SQLite-backed persistence for profiles, app config, sessions, and distance log.

Everything lives in a single `maratron.db` (stdlib sqlite3 — no extra dependency).
Model bodies are stored as JSON in a `data` column, with the query keys (profile
name, session started_at) as real indexed columns. On first run this imports any
pre-existing JSON files (profiles.json / app_config.json / sessions.json) so nothing
is lost, then renames them to *.bak.

The public API matches the previous JSON store, so engine/server/app are unchanged.
A connection is opened per call (sqlite3 handles file locking; WAL mode is enabled)
which is simplest and safe across the uvicorn and engine threads.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from .control import sample_two_segment_points
from .models import AppConfig, Profile, Session, SprintMethod

log = logging.getLogger("maratron.store")


def _default_skyrim() -> Profile:
    """The migrated hardcoded profile from the old treadmill.py."""
    return Profile(
        name="skyrim",
        game_window="Skyrim",
        max_pulses_per_second=110,
        gain=1.2,
        deadzone=0.01,
        smoothing=0.6,
        run_threshold=0.80,
        sprint_method=SprintMethod.NONE,
        run_button="XUSB_GAMEPAD_LEFT_SHOULDER",
        curve_points=sample_two_segment_points(0.30, n=9),
    )


class ConfigStore:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.db_path = os.path.join(data_dir, "maratron.db")
        self._lock = threading.Lock()
        self._init_db()
        self._migrate_json()
        self._seed_if_empty()

    # --------------------------- schema ----------------------------- #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _db(self):
        """Per-call connection that commits on success and always closes."""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._db() as c:
            c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS profiles (name TEXT PRIMARY KEY, data TEXT)")
            c.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, data TEXT)"
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at)")
            c.execute(
                "CREATE TABLE IF NOT EXISTS distance_log ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT)"
            )

    # --------------------------- migration -------------------------- #
    def _migrate_json(self) -> None:
        # profiles.json
        pj = os.path.join(self.data_dir, "profiles.json")
        if os.path.exists(pj) and self._count("profiles") == 0:
            raw = self._read_json(pj, {})
            imported = 0
            for item in raw.get("profiles", []):
                try:
                    p = Profile.model_validate(item)
                    self._upsert_profile(p)
                    imported += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("skip profile during import: %s", e)
            log.info("imported %d profiles from profiles.json", imported)
            self._backup(pj)

        # app_config.json
        cj = os.path.join(self.data_dir, "app_config.json")
        if os.path.exists(cj) and not self._meta_get("app_config"):
            raw = self._read_json(cj, None)
            if raw:
                try:
                    self.save_config(AppConfig.model_validate(raw))
                    log.info("imported app_config.json")
                except Exception as e:  # noqa: BLE001
                    log.warning("app_config import failed: %s", e)
            self._backup(cj)

        # sessions.json
        sj = os.path.join(self.data_dir, "sessions.json")
        if os.path.exists(sj) and self._count("sessions") == 0:
            raw = self._read_json(sj, [])
            imported = 0
            for item in raw:
                try:
                    self.append_session(Session.model_validate(item))
                    imported += 1
                except Exception:  # noqa: BLE001
                    pass
            log.info("imported %d sessions from sessions.json", imported)
            self._backup(sj)

    def _seed_if_empty(self) -> None:
        if self._count("profiles") == 0:
            self._upsert_profile(_default_skyrim())
            log.info("seeded default 'skyrim' profile")
        if not self._meta_get("app_config"):
            self.save_config(AppConfig())

    # --------------------------- profiles --------------------------- #
    def load_profiles(self) -> dict[str, Profile]:
        with self._lock, self._db() as c:
            rows = c.execute("SELECT data FROM profiles").fetchall()
        out: dict[str, Profile] = {}
        for r in rows:
            try:
                p = Profile.model_validate_json(r["data"])
                out[p.name] = p
            except Exception as e:  # noqa: BLE001
                log.warning("invalid profile row: %s", e)
        return out

    def save_profiles(self, profiles: dict[str, Profile]) -> None:
        """Replace the full profile set (matches the previous JSON store semantics)."""
        with self._lock, self._db() as c:
            c.execute("DELETE FROM profiles")
            c.executemany(
                "INSERT INTO profiles (name, data) VALUES (?, ?)",
                [(p.name, p.model_dump_json()) for p in profiles.values()],
            )

    def _upsert_profile(self, p: Profile) -> None:
        with self._lock, self._db() as c:
            c.execute(
                "INSERT INTO profiles (name, data) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET data=excluded.data",
                (p.name, p.model_dump_json()),
            )

    # --------------------------- app config ------------------------- #
    def load_config(self) -> AppConfig:
        raw = self._meta_get("app_config")
        if not raw:
            cfg = AppConfig()
            self.save_config(cfg)
            return cfg
        try:
            return AppConfig.model_validate_json(raw)
        except Exception as e:  # noqa: BLE001
            log.warning("invalid app_config, using defaults: %s", e)
            return AppConfig()

    def save_config(self, config: AppConfig) -> None:
        self._meta_set("app_config", config.model_dump_json())

    # --------------------------- sessions --------------------------- #
    def load_sessions(self) -> list[Session]:
        with self._lock, self._db() as c:
            rows = c.execute("SELECT data FROM sessions ORDER BY started_at").fetchall()
        out: list[Session] = []
        for r in rows:
            try:
                out.append(Session.model_validate_json(r["data"]))
            except Exception:  # noqa: BLE001
                pass
        return out

    def append_session(self, session: Session) -> None:
        with self._lock, self._db() as c:
            c.execute(
                "INSERT INTO sessions (started_at, data) VALUES (?, ?)",
                (session.started_at, session.model_dump_json()),
            )

    def clear_sessions(self) -> None:
        with self._lock, self._db() as c:
            c.execute("DELETE FROM sessions")

    def delete_session(self, started_at: str) -> bool:
        with self._lock, self._db() as c:
            cur = c.execute("DELETE FROM sessions WHERE started_at = ?", (started_at,))
            return cur.rowcount > 0

    # --------------------------- distance log ----------------------- #
    def append_distance(self, total_pulses: int, distance_per_pulse_cm: float,
                        profile: str | None = None) -> None:
        distance_cm = total_pulses * distance_per_pulse_cm
        entry = {
            "timestamp": datetime.now().isoformat(),
            "profile": profile,
            "total_pulses": total_pulses,
            "cm_per_pulse": round(distance_per_pulse_cm, 4),
            "distance_cm": round(distance_cm, 4),
            "distance_m": round(distance_cm / 100.0, 4),
            "distance_km": round(distance_cm / 100000.0, 4),
        }
        with self._lock, self._db() as c:
            c.execute("INSERT INTO distance_log (data) VALUES (?)", (json.dumps(entry),))

    # --------------------------- helpers ---------------------------- #
    def _count(self, table: str) -> int:
        with self._lock, self._db() as c:
            return c.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]

    def _meta_get(self, key: str):
        with self._lock, self._db() as c:
            row = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def _meta_set(self, key: str, value: str) -> None:
        with self._lock, self._db() as c:
            c.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    @staticmethod
    def _read_json(path: str, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            log.warning("failed reading %s: %s", path, e)
            return default

    @staticmethod
    def _backup(path: str) -> None:
        try:
            os.replace(path, path + ".bak")
        except Exception:  # noqa: BLE001
            pass
