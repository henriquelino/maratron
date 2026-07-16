"""FastAPI app: REST for config/profiles + a WebSocket that streams live metrics.

The engine and store are attached to app.state by app.py before uvicorn starts.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config_store import ConfigStore
from .engine import TreadmillEngine
from .models import RUN_BUTTONS, AppConfig, Profile

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")

app = FastAPI(title="Maratron")


def _engine() -> TreadmillEngine:
    return app.state.engine


def _store() -> ConfigStore:
    return app.state.store


# --------------------------------------------------------------------------- #
# Static
# --------------------------------------------------------------------------- #
@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


# --------------------------------------------------------------------------- #
# Status / config
# --------------------------------------------------------------------------- #
@app.get("/api/status")
def get_status():
    return _engine().get_status()


@app.get("/api/config")
def get_config() -> AppConfig:
    return app.state.config


@app.patch("/api/config")
def patch_config(patch: dict):
    # Mutate the shared AppConfig in place (the engine holds the same object) so
    # hardware fields like amount_of_magnets / one_revolution_cm take effect live.
    cfg = app.state.config
    data = cfg.model_dump()
    data.update({k: v for k, v in patch.items() if k in cfg.model_fields})
    validated = AppConfig.model_validate(data)  # reject bad values
    for field in cfg.model_fields:
        setattr(cfg, field, getattr(validated, field))
    _store().save_config(cfg)
    return cfg


@app.get("/api/buttons")
def get_buttons() -> list[str]:
    return RUN_BUTTONS


@app.get("/api/windows")
def get_windows() -> list[str]:
    """Titles of currently-open windows, for the game-window picker."""
    from .window_watcher import list_open_windows

    return list_open_windows()


@app.get("/api/serial-ports")
def get_serial_ports() -> list[dict]:
    """List available serial ports (device + human description)."""
    try:
        from serial.tools import list_ports

        return [
            {"device": p.device, "description": p.description or p.device}
            for p in list_ports.comports()
        ]
    except Exception:  # noqa: BLE001 — pyserial missing or enumeration failed
        return []


# --------------------------------------------------------------------------- #
# Profiles CRUD
# --------------------------------------------------------------------------- #
@app.get("/api/profiles")
def list_profiles() -> list[Profile]:
    return list(app.state.profiles.values())


@app.get("/api/profiles/{name}")
def get_profile(name: str) -> Profile:
    p = app.state.profiles.get(name)
    if not p:
        raise HTTPException(404, "profile not found")
    return p


@app.post("/api/profiles", status_code=201)
def create_profile(profile: Profile) -> Profile:
    if profile.name in app.state.profiles:
        raise HTTPException(409, "profile name already exists")
    app.state.profiles[profile.name] = profile
    _persist_profiles()
    return profile


@app.put("/api/profiles/{name}")
def update_profile(name: str, profile: Profile) -> Profile:
    if name not in app.state.profiles:
        raise HTTPException(404, "profile not found")
    # allow rename: drop old key if the name changed
    if profile.name != name:
        app.state.profiles.pop(name, None)
    app.state.profiles[profile.name] = profile
    _persist_profiles()
    return profile


@app.delete("/api/profiles/{name}")
def delete_profile(name: str):
    if name not in app.state.profiles:
        raise HTTPException(404, "profile not found")
    del app.state.profiles[name]
    _persist_profiles()
    return {"ok": True}


class ActiveProfileBody(BaseModel):
    name: str


@app.post("/api/active-profile")
def set_active_profile(body: ActiveProfileBody):
    if body.name not in app.state.profiles:
        raise HTTPException(404, "profile not found")
    _engine().set_active_profile(body.name)
    app.state.config.active_profile = body.name
    _store().save_config(app.state.config)
    return {"active": body.name}


# --------------------------------------------------------------------------- #
# Controls
# --------------------------------------------------------------------------- #
class MockSpeedBody(BaseModel):
    value: float


@app.post("/api/mock-speed")
def set_mock_speed(body: MockSpeedBody):
    _engine().set_mock_speed(body.value)
    return {"ok": True}


@app.post("/api/distance/reset")
def reset_distance():
    _engine().reset_distance()
    return {"ok": True}


class ReconnectBody(BaseModel):
    serial_port: str | None = None


@app.post("/api/reconnect")
def reconnect(body: ReconnectBody):
    """Reopen the (optionally new) serial port live and leave mock mode."""
    if body.serial_port:
        app.state.config.serial_port = body.serial_port
        _store().save_config(app.state.config)
    return _engine().reconnect(body.serial_port)


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
@app.get("/api/sessions")
def list_sessions():
    return _store().load_sessions()


@app.delete("/api/sessions")
def clear_sessions():
    _store().clear_sessions()
    return {"ok": True}


class DeleteSessionBody(BaseModel):
    started_at: str


@app.post("/api/sessions/delete")
def delete_session(body: DeleteSessionBody):
    return {"ok": _store().delete_session(body.started_at)}


@app.post("/api/session/start")
def session_start():
    _engine().start_session()
    return {"ok": True}


@app.post("/api/session/stop")
def session_stop():
    s = _engine().stop_session()
    return {"ok": True, "saved": s is not None}


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #
@app.websocket("/ws")
async def ws_status(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            status = _engine().get_status()
            await ws.send_json(status.model_dump(mode="json"))
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — client gone / send failed
        pass


# --------------------------------------------------------------------------- #
def _persist_profiles() -> None:
    _store().save_profiles(app.state.profiles)
    _engine().update_profiles(app.state.profiles)


@app.on_event("shutdown")
def _on_shutdown():
    _engine().stop()
