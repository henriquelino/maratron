"""Auto profile switching by foreground window title (Windows).

Polls the active window title ~1x/sec; when AppConfig.auto_switch is on, matches
the title against each profile's game_window substring and switches the engine's
active profile on change. No-ops cleanly on non-Windows or if ctypes is unavailable.
"""

from __future__ import annotations

import logging
import sys
import threading
import time

log = logging.getLogger("maratron.window")


def _get_foreground_title() -> str:
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def list_open_windows() -> list[str]:
    """Titles of visible top-level windows (Windows only; [] elsewhere)."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        titles: list[str] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                t = buf.value.strip()
                if t:
                    titles.append(t)
            return True

        user32.EnumWindows(_cb, 0)
        return sorted(set(titles), key=str.lower)
    except Exception as e:  # noqa: BLE001
        log.warning("window enumeration failed: %s", e)
        return []


class WindowWatcher:
    def __init__(self, engine, config, profiles, interval: float = 1.0) -> None:
        self._engine = engine
        self._config = config
        self._profiles = profiles
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_matched: str | None = None
        self._supported = sys.platform == "win32"
        self.current_title = ""

    def start(self) -> None:
        if not self._supported:
            log.info("window auto-switch unsupported on %s; disabled", sys.platform)
            return
        self._thread = threading.Thread(target=self._run, name="maratron-window", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            time.sleep(self._interval)
            try:
                raw = _get_foreground_title()
            except Exception as e:  # noqa: BLE001
                log.warning("window title read failed: %s", e)
                continue
            self.current_title = raw  # always cached (used for session game-focus)
            if not self._config.auto_switch or not raw:
                continue
            title = raw.lower()
            for name, prof in list(self._profiles.items()):
                gw = (prof.game_window or "").strip().lower()
                if gw and gw in title and name != self._last_matched:
                    self._last_matched = name
                    self._engine.set_active_profile(name)
                    log.info("auto-switched to profile '%s' (window: %s)", name, title)
                    break
