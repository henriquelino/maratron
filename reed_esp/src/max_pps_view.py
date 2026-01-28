
import serial
import time

SERIAL_PORT = "COM7"
BAUDRATE = 115200


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


def main():
    last_pulse_count = 0
    last_arduino_ms = 0
    max_pps = 0.0
    poll_interval = 0.1
    
    ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=1)
    print("Connected. Walk on treadmill to measure max PPS...")
    print("Press Ctrl+C to stop.\n")

    while True:
        try:
            time.sleep(poll_interval)
            
            data = get_treadmill_data(ser)
            if not data:
                continue
            
            current_pulses, arduino_ms = data
            
            new_pulses = current_pulses - last_pulse_count
            time_delta_ms = arduino_ms - last_arduino_ms
            
            last_pulse_count = current_pulses
            last_arduino_ms = arduino_ms
            
            if time_delta_ms > 0:
                pulses_per_sec = new_pulses / (time_delta_ms / 1000.0)
                max_pps = max(max_pps, pulses_per_sec)
                
                if new_pulses > 0:
                    print(f"PPS: {pulses_per_sec:6.1f} | Max PPS: {max_pps:6.1f}")

        except KeyboardInterrupt:
            print(f"\n\nMax PPS reached: {max_pps:.1f}")
            break
        except Exception as e:
            print("Error:", e)
            break


if __name__ == "__main__":
    main()