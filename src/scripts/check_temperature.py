"""Print raw MAX31865 readings to find out why the PT100 gives no valid temperature.

Run on the Pi with the oven application closed:  python scripts/check_temperature.py
"""
import glob
import sys
from pathlib import Path
from time import sleep

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

FAULT_NAMES = ("RTD high threshold", "RTD low threshold", "REFIN- > 0.85 x VBIAS",
               "REFIN- < 0.85 x VBIAS (FORCE- open)", "RTDIN- < 0.85 x VBIAS (FORCE- open)",
               "Over/under voltage")

print("SPI devices:", glob.glob("/dev/spidev0.*") or "none - SPI is not enabled")
print(f"Settings: CS=GPIO{config.TEMP_CS_PIN}, RTD_NOMINAL={config.RTD_NOMINAL}, "
      f"REF={config.RTD_REF_RESISTOR}, WIRES={config.RTD_WIRES}")

import adafruit_max31865
import board
import busio
import digitalio

spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
cs = digitalio.DigitalInOut(getattr(board, f"D{config.TEMP_CS_PIN}"))
chip = adafruit_max31865.MAX31865(spi, cs, rtd_nominal=config.RTD_NOMINAL,
                                  ref_resistor=config.RTD_REF_RESISTOR, wires=config.RTD_WIRES)

config_register = chip._read_u8(0x00)
print(f"Config register: 0x{config_register:02X}", end="  ")
if config_register in (0x00, 0xFF):
    print("<- no SPI communication: check SCK/MOSI/MISO/CS wiring and 3.3 V/GND to the board")
else:
    print("(chip is answering)")

for _ in range(5):
    raw = chip.read_rtd()
    faults = [name for name, active in zip(FAULT_NAMES, chip.fault) if active]
    print(f"raw={raw:5d}  resistance={chip.resistance:7.2f} ohm  "
          f"temperature={chip.temperature:8.2f} C  faults={faults or 'none'}")
    chip.clear_faults()
    sleep(0.5)

print("\nHow to read this:")
print(" ~100-120 ohm and 0-50 C with no faults: the sensor works.")
print(f" ~{config.RTD_REF_RESISTOR:g} ohm / ~988 C or FORCE- open faults: the PT100 is disconnected or a wire is open.")
print(" ~0 ohm / about -242 C: the PT100 leads are shorted.")
print(" Odd but steady values: check RTD_WIRES and the 2/3/4-wire jumpers on the MAX31865 board.")
cs.deinit()
spi.deinit()
