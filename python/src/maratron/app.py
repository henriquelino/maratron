"""Terminal entry point: build the engine, start its thread, serve the dashboard.

    python -m maratron.app --mock
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
import webbrowser

import uvicorn

from . import server
from .config_store import ConfigStore
from .engine import TreadmillEngine
from .window_watcher import WindowWatcher

log = logging.getLogger("maratron.app")

# python/src/maratron/app.py -> up 3 -> python/ ; keeps data at python/data (gitignored)
DEFAULT_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"
)


def build(args) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    store = ConfigStore(args.data_dir)
    config = store.load_config()
    profiles = store.load_profiles()
    treadmills = store.load_treadmills()
    persons = store.load_persons()

    # CLI overrides. The --mock flag is authoritative for mock intent so a persisted
    # mock=true (e.g. from a prior runtime serial fallback) never forces mock on a
    # real launch — without --mock we always *attempt* real and fall back at runtime.
    config.mock = bool(args.mock)
    config.vr_role = args.vr_role  # authoritative each run (debug flag; UI has no role selector)
    if args.auto_switch:
        config.auto_switch = True
    if config.active_profile not in profiles:
        config.active_profile = next(iter(profiles), None)
    if config.active_person not in persons:
        config.active_person = next(iter(persons), None)
    # --serial-port overrides the active profile's treadmill port (or the first treadmill).
    if args.serial_port and treadmills:
        active = profiles.get(config.active_profile)
        tname = (active.treadmill if active and active.treadmill in treadmills
                 else next(iter(treadmills)))
        treadmills[tname].serial_port = args.serial_port
        store.save_treadmills(treadmills)
    store.save_config(config)

    def _dpp_for(profile_name: str | None) -> float:
        prof = profiles.get(profile_name) if profile_name else None
        tm = treadmills.get(prof.treadmill) if prof and prof.treadmill else None
        return tm.distance_per_pulse_cm if tm else 12.9 / 11

    def flush(total_pulses: int, profile_name: str | None) -> None:
        store.append_distance(total_pulses, _dpp_for(profile_name), profile_name)

    engine = TreadmillEngine(
        config, profiles, treadmills, persons,
        distance_flush=flush, session_finalize=store.append_session,
    )

    watcher = WindowWatcher(engine, config, profiles)
    engine.set_focus_getter(lambda: watcher.current_title)
    engine.start()
    watcher.start()

    # Expose to the FastAPI handlers.
    server.app.state.engine = engine
    server.app.state.store = store
    server.app.state.config = config
    server.app.state.profiles = profiles
    server.app.state.treadmills = treadmills
    server.app.state.persons = persons
    server.app.state.watcher = watcher

    url = f"http://127.0.0.1:{args.port}"
    mode = "MOCK" if config.mock else "REAL"
    ui = "none" if args.no_browser else args.ui
    print(f"\n  Maratron dashboard [{mode}] -> {url}   (ui: {ui})\n")

    if ui == "window":
        _serve_windowed(url, args.port, engine)
    else:
        if ui == "browser":
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        uvicorn.run(server.app, host="127.0.0.1", port=args.port, log_level="warning")


def _wait_until_up(url: str, timeout: float = 12.0) -> bool:
    import time
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url + "/api/status", timeout=0.5)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    return False


def _serve_windowed(url: str, port: int, engine: TreadmillEngine) -> None:
    """Run uvicorn in a background thread and show the dashboard in a native window
    (pywebview). Falls back to the browser if pywebview isn't installed."""
    try:
        import webview
    except ImportError:
        log.warning("pywebview not installed (pip install pywebview) — opening in browser instead")
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        uvicorn.run(server.app, host="127.0.0.1", port=port, log_level="warning")
        return

    uconfig = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    userver = uvicorn.Server(uconfig)
    threading.Thread(target=userver.run, name="maratron-uvicorn", daemon=True).start()
    _wait_until_up(url)

    webview.create_window("Maratron", url, width=1360, height=900, min_size=(900, 600))
    try:
        webview.start()  # blocks on the main thread until the window is closed
    finally:
        userver.should_exit = True  # triggers FastAPI shutdown -> engine.stop()
        engine.stop()               # idempotent safety net


def main() -> None:
    parser = argparse.ArgumentParser(description="Maratron treadmill -> gamepad web dashboard")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--serial-port", default=None, help="override COM port (e.g. COM7)")
    parser.add_argument("--mock", action="store_true", help="run without hardware")
    parser.add_argument("--auto-switch", action="store_true", help="auto-switch profile by window")
    parser.add_argument("--vr-role", choices=["optout", "left", "right", "treadmill"],
                        default="treadmill",
                        help="SteamVR device role (debug). Default 'treadmill' = keep both "
                             "controllers; left/right sacrifice that hand (fallback).")
    parser.add_argument("--ui", choices=["window", "browser", "none"], default="window",
                        help="window = native app window (pywebview); browser = open a tab; none = headless")
    parser.add_argument("--no-browser", action="store_true", help="alias for --ui none")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
