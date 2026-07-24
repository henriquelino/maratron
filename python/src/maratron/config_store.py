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
from .models import (
    AppConfig,
    InclinePreset,
    Person,
    Profile,
    Session,
    SprintMethod,
    Treadmill,
    grade_pct,
)

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
        self._migrate_and_seed()

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
            c.execute("CREATE TABLE IF NOT EXISTS persons (name TEXT PRIMARY KEY, data TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS treadmills (name TEXT PRIMARY KEY, data TEXT)")
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

    def _migrate_and_seed(self) -> None:
        """Move legacy single-config hardware/body fields into a default Treadmill + Person,
        seed defaults on a fresh install, and repoint existing profiles/sessions. Reads the
        *raw* old app_config JSON (which still has the pre-refactor fields) before anything
        rewrites it. Idempotent via the 'schema_v2' meta flag."""
        old = {}
        raw = self._meta_get("app_config")
        if raw:
            try:
                old = json.loads(raw)
            except Exception:  # noqa: BLE001
                old = {}

        # Default treadmill from old hardware fields (or model defaults on fresh install).
        if self._count("treadmills") == 0:
            front = float(old.get("incline_front_cm", 19.0))
            back = float(old.get("incline_back_cm", 9.0))
            span = float(old.get("incline_span_cm", 86.6))
            grade = grade_pct(front, back, span)
            t = Treadmill(
                name="My Treadmill",
                amount_of_magnets=int(old.get("amount_of_magnets", 11)),
                one_revolution_cm=float(old.get("one_revolution_cm", 12.9)),
                serial_port=str(old.get("serial_port", "COM7")),
                front_height_cm=front,
                span_cm=span,
                presets=[InclinePreset(label=f"~{round(grade)}%", back_height_cm=back)],
            )
            self._upsert_treadmill(t)
            log.info("seeded treadmill 'My Treadmill'")

        # Default person from old weight (or empty on fresh install).
        if self._count("persons") == 0:
            self._upsert_person(Person(name="Me", weight_kg=old.get("weight_kg")))
            log.info("seeded person 'Me'")

        # Seed a default game profile on a truly fresh install.
        if self._count("profiles") == 0:
            self._upsert_profile(_default_skyrim())
            log.info("seeded default 'skyrim' profile")

        # Repoint any profiles/sessions that don't yet reference a person/treadmill.
        persons = self.load_persons()
        treadmills = self.load_treadmills()
        default_person = next(iter(persons), None)
        default_tm = next(iter(treadmills), None)
        preset0 = None
        if default_tm and treadmills[default_tm].presets:
            preset0 = treadmills[default_tm].presets[0].label

        profiles = self.load_profiles()
        changed = False
        for p in profiles.values():
            if p.person is None:
                p.person, changed = default_person, True
            if p.treadmill is None:
                p.treadmill, changed = default_tm, True
            if p.incline_preset is None and preset0:
                p.incline_preset, changed = preset0, True
        if changed:
            self.save_profiles(profiles)

        if not self._meta_get("schema_v2"):
            self._tag_sessions(default_person, default_tm)
            self._meta_set("schema_v2", "1")

        # schema_v3: output_mode moved from the single global AppConfig onto each Profile.
        # Backfill every existing profile with the old global value so behavior is preserved
        # (before this, all profiles shared one global output_mode). One-shot, guarded.
        if not self._meta_get("schema_v3"):
            global_mode = old.get("output_mode", "gamepad")
            profs = self.load_profiles()
            if profs:
                for p in profs.values():
                    p.output_mode = global_mode
                self.save_profiles(profs)
            self._meta_set("schema_v3", "1")
            log.info("migrated output_mode onto profiles (schema_v3, mode=%s)", global_mode)

        # Normalise app_config to the new schema and ensure active_person is set.
        cfg = self.load_config()  # drops legacy fields on validate
        if cfg.active_person is None:
            cfg.active_person = default_person
        self.save_config(cfg)

    def _tag_sessions(self, person: str | None, treadmill: str | None) -> None:
        sessions = self.load_sessions()
        if not sessions:
            return
        changed = False
        for s in sessions:
            if s.person is None:
                s.person, changed = person, True
            if s.treadmill is None:
                s.treadmill, changed = treadmill, True
        if changed:
            self.clear_sessions()
            for s in sessions:
                self.append_session(s)

    # --------------------------- persons ---------------------------- #
    def load_persons(self) -> dict[str, Person]:
        with self._lock, self._db() as c:
            rows = c.execute("SELECT data FROM persons").fetchall()
        out: dict[str, Person] = {}
        for r in rows:
            try:
                p = Person.model_validate_json(r["data"])
                out[p.name] = p
            except Exception as e:  # noqa: BLE001
                log.warning("invalid person row: %s", e)
        return out

    def save_persons(self, persons: dict[str, Person]) -> None:
        with self._lock, self._db() as c:
            c.execute("DELETE FROM persons")
            c.executemany(
                "INSERT INTO persons (name, data) VALUES (?, ?)",
                [(p.name, p.model_dump_json()) for p in persons.values()],
            )

    def _upsert_person(self, p: Person) -> None:
        with self._lock, self._db() as c:
            c.execute(
                "INSERT INTO persons (name, data) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET data=excluded.data",
                (p.name, p.model_dump_json()),
            )

    # --------------------------- treadmills ------------------------- #
    def load_treadmills(self) -> dict[str, Treadmill]:
        with self._lock, self._db() as c:
            rows = c.execute("SELECT data FROM treadmills").fetchall()
        out: dict[str, Treadmill] = {}
        for r in rows:
            try:
                t = Treadmill.model_validate_json(r["data"])
                out[t.name] = t
            except Exception as e:  # noqa: BLE001
                log.warning("invalid treadmill row: %s", e)
        return out

    def save_treadmills(self, treadmills: dict[str, Treadmill]) -> None:
        with self._lock, self._db() as c:
            c.execute("DELETE FROM treadmills")
            c.executemany(
                "INSERT INTO treadmills (name, data) VALUES (?, ?)",
                [(t.name, t.model_dump_json()) for t in treadmills.values()],
            )

    def _upsert_treadmill(self, t: Treadmill) -> None:
        with self._lock, self._db() as c:
            c.execute(
                "INSERT INTO treadmills (name, data) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET data=excluded.data",
                (t.name, t.model_dump_json()),
            )

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
