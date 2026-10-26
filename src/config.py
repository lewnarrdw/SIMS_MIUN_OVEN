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
# Signal tower outputs, lights listed top to bottom. The schematic net SIGNAL_WHITE
# (BCM GPIO24) drives the tower's orange light. Red and yellow are swapped relative to
# All_pinnar.docx: the single-light tests on the Pi showed red on GPIO17, yellow on GPIO22.
TOWER_PINS = {"red": 17, "orange": 24, "yellow": 22, "green": 23, "buzzer": 27}
TOWER_LIGHTS = ("red", "orange", "yellow", "green")  # Top to bottom
BUZZER_TEST_S = 10.0  # How long Manual controls > Test buzzer sounds the buzzer
LIGHT_TEST_STEP_S = 0.6  # Pace of Manual controls > Light tower > Test all lights
SINGLE_LIGHT_TEST_S = 3.0  # How long a single-light test keeps its light on
RELAY_ACTIVE_LOW = True
# Per-output polarity. GPIO17 (red) is on optoisolator U56 with the heater and door
# motor, which are active-high; the other outputs are on U23 and switch on when LOW.
# Driving red active-low lit it in Ready and turned it off during E-Stop.
TOWER_ACTIVE_LOW = {"red": False, "orange": True, "yellow": True, "green": True, "buzzer": True}
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
# PID tuned 2026-10-26 from kiln-run-20261023-135828: the oven fits a first-order lag
# with dead time (gain 0.47 °C per % output, time constant 945 s, dead time 17 s;
# 0.21 °C RMS fit over the 35-minute run). The gains (Ti = 180 s, Td = 20 s) came from
# a closed-loop search on that model, checked against gain and time constant ±30 %,
# twice the dead time and rooms of 18.5-22.5 °C.
PID_KP = 33.0
PID_KI = 0.183
PID_KD = 660.0
# The integral only accumulates within this many °C of the target. Integrating during
# the heat-up stores error that carries the oven past the target.
PID_INTEGRAL_ZONE_C = 1.0
ROOM_TEMPERATURE = 20.0
# Steady output per °C above room temperature: 1 / 0.47 °C per % from the fit above.
FF_PERCENT_PER_DEGREE = 2.1
DATA_DIRECTORY = "data"
# Minimise button: None shows it only when a desktop session (with a taskbar) can
# restore the window; True or False forces it. A kiosk-started Pi has no way back.
SHOW_MINIMIZE_BUTTON = None
