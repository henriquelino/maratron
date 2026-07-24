# Image shot list

This folder holds the guide images. Each entry gives the filename and what it shows.

Keep the screenshots at the same window size for a consistent look. The native window opens at 1360 by 900.

## Real-world photos (in the folder)

- hw-esp32-wiring.jpg, hw-esp32-wiring2.jpg: the ESP32 wired to the reed switch, two views.
- hw-magnet-disc.jpg, hw-magnet-disc-closeup.jpg: the 42 cm magnet disc with the tape and the section marks.
- hw-magnets.jpg: a magnet fixed on each disc mark.
- roller_magnets_placement.png: the target layout, magnets evenly spaced with the original magnet as zero.
- hw-sensor.jpg: the reed switch mounted next to the disc, with the air gap visible.
- hw-paracord-arms.jpg: trucker's-hitch triangle bracing on the arm supports.
- hw-paracord-frame.jpg: paracord cross bracing under the bottom frame.
- measures.png: schematic of bed length (A), front height (B), and back height (C).
- measure-bed-length.jpg, measure-bed-length-start.jpg: the tape along the board, about 80 cm.
- measure-front-height.jpg, measure-back-height.jpg: front (18.5 cm) and back (11.5 cm) deck heights.
- incline-hook.jpg: the back-height pin that sets the incline.

## Program screenshots (in the folder)

- ui-first-run.png (launch screen, Treadmill tab)
- ui-config.png, ui-config-people.png, ui-config-treadmill.png, ui-config-incline.png, ui-config-general.png
- ui-help-popover.png
- ui-game-profile.png, ui-game-profile-output-dropdown.png
- ui-calibration-max-pps.png, ui-calibration-copy.png
- ui-curve.png
- ui-treadmill-idle.png, ui-mock-simulator.png
- ui-activity-log.png, ui-session-modal.png

## VR binding walkthrough (in the folder)

- vr-bind-1-controller-settings.png through vr-bind-8-return-left-hand.png: the eight SteamVR steps to bind maratron_treadmill (shots from Ancient Dungeon VR).
- vr-locomotion-demo.mp4: the mock speed slider driving forward locomotion in Dungeons of Eternity (simulated input, not live walking).

## Not planned

- ui-treadmill-live.png: cannot walk and screenshot at once. The mock-mode shot (ui-mock-simulator.png) shows the same live view.
- rig-overview.jpg: the setup is wall-mounted; cannot frame the whole rig in one shot.
- measure-stride.jpg: no point in a photo of a walking person.
- vr-headset-play.jpg: not going to shoot this.

## Build notes (kept from the shoot)

These are the original per-image notes, kept so they are not lost.

- Wiring: added wiring views 1 and 2.
- Magnet disc: this is the magnet mounting disc, not the belt. It is a circle with a reed switch. It was measured and divided into equal sections to mount the magnets. Total disc size is 42 cm. hw-magnets shows the tape, the marks, and a magnet on each mark. The treadmill's single original magnet, the black circle, is the zero mark. The finished result is like roller_magnets_placement, but the magnets should be evenly placed, not crooked as in that drawing. hw-sensor shows the reed switch; the magnets line up to it so each pass triggers the switch.
- Bed and heights: measures.png is the drawing. A = bed length, B = front height, C = back height. The bed length start is measure-bed-length-start.jpg and the end is measure-bed-length.jpg, 80 cm total. measure-back-height.jpg is C for the current setup, shown in incline-hook.jpg. measure-front-height.jpg is B. These are not exact; about 0.1 cm deviation is not a problem.
- One-revolution distance: the disc size (42 cm) only sets the magnet spacing. The calibration distance is one full belt revolution, which is 12.9 cm.
- Reed switch: gives 0 or 1 only, not a hall sensor. It is simpler and has only two wires.
- Incline pin (incline-hook.jpg): the back-height setting. Pull the pin and the leg goes up, which lowers the back and increases the incline.
- Calibration: made two images. ui-calibration-max-pps.png has the toast of the peak pps. ui-calibration-copy.png has the value set as max pps in the profile.
- ui-vr-config: not needed; the open Output dropdown (ui-game-profile-output-dropdown.png) already shows the VR selection.
- SteamVR binding: captured as the vr-bind-1 through vr-bind-8 series (Ancient Dungeon VR).
- ui-treadmill-live: cannot walk and screenshot at the same time. Use mock mode.
- measure-stride and vr-headset-play: will not shoot these.
