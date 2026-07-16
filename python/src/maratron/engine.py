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
from .models import AppConfig, EngineStatus, Profile
from .session import SessionTracker

log = logging.getLogger("maratron.engine")


def _pps_to_kmh(pulses_per_sec: float, distance_per_pulse_cm: float) -> float:
    return pulses_per_sec * distance_per_pulse_cm * 3600.0 / 100000.0


class TreadmillEngine:
    def __init__(
        self,
        config: AppConfig,
        profiles: dict[str, Profile],
        distance_flush: Callable[[int, str | None], None] | None = None,
        session_finalize: Callable | None = None,
    ) -> None:
        self._config = config
        self._profiles = dict(profiles)
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
        self._gamepad = hardware.make_gamepad(config.mock)
        self._using_null = isinstance(self._gamepad, hardware.NullGamepadOutput)
        self._source = self._make_source()

    # ------------------------------------------------------------------ #
    def _make_source(self) -> hardware.PulseSource:
        if self._config.mock:
            return self._make_mock_source()
        try:
            src = hardware.SerialPulseSource(self._config.serial_port, self._config.baudrate)
            log.info("serial connected on %s", self._config.serial_port)
            return src
        except Exception as e:  # noqa: BLE001
            log.warning(
                "serial port %s unavailable (%s); falling back to MOCK input",
                self._config.serial_port,
                e,
            )
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

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run_loop, name="maratron-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        try:
            self._tracker.stop(time.monotonic())  # persist any in-progress session
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
            if name in self._profiles:
                self._active_name = name
                self._config.active_profile = name
                self._status.active_profile = name

    def set_mock_speed(self, value: float) -> None:
        self._mock_speed = float(value)

    def set_focus_getter(self, getter: Callable[[], str]) -> None:
        self._focus_getter = getter

    def start_session(self) -> None:
        prof = self._active_profile()
        self._tracker.start_manual(
            time.monotonic(), datetime.now(timezone.utc).isoformat(),
            self._current_distance_m(), prof.name, prof.game_window,
        )

    def stop_session(self):
        return self._tracker.stop(time.monotonic())

    def _current_distance_m(self) -> float:
        return self._state.total_pulses * self._config.distance_per_pulse_cm / 100.0

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
        if serial_port:
            self._config.serial_port = serial_port
        try:
            new_source = hardware.SerialPulseSource(self._config.serial_port, self._config.baudrate)
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
                self._status.error = f"could not open {self._config.serial_port}: {e}"
            log.warning("reconnect failed: %s", e)
            return {"ok": False, "mock": True, "error": str(e), "port": self._config.serial_port}

        # success — switch to real input (and real output if we were on a null pad)
        self._config.mock = False
        if isinstance(self._gamepad, hardware.NullGamepadOutput):
            self._gamepad = hardware.make_gamepad(False)
        old = self._source
        self._state = ControlState()  # fresh baseline for the new source
        self._source = new_source
        try:
            old.close()
        except Exception:  # noqa: BLE001
            pass
        gamepad_null = isinstance(self._gamepad, hardware.NullGamepadOutput)
        with self._lock:
            self._status.mock = False
            self._status.error = None
        log.info("reconnected on %s (gamepad=%s)", self._config.serial_port,
                 "null" if gamepad_null else "vgamepad")
        return {"ok": True, "mock": False, "gamepad_null": gamepad_null,
                "port": self._config.serial_port}

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

                total = self._state.total_pulses
                distance_cm = total * self._config.distance_per_pulse_cm
                distance_m = distance_cm / 100.0
                speed_kmh = _pps_to_kmh(result.pulses_per_sec, self._config.distance_per_pulse_cm)
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
                    profile.name, profile.game_window, focused,
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
