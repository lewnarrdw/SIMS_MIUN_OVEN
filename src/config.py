"""Validated operating limits and Raspberry Pi pin configuration."""

from pathlib import Path

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
# Signal tower outputs, lights listed top to bottom. The schematic net SIGNAL_WHITE
# (BCM GPIO24) drives the tower's orange light. Red and yellow are swapped relative to
# All_pinnar.docx: the single-light tests on the Pi showed red on GPIO17, yellow on GPIO22.
TOWER_PINS = {"red": 17, "orange": 24, "yellow": 22, "green": 23, "buzzer": 27}
TOWER_LIGHTS = ("red", "orange", "yellow", "green")  # Top to bottom
BUZZER_TEST_S = 1.0  # How long Manual controls > Test buzzer sounds the buzzer
LIGHT_TEST_STEP_S = 0.6  # Pace of Manual controls > Light tower > Test all lights
SINGLE_LIGHT_TEST_S = 3.0  # How long a single-light test keeps its light on
RELAY_ACTIVE_LOW = True
# Per-output polarity. GPIO17 (red) is on optoisolator U56 with the heater and door
# motor, which are active-high; the other outputs are on U23 and switch on when LOW.
# Driving red active-low lit it in Ready and turned it off during E-Stop.
TOWER_ACTIVE_LOW = {"red": False, "orange": True, "yellow": True, "green": True, "buzzer": False}
BUTTON_START_PIN = 13
BUTTON_STOP_PIN = 4
# The stop button is active-low: unpressed reads HIGH, pressed reads LOW.
STOP_BUTTON_ACTIVE_LOW = True
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
# Hold starts once the temperature has settled: over the last STABLE_TIME_S, the median
# reading is within STABLE_TOLERANCE of the target and at least STABLE_IN_BAND_SHARE of
# the readings are inside that band, so the hold starts about STABLE_TIME_S after the
# temperature settles. On the first run with the tuned PID (kiln-run-20261026-123831)
# the temperature entered the band at 249 s and never left it (max +0.07 °C); a 120 s
# window held it back until 358 s, while 45 s starts the hold at about 290 s.
STABLE_TOLERANCE = 0.1
STABLE_TIME_S = 45.0
STABLE_IN_BAND_SHARE = 0.9
HOLD_DURATION_MIN_S = 1.0
HOLD_DURATION_MAX_S = 120.0
SENSOR_PERIOD_S = 1.0 / 15.0
LOOP_PERIOD_S = 1.0
FILTER_WINDOW = 15
SENSOR_FAULT_TIMEOUT_S = 2.0
# Warm-up check: at or above this heater output, the temperature must rise by the
# minimum within the window, or the heater or probe is faulty (e.g. probe fell out).
# The window must outlast the heater element lag: a slow heater can barely move the
# probe in the first 20 s from cold. Tune both values from a logged run on the real oven.
HEATING_CHECK_OUTPUT_PERCENT = 80.0
HEATING_CHECK_WINDOW_S = 120.0
HEATING_CHECK_MIN_RISE_C = 1.0
# PI retuned 2026-10-30 (D off: it mostly amplified the sensor's 0.034 °C steps, swinging
# the heater 8-41 % while holding). Closed-loop search with the real PID and hold rules
# on three oven models (gain 0.39 °C/%, time constant 636 s, heater lag 30 s, dead time
# 18 s from kiln-run-20261026-123831; 0.47 °C/%, 945 s, dead 17 s from the earlier fit;
# and a refit of kiln-run-20261023-135828), rooms of 18.5-24 °C, cold and warm starts,
# targets 30-45 °C, readings rounded to the sensor's step. The first model reproduces
# good_test (2026-10-30) with a room of about 23.5 °C. Ti = 211 s.
PID_KP = 19.0
PID_KI = 0.09
PID_KD = 0.0
# The integral only accumulates within this many °C of the target. Integrating during
# the heat-up stores error that carries the oven past the target.
PID_INTEGRAL_ZONE_C = 1.0
# Room the feed-forward assumes: between the lab (about 20 °C) and the warmer room of
# good_test (about 23.5 °C). Too high only leaves the integral a little more to do;
# too low adds heat the oven can only lose slowly, so the guess leans warm.
ROOM_TEMPERATURE = 22.0
# Steady output per °C above room temperature. Holding 30 °C in a 20.1 °C room took
# 24 % on average in kiln-run-20261026-123831, i.e. 2.4 %/°C.
FF_PERCENT_PER_DEGREE = 2.4
# Next to the program, not wherever it was started from (e.g. ~ when launched from a shortcut).
DATA_DIRECTORY = str(Path(__file__).resolve().parent / "data")
# Minimise button: None shows it only when a desktop session (with a taskbar) can
# restore the window; True or False forces it. A kiosk-started Pi has no way back.
SHOW_MINIMIZE_BUTTON = None
