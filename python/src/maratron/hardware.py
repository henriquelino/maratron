"""I/O abstractions for pulse input and gamepad output.

Both real (serial / vgamepad) and mock/null implementations live here. vgamepad
and pyserial are imported lazily so the server runs on machines without ViGEmBus
or a connected ESP32 (mock mode).
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Protocol

log = logging.getLogger("maratron.hardware")

JOY_MAX = 32767


# --------------------------------------------------------------------------- #
# Pulse sources
# --------------------------------------------------------------------------- #
class PulseSource(Protocol):
    def read(self) -> tuple[int, int] | None:
        """Return (pulse_count, arduino_ms) or None if no data."""

    def reset(self) -> None: ...

    def close(self) -> None: ...


class SerialPulseSource:
    """Reads pulses from the ESP32 over USB serial (request/response protocol)."""

    def __init__(self, port: str, baudrate: int) -> None:
        import serial  # lazy: pyserial only needed for real hardware

        self._ser = serial.Serial(port, baudrate, timeout=1)

    def read(self) -> tuple[int, int] | None:
        self._ser.write(b"R")
        line = self._ser.readline().decode(errors="ignore").strip()
        if not line:
            return None
        try:
            pulses, timestamp = line.split(",")
            return int(pulses), int(timestamp)
        except ValueError:
            return None

    def reset(self) -> None:
        try:
            self._ser.write(b"C")
            self._ser.readline()  # consume ACK:RESET
        except Exception as e:  # noqa: BLE001
            log.warning("serial reset failed: %s", e)

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:  # noqa: BLE001
            pass


class MockPulseSource:
    """Synthesizes pulses from a target-speed getter — no hardware required.

    Each read() advances a simulated ``millis()`` clock by ``poll_interval`` and
    adds pulses proportional to ``target_speed * max_pps``. ``target_speed`` is a
    0..~1.3 value the UI slider drives.
    """

    def __init__(
        self,
        target_speed_getter: Callable[[], float],
        max_pps_getter: Callable[[], float],
        poll_interval: float,
    ) -> None:
        self._target = target_speed_getter
        self._max_pps = max_pps_getter
        self._poll = poll_interval
        self._pulses = 0
        self._ms = 0
        self._accum = 0.0

    def read(self) -> tuple[int, int] | None:
        self._ms += int(self._poll * 1000)
        target = max(0.0, self._target())
        max_pps = self._max_pps() or 1.0
        # fractional pulses accumulate so slow speeds still register over time
        self._accum += target * max_pps * self._poll
        whole = int(self._accum)
        self._accum -= whole
        self._pulses += whole
        return self._pulses, self._ms

    def reset(self) -> None:
        self._pulses = 0
        self._accum = 0.0

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# Gamepad outputs
# --------------------------------------------------------------------------- #
class GamepadOutput(Protocol):
    def set_left_stick_y(self, joy_y: int) -> None: ...

    def press(self, button_name: str) -> None: ...

    def release(self, button_name: str) -> None: ...

    def update(self) -> None: ...


class VGamepadOutput:
    """Wraps a virtual Xbox 360 pad via vgamepad (requires ViGEmBus)."""

    def __init__(self) -> None:
        import vgamepad as vg  # lazy: only when real output is requested

        self._vg = vg
        self._pad = vg.VX360Gamepad()

    def _button(self, name: str):
        return getattr(self._vg.XUSB_BUTTON, name)

    def set_left_stick_y(self, joy_y: int) -> None:
        self._pad.left_joystick(x_value=0, y_value=joy_y)

    def press(self, button_name: str) -> None:
        self._pad.press_button(self._button(button_name))

    def release(self, button_name: str) -> None:
        self._pad.release_button(self._button(button_name))

    def update(self) -> None:
        self._pad.update()


class NullGamepadOutput:
    """No-op gamepad; records the last joy_y for status/debugging."""

    def __init__(self) -> None:
        self.last_joy_y = 0
        self.pressed: set[str] = set()

    def set_left_stick_y(self, joy_y: int) -> None:
        self.last_joy_y = joy_y

    def press(self, button_name: str) -> None:
        self.pressed.add(button_name)

    def release(self, button_name: str) -> None:
        self.pressed.discard(button_name)

    def update(self) -> None:
        pass


class VRGamepadOutput:
    """Feeds a SteamVR controller thumbstick via shared memory (see vr_ipc + the C++
    driver). Implements the same GamepadOutput protocol, so the engine loop is agnostic.
    Works regardless of mock mode — the shared memory is just an mmap."""

    def __init__(self, invert_y: bool = False, role: int = 3) -> None:
        from .vr_ipc import VRSharedMemory, BUTTON_BITS

        self._shm = VRSharedMemory()
        self._bits = BUTTON_BITS
        self._invert = invert_y
        self._role = role
        self._joy_y = 0.0
        self._buttons = 0
        self._trigger = 0.0

    def set_left_stick_y(self, joy_y: int) -> None:
        y = max(-1.0, min(1.0, joy_y / JOY_MAX))
        self._joy_y = -y if self._invert else y

    def _bit(self, name: str) -> int:
        return self._bits.get(name, 1)  # default: sprint (bit0)

    def press(self, button_name: str) -> None:
        self._buttons |= self._bit(button_name)

    def release(self, button_name: str) -> None:
        self._buttons &= ~self._bit(button_name)

    def update(self) -> None:
        self._shm.write(0.0, self._joy_y, self._trigger, self._buttons, time.time(), self._role)

    def close(self) -> None:
        self._shm.close()


class CompositeOutput:
    """Fans out to several outputs at once (e.g. gamepad + VR)."""

    def __init__(self, outputs: list) -> None:
        self._outs = outputs

    def set_left_stick_y(self, joy_y: int) -> None:
        for o in self._outs:
            o.set_left_stick_y(joy_y)

    def press(self, button_name: str) -> None:
        for o in self._outs:
            o.press(button_name)

    def release(self, button_name: str) -> None:
        for o in self._outs:
            o.release(button_name)

    def update(self) -> None:
        for o in self._outs:
            o.update()

    def close(self) -> None:
        for o in self._outs:
            if hasattr(o, "close"):
                o.close()


def make_gamepad(mock: bool) -> GamepadOutput:
    """Return a real gamepad, degrading to NullGamepadOutput (never raising)."""
    if mock:
        return NullGamepadOutput()
    try:
        return VGamepadOutput()
    except Exception as e:  # noqa: BLE001
        log.warning("vgamepad unavailable (%s); falling back to null output", e)
        return NullGamepadOutput()


def _make_vr(config) -> GamepadOutput:
    try:
        from .vr_ipc import ROLE_INT

        role = ROLE_INT.get(getattr(config, "vr_role", "optout"), 3)
        return VRGamepadOutput(invert_y=getattr(config, "vr_invert_y", False), role=role)
    except Exception as e:  # noqa: BLE001
        log.warning("VR output unavailable (%s); falling back to null output", e)
        return NullGamepadOutput()


def make_output(config, output_mode: str | None = None) -> GamepadOutput:
    """Build the control output(s). ``output_mode`` (gamepad | vr | both) is passed by
    the engine from the active profile; falls back to config.output_mode when None.
    VR role/invert always come from the global config (see _make_vr)."""
    mode = output_mode or getattr(config, "output_mode", "gamepad")
    if mode == "vr":
        return _make_vr(config)
    if mode == "both":
        return CompositeOutput([make_gamepad(config.mock), _make_vr(config)])
    return make_gamepad(config.mock)
