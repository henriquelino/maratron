"""Read our device's stick back FROM SteamVR (app side), no headset.

Proves the full chain: Python -> shared memory -> C++ driver -> SteamVR.
Run `test_vr_ipc.py sweep` in another process first, and have SteamVR running
(a headset, or the null/headless HMD driver). Requires: pip install openvr.

NOTE: legacy getControllerState only reports axes for HAND-role devices; a
Treadmill-role device shows its stick in SteamVR's Test Controller UI but not
here. Use this mainly to confirm the device exists and its role/type.
"""
import time
import openvr

TARGET = "maratron-treadmill"   # serial prefix of our device

vr = openvr.init(openvr.VRApplication_Background)
try:
    idx = None
    for i in range(openvr.k_unMaxTrackedDeviceCount):
        if vr.getTrackedDeviceClass(i) == openvr.TrackedDeviceClass_Invalid:
            continue
        try:
            serial = vr.getStringTrackedDeviceProperty(i, openvr.Prop_SerialNumber_String)
        except Exception:
            serial = ""
        if TARGET in serial:
            role = vr.getInt32TrackedDeviceProperty(i, openvr.Prop_ControllerRoleHint_Int32)
            ctype = vr.getStringTrackedDeviceProperty(i, openvr.Prop_ControllerType_String)
            print(f"found device idx={i} serial={serial} roleHint={role} type={ctype}")
            idx = i
            break

    if idx is None:
        print("!! device not found -- is SteamVR running with our driver loaded?")
        raise SystemExit(1)

    print("\nlegacy controller axes (only populated for hand-role devices):")
    print(f"{'t':>5}  {'ax0(x,y)':>16} {'ax1':>8} {'ax2':>8}")
    for k in range(20):
        _res, state = vr.getControllerState(idx)
        a = state.rAxis
        print(f"{k*0.25:5.2f}  ({a[0].x:+.3f},{a[0].y:+.3f})   {a[1].x:+.3f}   {a[2].x:+.3f}")
        time.sleep(0.25)
finally:
    openvr.shutdown()
