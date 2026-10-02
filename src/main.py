"""Application entry point and oven-controller driver skeleton.

Hardware-specific functions such as ``read_temperature`` and heater output
control are intentionally left for the platform adapter to provide.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from enum import IntEnum

from gui import main as run_gui


# Keep these defaults aligned with the GUI controls in gui.py.
DEFAULT_TARGET_TEMPERATURE_C = 45.0
DEFAULT_HOLD_DURATION_S = 60.0
DEFAULT_KP = 0.10
DEFAULT_KI = 0.006
DEFAULT_KD = 0.05
SAMPLE_INTERVAL_S = 0.5
# Set True only after sensor, PID, and hardware safety hooks are connected.
HARDWARE_DRIVER_READY = False


class ProcessStage(IntEnum):
    """Stage indices expected by the GUI StageProgress widget."""

    READY = 0
    WARMING_UP = 1
    HOLDING = 2
    COOLING_DOWN = 3


@dataclass
class OvenState:
    """Mutable process data shared by the controller and GUI."""

    running: bool = False
    stage: ProcessStage = ProcessStage.READY
    progress_fraction: float = 0.0
    progress_detail: str = ""
    status_text: str = "Ready to run"
    elapsed_s: float = 0.0
    temperature_c: float | None = None
    heater_output_percent: float = 0.0
    door_open: bool | None = None
    times_s: list[float] = field(default_factory=list)
    temperatures_c: list[float] = field(default_factory=list)
    heater_outputs_percent: list[float] = field(default_factory=list)
    target_temperature_c: float = DEFAULT_TARGET_TEMPERATURE_C
    hold_duration_s: float = DEFAULT_HOLD_DURATION_S
    k_p: float = DEFAULT_KP
    k_i: float = DEFAULT_KI
    k_d: float = DEFAULT_KD
    integral_error: float = 0.0
    previous_error: float | None = None
    previous_sample_time_s: float | None = None


state = OvenState()


def temperature_reading_loop() -> None:
    """Skeleton for periodic sampling and GUI-facing state updates.

    Provide ``read_temperature`` and the hardware output methods in the
    Raspberry Pi hardware adapter before enabling this loop. Keep blocking
    sensor I/O and control work off the Qt UI thread.
    """
    start_time = time.monotonic()
    state.running = True

    while state.running:
        sample_time_s = time.monotonic() - start_time

        # Hardware integration point: read_temperature() is intentionally
        # not implemented in this entry point.
        # temperature_c = read_temperature()
        temperature_c = None

        if temperature_c is not None:
            state.elapsed_s = sample_time_s
            state.temperature_c = temperature_c

            # Run PID only while actively heating or holding the target.
            # The implementation belongs in the hardware/control layer.
            # if state.stage in (ProcessStage.WARMING_UP, ProcessStage.HOLDING):
            #     state.heater_output_percent = calculate_pid(
            #         target_temperature_c=state.target_temperature_c,
            #         measured_temperature_c=temperature_c,
            #         dt_s=SAMPLE_INTERVAL_S,
            #         k_p=state.k_p,
            #         k_i=state.k_i,
            #         k_d=state.k_d,
            #     )
            # else:
            #     state.heater_output_percent = 0.0

            state.times_s.append(sample_time_s)
            state.temperatures_c.append(temperature_c)
            state.heater_outputs_percent.append(state.heater_output_percent)

        # TODO: update warm-up/hold/cool-down stage, progress_fraction,
        # progress_detail, PID state, heater_output_percent, and status_text.
        time.sleep(SAMPLE_INTERVAL_S)


def main() -> None:
    """Launch the GUI and, when ready, the background hardware loop."""
    controller_thread: threading.Thread | None = None
    if HARDWARE_DRIVER_READY:
        controller_thread = threading.Thread(
            target=temperature_reading_loop,
            name="oven-temperature-loop",
            daemon=True,
        )
        controller_thread.start()

    try:
        run_gui()
    finally:
        # Ask the worker to stop when the GUI exits. The loop checks this
        # flag once per sampling interval.
        state.running = False
        if controller_thread is not None:
            controller_thread.join(timeout=SAMPLE_INTERVAL_S + 1.0)


if __name__ == "__main__":
    main()