
import serial
import time
import vgamepad as vg
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime
import json
import os

class SprintMethod(str, Enum):
    NONE: str = 'none'
    HOLD: str = 'hold'
    CLICK_RELEASE: str = 'click_release'

class Profile(BaseModel):
    """
    Quick Tweaks Guide
        Goal                            |	Parameter           |	Change  
    Need to walk MORE to reach 100%     | max_pulses_per_second | Increase (30→40)
    Need to walk LESS to reach 100%     | max_pulses_per_second | Decrease (30→20)
    Everything feels sluggish           | smoothing             | Increase (0.3→0.5)
    Joystick is too twitchy             | smoothing             | Decrease (0.3→0.15)
    Ignore tiny accidental movements    | deadzone              | Increase (0.01→0.05)
    """
    max_pulses_per_second: int = Field(30, description="""
        Effect: Controls how much you need to walk to max out the joystick
        To make MORE sensitive (barely walk = 100%): Lower this value (e.g., 20, 15)
        To make LESS sensitive (walk more = 100%): Raise this value (e.g., 40, 50)
        How it works: Speed is normalized by dividing actual pulses by this value. Lower values mean fewer pulses needed to reach 100%
    """)
    gain: float = Field(1.1, description="""
        Effect: Direct multiplier on all movement speed
        To make MORE sensitive: Increase (e.g., 1.5, 2.0)
        To make LESS sensitive: Decrease (e.g., 0.5, 0.7)
        How it works: Directly scales the speed output before joystick mapping
    """)
    deadzone: float = Field(0.02, description="""
        Effect: Minimum speed threshold before any movement registers
        To ignore more tiny movements (less sensitive to small shuffles): Increase (e.g., 0.05, 0.1)
        To respond to smaller movements: Decrease (e.g., 0.005)
        How it works: Speeds below this are forced to zero
    """)
    smoothing: float = Field(0.3, description="""
        Effect: How quickly the joystick responds to changes (exponential moving average)
        Value range: 0.0-1.0 (higher = more responsive)
        More responsive (snappy, sensitive to quick changes): Increase (e.g., 0.5, 0.7)
        Smoother, less twitchy (sluggish, slower response): Decrease (e.g., 0.1, 0.15)
        How it works: filtered_speed = old * (1 - smoothing) + new * smoothing
    """)
    
    walk_threshold: float = Field(0.40, description="""
        Effect: Speed at which the sensitivity curve changes (not overall sensitivity, but curve shape)
        Below threshold: Finer control (quadratic curve)
        Above threshold: Less steep curve (0.6 power)
        Purpose: Gives precise control at low speeds, faster ramping at high speeds
    """)
    run_threshold: float = Field(0.90, description="""
        Effect: At what joystick percentage (0-100%) the sprint button activates
        Lower value (e.g., 70): Sprint triggers earlier with less movement
        Higher value (e.g., 95): Need to walk faster to trigger sprint
    """)

    sprint_method: SprintMethod = Field(SprintMethod.HOLD, description="""
        HOLD: Holds the sprint button while moving fast
        CLICK_RELEASE: Taps sprint button for 0.1s bursts
    """)
    run_button : int = Field(vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB)

# ================= CONFIG =================

skyrim = Profile(
    max_pulses_per_second=110,
    gain=1.1,
    deadzone=0.01,
    smoothing=0.6, # original .3
    run_threshold=0.80,
    walk_threshold=0.20,
    sprint_method=SprintMethod.HOLD,
    run_button = vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER
)
# amount of magnets in treadmill (4) divided by the distance it takes to complete one full revolution (13cm) = 13/4 = 3.25cm per pulse
AMOUNT_OF_MAGNETS = 11
ONE_REVOLUTION_CM = 12.9
# PULSES_OVER_30CM=29


# over a couple of tries, I've measured approximately 2.9cm per pulse
# moving the belt by hand over 30cm and counting pulses
# took avg 29 pulses to cover 30cm
# 29p/30cm
# 2,9cm/p
# 11p=1rev
# // 11 magnets
# DISTANCE_PER_PULSE_CM = 30 / PULSES_OVER_30CM

DISTANCE_PER_PULSE_CM = ONE_REVOLUTION_CM / AMOUNT_OF_MAGNETS

SERIAL_PORT = "COM7"
BAUDRATE = 115200
DISTANCE_LOG_FILE = "distance_log.json"

# ==========================================


def get_treadmill_data(ser: serial.Serial) -> tuple[int, int] | None:
    """Request and read treadmill pulse data from Arduino."""
    ser.write(b"R")
    line = ser.readline().decode().strip()
    
    if line:
        try:
            pulses, timestamp = line.split(",")
            return int(pulses), int(timestamp)
        except ValueError:
            return None
    return None


