# Maratron SteamVR driver

A user-mode SteamVR (OpenVR) driver that registers its own **`maratron_treadmill`** controller whose **joystick** is driven by your treadmill pace (fed from the Maratron app over shared memory). No kernel-driver signing.

**PCVR / SteamVR on Windows only** (Quest via Link / Air Link / Virtual Desktop counts: the game runs on the PC). It cannot drive **native Quest** games (no PC process); that would need the on-headset FTMS route.

## Prerequisites
- SteamVR installed.
- Visual Studio 2022 / Build Tools 2022 with the **"Desktop development with C++"** workload (gives MSVC, CMake, Ninja).

## Build / register
```powershell
cd vr_driver
.\build.ps1        # -> maratron/bin/win64/driver_maratron.dll  (close SteamVR first, it locks the DLL)
.\register.ps1     # add to SteamVR   (.\register.ps1 -Remove to undo)
```
Restart SteamVR after registering (and after any rebuild). Also set `activateMultipleDrivers: true` in `steamvr.vrsettings` so this driver loads alongside your headset's driver.

## Which route for which game

| Your game | Setting | Controllers | Notes |
|---|---|---|---|
| Roomscale hand-stick locomotion, most VR games (Ancient Dungeon, Dungeons of Eternity, …) | Output **VR**, role **Treadmill** + a one-time SteamVR binding | **Both kept**, recommended | Verified working. Bind once (below); then walking moves you, both controllers free. |
| Same, but you want zero setup / a quick test | Output **VR**, role **Left** | Real **left** set aside; right still aims | Fallback: always moves in free-locomotion games with no binding, but the left controller goes dead while active. |
| Game with built-in **gamepad** locomotion (Skyrim VR, No Man's Sky, UEVR, flat-to-VR) | Output **Gamepad** (needs ViGEmBus) | Both kept | Enable the game's gamepad/flat locomotion. Kept for broad compatibility. |

**One-time keep-both binding (Treadmill role):** SteamVR → Settings → Controllers → Manage Controller Bindings → [game] → find the **`maratron_treadmill`** device → bind its **joystick → the game's move action**, and/or **Extra Settings → "Return bindings with left hand."** Save. Full explanation of the mechanism (own input profile + a legacy binding that routes `/user/treadmill/input/joystick` → the left hand's stick axis) is in `../docs/vr-locomotion.md`.

**Order of operations for VR:** start Maratron → set Output=VR + role **Treadmill** → then (re)start SteamVR, so the driver reads the role at load → bind once per game.

## How it works
- Own controller type `maratron_treadmill` with an input profile + legacy binding shipped in `maratron/resources/input/`. Modeled on OpenVR-WalkInPlace.
- Shared memory `Local\MaratronVRInput` (struct in `src/driver_maratron.cpp` **must stay in sync with** `python/src/maratron/vr_ipc.py`). Carries joystick x/y, trigger, buttons, and the chosen role.
- The device stays connected (visible/bindable); the stick centers when you're not moving, and zeroes if Maratron stops writing (>~0.4 s).
- Buttons today: sprint → grip click. (See "planned improvements" in `../docs/vr-locomotion.md`: VR button mapping should become configurable.)

## No-headset testing
`tools/test_vr_ipc.py` (writer/sweep/reader over the shared region) and `tools/client_read.py` (read the device back from SteamVR via the `openvr` pip pkg) let you validate the whole pipeline without a headset. A headless "virtual headset" is available via the null driver. See `../docs/vr-locomotion.md`.

## Gotchas
- **Left/Right role replaces that real controller** while active, inherent to SteamVR's per-hand model. Use **Treadmill role** for keep-both; Left/Right is only the zero-binding fallback.
- Rebuild fails with `LNK1104` if SteamVR is running (it locks the DLL); quit SteamVR first.
- If forward walks you backward, toggle **Invert VR forward** in Config.
- If SteamVR updates and rejects the driver, bump the OpenVR tag/header and rebuild.
