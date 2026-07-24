"""Shared-memory bridge from the Python engine to the C++ SteamVR driver.

A single fixed-size struct is overwritten each control tick; only the latest value
matters (last-writer-wins), so named shared memory beats a pipe. The C++ driver
opens the same mapping and reads it every frame.

Struct (48 bytes, little-endian, packed):
    uint32 magic     = 0x4D545652 ("MTVR")
    uint32 version   = 1
    uint64 seq        # bumped each write; driver uses it as a staleness heartbeat
    double timestamp  # time.time(); diagnostics only (NOT for cross-process staleness)
    float  joyX       # -1..1
    float  joyY       # -1..1  (forward)
    float  trigger    # 0..1
    uint32 buttons    # bit0=grip, bit1=unused, bit2=stickClick
    uint32 reserved
"""

from __future__ import annotations

import logging
import mmap
import struct

from .models import BUTTON_CATALOG

log = logging.getLogger("maratron.vr")

MAGIC = 0x4D545652
VERSION = 1
TAGNAME = "Local\\MaratronVRInput"
_FMT = "<IIQdfffII"          # 4+4+8+8+4+4+4+4+4 = 44 -> struct pads? see SIZE
SIZE = 48                    # fixed shared region size

BTN_SPRINT = 1 << 0       # driver maps this to /input/grip/click (a real controller button)
# bit 1 is unused: the VR controller has no "jump" input (this was a phantom mapping)
BTN_STICK_CLICK = 1 << 2  # driver maps this to /input/joystick/click

# SteamVR ETrackedControllerRole values (see openvr_driver.h).
ROLE_INT = {"invalid": 0, "left": 1, "right": 2, "optout": 3, "treadmill": 4}

# The VR input each catalog VR-label drives. models.BUTTON_CATALOG is the single source
# of truth for the button list and which buttons have a VR action; we derive the
# name->bit map from it so the two never drift. A run_button whose catalog entry has no
# VR label is absent here and falls back to grip (see hardware.py _bit).
_VR_LABEL_BITS = {"Grip click": BTN_SPRINT, "Thumbstick click": BTN_STICK_CLICK}
BUTTON_BITS = {
    entry["value"]: _VR_LABEL_BITS[entry["vr"]]
    for entry in BUTTON_CATALOG
    if entry.get("vr") in _VR_LABEL_BITS
}


class VRSharedMemory:
    """Writer side. Pagefile-backed named mapping (fileno -1). Both processes may
    create it, so start order does not matter."""

    def __init__(self) -> None:
        self._mm = mmap.mmap(-1, SIZE, tagname=TAGNAME)
        self._seq = 0

    def write(self, joy_x: float, joy_y: float, trigger: float, buttons: int,
              timestamp: float, role: int = 3) -> None:
        self._seq += 1
        data = struct.pack(
            _FMT, MAGIC, VERSION, self._seq, timestamp,
            float(joy_x), float(joy_y), float(trigger), int(buttons) & 0xFFFFFFFF,
            int(role) & 0xFFFFFFFF,
        )
        self._mm.seek(0)
        self._mm.write(data.ljust(SIZE, b"\x00"))

    def close(self) -> None:
        try:
            # zero it so a lingering reader sees the stick centered
            self._mm.seek(0)
            self._mm.write(b"\x00" * SIZE)
            self._mm.close()
        except Exception:  # noqa: BLE001
            pass


def read_shared_memory() -> dict | None:
    """Debug/verification helper — reads the current struct (returns None if empty)."""
    try:
        mm = mmap.mmap(-1, SIZE, tagname=TAGNAME)
    except Exception:  # noqa: BLE001
        return None
    try:
        raw = mm.read(SIZE)
        magic, version, seq, ts, jx, jy, tr, btn, role = struct.unpack(_FMT, raw[:struct.calcsize(_FMT)])
        if magic != MAGIC:
            return None
        return {"version": version, "seq": seq, "timestamp": ts,
                "joyX": jx, "joyY": jy, "trigger": tr, "buttons": btn, "role": role}
    finally:
        mm.close()
