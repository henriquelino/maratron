"""No-headset test of the Maratron VR data path (Python -> shared memory).

Exercises the exact code the engine uses (VRGamepadOutput) and the exact named
shared region the C++ SteamVR driver reads (Local\\MaratronVRInput), across two
processes -- which is how the driver actually consumes it. No headset needed.

Usage (run two terminals, or writer/sweep in the background):
    python test_vr_ipc.py writer [role]   # ramps a simulated walk pace once, then holds
    python test_vr_ipc.py sweep  [role]   # oscillates the stick -1..+1 forever (triangle)
    python test_vr_ipc.py reader          # samples the shared region and prints it

role is one of: invalid left right optout treadmill  (default: treadmill)
"""
import os
import sys
import time

# repo/python/src  (this file lives at repo/vr_driver/tools/)
REPO_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "python", "src"))
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

from maratron.hardware import VRGamepadOutput, JOY_MAX  # noqa: E402
from maratron.vr_ipc import ROLE_INT, read_shared_memory  # noqa: E402


def run_writer(role_name: str) -> None:
    role = ROLE_INT.get(role_name, 4)
    out = VRGamepadOutput(invert_y=False, role=role)
    print(f"[writer] role={role_name}({role}) JOY_MAX={JOY_MAX} -- ramping pace, Ctrl-C to stop")
    t0 = time.time()
    try:
        while True:
            elapsed = time.time() - t0
            frac = min(1.0, elapsed / 4.0)          # accelerate 0->full over 4s, then hold
            out.set_left_stick_y(int(frac * JOY_MAX))
            if int(elapsed * 2) % 8 == 6:
                out.press("XUSB_GAMEPAD_LEFT_SHOULDER")
            else:
                out.release("XUSB_GAMEPAD_LEFT_SHOULDER")
            out.update()
            time.sleep(0.02)                         # 50 Hz, like the control loop
    except KeyboardInterrupt:
        out.close()
        print("\n[writer] closed (stick zeroed).")


def run_sweep(role_name: str) -> None:
    role = ROLE_INT.get(role_name, 4)
    out = VRGamepadOutput(invert_y=False, role=role)
    period = 6.0
    print(f"[sweep] role={role_name}({role}) -- joyY oscillating -1..+1, Ctrl-C to stop")
    t0 = time.time()
    try:
        while True:
            elapsed = time.time() - t0
            phase = (elapsed % period) / period       # 0..1
            tri = 4 * abs(phase - 0.5) - 1             # -1 .. +1 .. -1 triangle
            out.set_left_stick_y(int(tri * JOY_MAX))
            out.update()
            time.sleep(0.02)
    except KeyboardInterrupt:
        out.close()
        print("\n[sweep] closed (stick zeroed).")


def run_reader() -> None:
    print("[reader] sampling Local\\MaratronVRInput (what the C++ driver reads):")
    print(f"{'t':>5} {'seq':>6} {'joyY':>8} {'role':>5} {'btn':>4}")
    last_seq = -1
    for i in range(20):
        d = read_shared_memory()
        if d is None:
            print(f"{i*0.25:5.2f}   <no writer / region empty>")
        else:
            moved = "*" if d["seq"] != last_seq else " "
            last_seq = d["seq"]
            print(f"{i*0.25:5.2f} {d['seq']:6d} {d['joyY']:8.3f} {d['role']:5d} {d['buttons']:4d} {moved}")
        time.sleep(0.25)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "reader"
    role = sys.argv[2] if len(sys.argv) > 2 else "treadmill"
    if mode == "writer":
        run_writer(role)
    elif mode == "sweep":
        run_sweep(role)
    else:
        run_reader()