def save_distance_log(pulses: int) -> None:
    """Save distance walked to a JSON log file with timestamp."""
    distance_cm = pulses * DISTANCE_PER_PULSE_CM
    entry = {
        "timestamp": datetime.now().isoformat(),
        "total_pulses": pulses,
        "cm_per_pulse": round(DISTANCE_PER_PULSE_CM, 4),
        "distance_cm": round(distance_cm, 4),
        "distance_m": round(distance_cm / 100.0, 4),
        "distance_km": round(distance_cm / 100000.0, 4)
    }
    
    log_data = []
    if os.path.exists(DISTANCE_LOG_FILE):
        try:
            with open(DISTANCE_LOG_FILE, 'r') as f:
                log_data = json.load(f)
        except:
            log_data = []
    
    log_data.append(entry)
    
    with open(DISTANCE_LOG_FILE, 'w') as f:
        json.dump(log_data, f, indent=2)
    
    print(f"\nDistance logged: {distance_cm:.2f}cm ({distance_cm/100:.2f}m)")


def main(profile: Profile):
    walk_threshold = profile.walk_threshold
    max_pulses_per_second = profile.max_pulses_per_second
    gain = profile.gain
    deadzone = profile.deadzone
    smoothing = profile.smoothing
    run_threshold = profile.run_threshold
    sprint_method = profile.sprint_method
    run_button = profile.run_button

    filtered_speed = 0.0
    button_press_time = None
    is_running = False
    total_pulses = 0
    
    # Arduino timing
    last_pulse_count = 0
    last_arduino_ms = 0
    poll_interval = 0.1  # 100ms polling interval
        
    gamepad = vg.VX360Gamepad()
    ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=1)
    print("Connected. Waiting for data...")

    while True:
        try:
            time.sleep(poll_interval)
            
            # Get treadmill data from Arduino
            data = get_treadmill_data(ser)
            if not data:
                continue
            
            current_pulses, arduino_ms = data
            
            # Calculate deltas
            new_pulses = current_pulses - last_pulse_count
            time_delta_ms = arduino_ms - last_arduino_ms
            
            last_pulse_count = current_pulses
            last_arduino_ms = arduino_ms
            
            # Track distance walked
            total_pulses += new_pulses
            
            # Convert time delta to seconds
            interval_s = time_delta_ms / 1000.0 if time_delta_ms > 0 else poll_interval
            
            # Amount of pulses per second
            pulses_per_sec = new_pulses / interval_s

            # Normalized speed (0.0 → 1.0)
            # to calibrate, walk normally and adjust max_pulses_per_second until speed = 1.0
            # when running, speed will be > 1.0, example: 1.5
            speed = pulses_per_sec / max_pulses_per_second

            # apply a gain so walking less can reach max speed easier
            speed_with_gain = speed * gain

            # clamp speed between 0 and 1
            speed_with_gain = max(0.0, min(speed_with_gain, 1.0))
            
            # smoothing (EMA)
            filtered_speed = (
                filtered_speed * (1.0 - smoothing)
                + speed_with_gain * smoothing
            )

            # Snap to zero when stopped
            if new_pulses == 0 and filtered_speed < 0.01:
                filtered_speed = 0.0

            # deadzone
            if filtered_speed < deadzone:
                filtered_speed = 0.0
            
            if filtered_speed < walk_threshold:
                # caminhada lenta (controle fino)
                curved = (filtered_speed / walk_threshold) ** 2 * walk_threshold
            else:
                # caminhada rápida / corrida
                high = (filtered_speed - walk_threshold) / (1.0 - walk_threshold)
                curved = walk_threshold + (high ** 0.6) * (1.0 - walk_threshold)
                
            # Xbox expects -32768 → +32767
            joy_y = int(curved * 32767)

            # Apply to LEFT STICK Y (forward)
            gamepad.left_joystick(
                x_value=0,
                y_value=joy_y
            )
            
            if sprint_method:
                running_threshold_breached = joy_y > (32767 * run_threshold )

                if sprint_method == 'hold':
                    # hold while running
                    if not is_running and running_threshold_breached:
                        gamepad.press_button(run_button)
                        is_running = True
                    elif is_running and (not running_threshold_breached):
                        gamepad.release_button(run_button)
                        is_running = False

                elif sprint_method == 'click_release':
                    current_time = time.time()

                    # press sprint and release after short delay
                    # Press button when crossing threshold
                    if not is_running and running_threshold_breached:
                        # Just crossed threshold - press button
                        gamepad.press_button(run_button)
                        is_running = True
                        button_press_time = current_time
                    elif button_press_time is not None and (current_time - button_press_time >= 0.1):
                        # Release after 0.1 seconds
                        gamepad.release_button(run_button)
                        button_press_time = None
                    elif is_running and (not running_threshold_breached):
                        # Dropped below threshold - reset state
                        is_running = False

            gamepad.update()

        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print("Error:", e)
            break
    save_distance_log(total_pulses)


if __name__ == "__main__":
    main(skyrim)