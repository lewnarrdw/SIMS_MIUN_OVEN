"""Central configuration for the kiln control system.

All pin numbers use BCM (GPIO) numbering, not physical pin numbers.
Values marked TODO must be confirmed with the EE team.
"""

# --- Mode ---
SIMULATION = True               # True = fake hardware (works on any laptop)

# --- Temperature sensor: MAX31865 + PT100 (3-wire) ---
TEMP_CS_PIN = 5                 # SPI chip select
RTD_NOMINAL = 100.0             # PT100 = 100 ohm at 0 °C
RTD_REF_RESISTOR = 430.0        # TODO: confirm resistor value on the board
RTD_WIRES = 3

# --- Filtering ---
FILTER_WINDOW = 15              # number of readings in the rolling median

# --- Heater ---
HEATER_PIN = 23
HEATER_WINDOW_S = 5.0           # time-proportional window in seconds

# --- Door motor (MP6550 driver) ---
MOTOR_IN1_PIN = 18
MOTOR_IN2_PIN = 19
MOTOR_SLEEP_PIN = 26
DOOR_LIMIT_OPEN_PIN = 20
DOOR_LIMIT_CLOSED_PIN = 21
DOOR_TIMEOUT_S = 10.0           # stop motor if a limit isn't reached in time

# --- Signal tower (relay board) ---
TOWER_PINS = {
    "red": 17,
    "yellow": 27,
    "green": 22,
    "buzzer": 24,
}
RELAY_ACTIVE_LOW = True         # TODO: confirm with the relay board

# --- Buttons ---
BUTTON_START_PIN = 6
BUTTON_STOP_PIN = 13
BUTTON_OPEN_PIN = 16
BUTTON_CLOSE_PIN = 25
ESTOP_SENSE_PIN = 4             # TODO: depends on EE's E-stop design


# --- Filtering ---
FILTER_WINDOW = 15              # number of readings in the rolling median
SENSOR_PERIOD_S = 0.1           # read the sensor 10 times per second    


# --- Control loop ---
LOOP_PERIOD_S = 1.0             # controller runs once per second

# --- PID gains (placeholders, tuned later with real data) ---
PID_KP = 5.0
PID_KI = 0.1
PID_KD = 1.0

# --- Temperature range ---
MIN_SETPOINT = 20.0             # lowest target the user can choose
MAX_SETPOINT = 45.0             # highest target the user can choose

# --- Safety limits ---
MAX_TEMPERATURE = 50.0          # FAULT above this 
MAX_SENSOR_FAULTS = 3           # consecutive bad readings before FAULT