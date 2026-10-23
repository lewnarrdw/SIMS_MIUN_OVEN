"""Validated operating limits and Raspberry Pi pin configuration."""

TEMP_CS_PIN = 18  # MAX31865 software chip select; keep SPI CE0/CE1 pins free.
RTD_NOMINAL = 100.0
RTD_REF_RESISTOR = 430.0
RTD_WIRES = 3
# BCM GPIO7 is SPI CE1; use a free GPIO for the heater relay control instead.
HEATER_PIN = 12
HEATER_WINDOW_S = 1.0
# The heater relay input is inactive when its GPIO signal is LOW.
HEATER_ACTIVE_LOW = False
MOTOR_IN1_PIN = 5
MOTOR_IN2_PIN = 6
MOTOR_ENABLE_PIN = None  # No enable/sleep signal is specified
DOOR_LIMIT_OPEN_PIN = 20
DOOR_LIMIT_CLOSED_PIN = 16
DOOR_TIMEOUT_S = 10.0
TOWER_PINS = {"red": 22, "yellow": 17, "green": 23, "white": 24, "buzzer": 27}
BUZZER_TEST_S = 1.0  # How long Manual controls > Test buzzer sounds the buzzer
RELAY_ACTIVE_LOW = True
BUTTON_START_PIN = 13
BUTTON_STOP_PIN = 21
# The unpressed stop input reads LOW; HIGH means pressed.
STOP_BUTTON_ACTIVE_LOW = False
BUTTON_OPEN_PIN = 19
BUTTON_CLOSE_PIN = 26
ESTOP_SENSE_PIN = 25
# GPIO 25 reads LOW while the E-STOP is engaged and HIGH while released. The circuit
# drives HIGH only while powered; an internal pull-down makes power loss read as engaged.
ESTOP_ACTIVE_LOW = True
MIN_SETPOINT = 30.0
MAX_SETPOINT = 45.0
MAX_TEMPERATURE = 50.0
DOOR_SAFE_TEMPERATURE = 30.0
STABLE_TOLERANCE = 0.1
STABLE_TIME_S = 30.0
HOLD_DURATION_MIN_S = 1.0
HOLD_DURATION_MAX_S = 120.0
SENSOR_PERIOD_S = 1.0 / 15.0
LOOP_PERIOD_S = 1.0
FILTER_WINDOW = 15
SENSOR_FAULT_TIMEOUT_S = 2.0
# Warm-up check: at or above this heater output, the temperature must rise by the
# minimum within the window, or the heater or probe is faulty (e.g. probe fell out).
HEATING_CHECK_OUTPUT_PERCENT = 80.0
HEATING_CHECK_WINDOW_S = 20.0
HEATING_CHECK_MIN_RISE_C = 0.5
PID_KP = 10.0
PID_KI = 0.1
PID_KD = 1.0
ROOM_TEMPERATURE = 20.0
FF_PERCENT_PER_DEGREE = 1.0
DATA_DIRECTORY = "data"
# Minimise button: None shows it only when a desktop session (with a taskbar) can
# restore the window; True or False forces it. A kiosk-started Pi has no way back.
SHOW_MINIMIZE_BUTTON = None
