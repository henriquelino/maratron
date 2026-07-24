# VR games that work with Maratron's gamepad output

Maratron's **gamepad output** emulates a virtual Xbox controller and pushes the **left stick forward** as you walk. The games that "just work", keeping **both** VR controllers and needing **no SteamVR binding**, are **first-person PCVR games where the left stick = walk** and that accept a gamepad.

**Setup:** dashboard → **Config → Game Output = Gamepad** (or **Both**). Needs the ViGEmBus driver. In the game, enable **smooth/direct locomotion + gamepad**, ideally **HMD-relative** (walk where you look). Maratron drives forward only (no strafe/turn from the treadmill). Turn with your head/controller.

> For roomscale games that only do hand-thumbstick locomotion (Ancient Dungeon, Dungeons of Eternity, most native VR titles), the gamepad approach does **not** work. Use the SteamVR driver instead. The recommended path is **Treadmill role + a one-time SteamVR binding**, which keeps **both** controllers (verified on those two games); **Left role** is the zero-setup fallback that sacrifices one hand. See `docs/vr-locomotion.md` and `vr_driver/README.md`.

## Native PCVR games with gamepad walking
- **Skyrim VR**: the easiest guaranteed win. Enable gamepad + smooth locomotion in settings.
- **No Man's Sky (VR)**: full gamepad support.
- **Subnautica / Subnautica: Below Zero (VR)**: gamepad movement.
- **Minecraft VR** (Vivecraft, or the official Win10 VR build): gamepad locomotion option.
- **Third-person gamepad VR** (forward = character walks): **Lucky's Tale, Chronos, Edge of Nowhere, Moss / Moss: Book II**.
- **Fallout 4 VR does NOT work**: motion-controllers only; a gamepad can't drive movement.

## UEVR (Praydog): hundreds of games
[UEVR](https://github.com/praydog/UEVR) injects VR into almost any **Unreal Engine 4.8 to 5.x** flatscreen game, and **gamepad is its primary control** with real player locomotion. The **left stick walks your character**, exactly what Maratron drives. First-person UE games feel close to native VR. One tool covers a large library.

- Examples: Mass Effect Legendary Edition, Lies of P, Hogwarts Legacy, Black Myth: Wukong, Senua's Saga: Hellblade II, Palworld, and many more.
- Best treadmill fit = **first-person** UE games.

**Finding profiles & compatibility:**
- **uevr-profiles.com**: community repository of thousands of downloadable per-game UEVR profiles (search a game → download its profile). Main practical source.
- **UEVR Hub**: https://uevr-profiles.github.io/ (mirror/repository).
- **Official docs**: https://docs.uevr.io/
- **Flat2VR tested-games spreadsheet**: compatibility ratings per game (linked from the Flat2VR community / UEVR docs).

## Flatscreen-in-VR (also gamepad-driven)
- **vorpX** (paid): turns first-person flatscreen games into VR; the game's normal gamepad input moves you, so the left stick walks.
- Steam / Virtual Desktop "theater" mode + any first-person game with gamepad support.

## Cockpit / vehicle games (gamepad, but not "walking")
Not a natural treadmill fit (no walking), but gamepad works if you ever map pace→throttle: **Elite Dangerous, Microsoft Flight Simulator VR, DCS World, Star Wars: Squadrons.**

---

### Summary
- Easiest native win: **Skyrim VR**.
- Biggest library: **UEVR** + any first-person Unreal game (grab its profile from **uevr-profiles.com**).
- Both keep your controllers and need zero SteamVR binding. Set Maratron Output = Gamepad and enable the game's gamepad + smooth locomotion.
