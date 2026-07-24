"""TreadmillEngine — runs the control loop in a daemon thread and exposes a
thread-safe live-status snapshot for the web layer.

The uvicorn asyncio loop runs on the main thread; the blocking serial/sleep loop
runs here. A single writer (this loop) + a Lock-guarded snapshot keeps sharing
simple: the WebSocket coroutine just polls get_status() on its own cadence.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from . import hardware
from .control import ControlState, JOY_MAX, step
from .models import AppConfig, EngineStatus, Person, Profile, Treadmill
from .session import Context, SessionTracker

log = logging.getLogger("maratron.engine")


def _pps_to_kmh(pulses_per_sec: float, distance_per_pulse_cm: float) -> float:
    return pulses_per_sec * distance_per_pulse_cm * 3600.0 / 100000.0


class TreadmillEngine:
    def __init__(
        self,
        config: AppConfig,
        profiles: dict[str, Profile],
        treadmills: dict[str, Treadmill] | None = None,
        persons: dict[str, Person] | None = None,
        distance_flush: Callable[[int, str | None], None] | None = None,
        session_finalize: Callable | None = None,
    ) -> None:
        self._config = config
        self._profiles = dict(profiles)
        self._treadmills = dict(treadmills or {})
        self._persons = dict(persons or {})
        self._distance_flush = distance_flush
        self._focus_getter: Callable[[], str] = lambda: ""
        self._tracker = SessionTracker(config, session_finalize or (lambda s: None))

        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

        self._mock_speed = 0.0
        self._active_name = config.active_profile or (next(iter(profiles), None))
        self._status = EngineStatus(mock=config.mock, active_profile=self._active_name)

        self._state = ControlState()
        self._open_port: str | None = None
        self._gamepad = hardware.make_output(config, self._effective_output_mode())
        self._using_null = isinstance(self._gamepad, hardware.NullGamepadOutput)
        self._source = self._make_source()

    # ------------------------------------------------------------------ #
    def _serial_port(self) -> str:
        return self._active_treadmill().serial_port

    def _make_source(self) -> hardware.PulseSource:
        if self._config.mock:
            return self._make_mock_source()
        port = self._serial_port()
        try:
            src = hardware.SerialPulseSource(port, self._config.baudrate)
            self._open_port = port
            log.info("serial connected on %s", port)
            return src
        except Exception as e:  # noqa: BLE001
            log.warning("serial port %s unavailable (%s); falling back to MOCK input", port, e)
            self._config.mock = True
            with self._lock:
                self._status.mock = True
            return self._make_mock_source()

    def _make_mock_source(self) -> hardware.MockPulseSource:
        return hardware.MockPulseSource(
            target_speed_getter=lambda: self._mock_speed,
            max_pps_getter=lambda: self._active_profile().max_pulses_per_second,
            poll_interval=self._config.poll_interval_s,
        )

    def _active_profile(self) -> Profile:
        with self._lock:
            name = self._active_name
            prof = self._profiles.get(name) if name else None
        if prof is None:
            # No profiles yet: use a neutral default so the loop still runs.
            prof = Profile(name="default")
        return prof

    def _active_treadmill(self) -> Treadmill:
        prof = self._active_profile()
        with self._lock:
            t = self._treadmills.get(prof.treadmill) if prof.treadmill else None
            if t is None and self._treadmills:
                t = next(iter(self._treadmills.values()))
        return t or Treadmill(name="default")

    def _active_person(self) -> Person:
        prof = self._active_profile()
        with self._lock:
            p = self._persons.get(prof.person) if prof.person else None
            if p is None and self._config.active_person:
                p = self._persons.get(self._config.active_person)
        return p or Person(name="default")

    def _active_context(self) -> Context:
        prof = self._active_profile()
        t = self._active_treadmill()
        person = self._active_person()
        return Context(
            person=person.name if person.name != "default" else None,
            treadmill=t.name if t.name != "default" else None,
            weight_kg=person.weight_kg,
            grade_pct=t.grade_for(prof.incline_preset),
            stride_cm=person.effective_stride_cm(),
        )

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run_loop, name="maratron-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if getattr(self, "_stopped", False):
            return  # idempotent: window-close and FastAPI shutdown may both call this
        self._stopped = True
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        try:
            self._tracker.stop(time.monotonic())  # persist any in-progress session
        except Exception:  # noqa: BLE001
            pass
        if hasattr(self._gamepad, "close"):
            try:
                self._gamepad.close()
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            total = self._status.total_pulses
            name = self._active_name
        if self._distance_flush and total > 0:
            try:
                self._distance_flush(total, name)
            except Exception as e:  # noqa: BLE001
                log.warning("distance flush failed: %s", e)
        self._source.close()

    def get_status(self) -> EngineStatus:
        with self._lock:
            return self._status.model_copy()

    def set_active_profile(self, name: str) -> None:
        with self._lock:
            if name not in self._profiles:
                return
            self._active_name = name
            self._config.active_profile = name
            self._status.active_profile = name
            person = self._profiles[name].person
            if person:
                self._config.active_person = person
        # Switch the output device to the new profile's output_mode (live; gamepad<->VR
        # needs no SteamVR restart — the driver stays loaded, we just (re)open the writer).
        self.rebuild_output()
        # If the new profile's treadmill uses a different serial port, reconnect.
        if not self._config.mock:
            new_port = self._serial_port()
            if self._open_port is not None and new_port != self._open_port:
                log.info("active profile treadmill port changed %s -> %s; reconnecting",
                         self._open_port, new_port)
                self.reconnect(new_port)

    def set_active_person(self, name: str) -> None:
        with self._lock:
            self._config.active_person = name

    def update_treadmills(self, treadmills: dict[str, Treadmill]) -> None:
        with self._lock:
            self._treadmills = dict(treadmills)

    def update_persons(self, persons: dict[str, Person]) -> None:
        with self._lock:
            self._persons = dict(persons)

    def set_mock_speed(self, value: float) -> None:
        self._mock_speed = float(value)

    def set_focus_getter(self, getter: Callable[[], str]) -> None:
        self._focus_getter = getter

    def _effective_output_mode(self) -> str:
        """The active profile's output_mode, falling back to the global config default.
        Caller must hold the lock if concurrent with the control loop (rebuild_output does)."""
        prof = self._profiles.get(self._active_name)
        return getattr(prof, "output_mode", None) or getattr(self._config, "output_mode", "gamepad")

    def rebuild_output(self) -> None:
        """Swap the control output live (after a profile switch or vr_invert_y change)."""
        with self._lock:
            old = self._gamepad
            self._gamepad = hardware.make_output(self._config, self._effective_output_mode())
        if old is not None and hasattr(old, "close"):
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass

    def start_session(self) -> None:
        prof = self._active_profile()
        self._tracker.start_manual(
            time.monotonic(), datetime.now(timezone.utc).isoformat(),
            self._current_distance_m(), prof.name, prof.game_window, self._active_context(),
        )

    def stop_session(self, save: bool = True) -> bool:
        _, persisted = self._tracker.stop(time.monotonic(), force_save=save)
        return persisted

    def _current_distance_m(self) -> float:
        return self._state.total_pulses * self._active_treadmill().distance_per_pulse_cm / 100.0

    def reset_distance(self) -> None:
        self._source.reset()
        with self._lock:
            self._status.total_pulses = 0
            self._status.distance_m = 0.0
            self._status.distance_km = 0.0
        self._state.total_pulses = 0

    def reconnect(self, serial_port: str | None = None) -> dict:
        """(Re)open the serial port live and leave mock mode. Falls back to mock on failure.

        Swaps the pulse source (and, if currently a null gamepad, tries a real one)
        without restarting. Safe to call while the loop is running — the loop picks up
        the new source/gamepad on its next iteration.
        """
        port = serial_port or self._serial_port()
        try:
            new_source = hardware.SerialPulseSource(port, self._config.baudrate)
        except Exception as e:  # noqa: BLE001
            self._config.mock = True
            if not isinstance(self._source, hardware.MockPulseSource):
                old = self._source
                self._source = self._make_mock_source()
                try:
                    old.close()
                except Exception:  # noqa: BLE001
                    pass
            with self._lock:
                self._status.mock = True
                self._status.connected = False
                self._status.error = f"could not open {port}: {e}"
            log.warning("reconnect failed: %s", e)
            return {"ok": False, "mock": True, "error": str(e), "port": port}

        # success — switch to real input (and real output if we were on a null pad)
        self._config.mock = False
        if isinstance(self._gamepad, hardware.NullGamepadOutput):
            self._gamepad = hardware.make_gamepad(False)
        old = self._source
        self._state = ControlState()  # fresh baseline for the new source
        self._source = new_source
        self._open_port = port
        try:
            old.close()
        except Exception:  # noqa: BLE001
            pass
        gamepad_null = isinstance(self._gamepad, hardware.NullGamepadOutput)
        with self._lock:
            self._status.mock = False
            self._status.error = None
        log.info("reconnected on %s (gamepad=%s)", port, "null" if gamepad_null else "vgamepad")
        return {"ok": True, "mock": False, "gamepad_null": gamepad_null, "port": port}

    def update_profiles(self, profiles: dict[str, Profile]) -> None:
        with self._lock:
            self._profiles = dict(profiles)
            if self._active_name not in self._profiles:
                self._active_name = next(iter(self._profiles), None)
                self._status.active_profile = self._active_name

    # ------------------------------------------------------------------ #
    def _run_loop(self) -> None:
        self._state = ControlState()  # fresh baseline each run
        poll = self._config.poll_interval_s
        error_streak = 0

        with self._lock:
            self._status.running = True

        while not self._stop_evt.is_set():
            time.sleep(poll)
            try:
                data = self._source.read()
                if not data:
                    continue
                current_pulses, arduino_ms = data
                profile = self._active_profile()

                result = step(self._state, current_pulses, arduino_ms, profile, poll, time.time())

                self._gamepad.set_left_stick_y(result.joy_y)
                if result.sprint_action == "press":
                    self._gamepad.press(profile.run_button)
                elif result.sprint_action == "release":
                    self._gamepad.release(profile.run_button)
                self._gamepad.update()

                dpp = self._active_treadmill().distance_per_pulse_cm
                ctx = self._active_context()
                total = self._state.total_pulses
                distance_cm = total * dpp
                distance_m = distance_cm / 100.0
                speed_kmh = _pps_to_kmh(result.pulses_per_sec, dpp)
                joystick_pct = round(result.joy_y / JOY_MAX * 100, 1)

                # session tracking
                focused = ""
                try:
                    focused = self._focus_getter() or ""
                except Exception:  # noqa: BLE001
                    focused = ""
                gw = (profile.game_window or "").strip().lower()
                game_focused = bool(gw and gw in focused.lower())
                now_mono = time.monotonic()
                self._tracker.update(
                    now_mono, datetime.now(timezone.utc).isoformat(),
                    result.pulses_per_sec, distance_m, speed_kmh, joystick_pct,
                    profile.name, profile.game_window, focused, ctx,
                )
                snap = self._tracker.snapshot()

                with self._lock:
                    self._status.connected = True
                    self._status.pulses_per_sec = round(result.pulses_per_sec, 2)
                    self._status.filtered_speed = round(result.filtered_speed, 4)
                    self._status.curved = round(result.curved, 4)
                    self._status.joystick_pct = joystick_pct
                    self._status.sprinting = self._state.is_running
                    self._status.total_pulses = total
                    self._status.distance_m = round(distance_m, 3)
                    self._status.distance_km = round(distance_cm / 100000.0, 5)
                    self._status.speed_kmh = round(speed_kmh, 2)
                    self._status.grade_pct = round(ctx.grade_pct, 2)
                    self._status.active_person = ctx.person
                    self._status.active_treadmill = ctx.treadmill
                    self._status.game_focused = game_focused
                    self._status.error = None
                    for k, v in snap.items():
                        setattr(self._status, k, v)
                error_streak = 0

            except Exception as e:  # noqa: BLE001
                error_streak += 1
                log.warning("engine loop error (%d): %s", error_streak, e)
                with self._lock:
                    self._status.connected = False
                    self._status.error = str(e)
                # bounded backoff so a disconnected port doesn't spin hot
                time.sleep(min(2.0, 0.1 * error_streak))

        with self._lock:
            self._status.running = False
