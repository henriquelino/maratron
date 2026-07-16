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

# python/src/maratron/app.py -> up 3 -> python/ ; keeps data at python/data (gitignored)
DEFAULT_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"
)


def build(args) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    store = ConfigStore(args.data_dir)
    config = store.load_config()
    profiles = store.load_profiles()

    # CLI overrides. The --mock flag is authoritative for mock intent so a persisted
    # mock=true (e.g. from a prior runtime serial fallback) never forces mock on a
    # real launch — without --mock we always *attempt* real and fall back at runtime.
    if args.serial_port:
        config.serial_port = args.serial_port
    config.mock = bool(args.mock)
    if args.auto_switch:
        config.auto_switch = True
    if config.active_profile not in profiles:
        config.active_profile = next(iter(profiles), None)
    store.save_config(config)

    def flush(total_pulses: int, profile_name: str | None) -> None:
        store.append_distance(total_pulses, config.distance_per_pulse_cm, profile_name)

    engine = TreadmillEngine(
        config, profiles, distance_flush=flush, session_finalize=store.append_session
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
    server.app.state.watcher = watcher

    url = f"http://127.0.0.1:{args.port}"
    mode = "MOCK" if config.mock else "REAL"
    print(f"\n  Maratron dashboard [{mode}] -> {url}\n")

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(server.app, host="127.0.0.1", port=args.port, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser(description="Maratron treadmill -> gamepad web dashboard")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--serial-port", default=None, help="override COM port (e.g. COM7)")
    parser.add_argument("--mock", action="store_true", help="run without hardware")
    parser.add_argument("--auto-switch", action="store_true", help="auto-switch profile by window")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
