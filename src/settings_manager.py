"""Import and export kiln settings as CSV."""

from __future__ import annotations

import csv
from pathlib import Path

import config


class SettingsManager:
    """Handles import and export of kiln settings."""

    REQUIRED_KEYS = (
        "target_temperature_c",
        "hold_duration_s",
        "kp",
        "ki",
        "kd",
    )


    @staticmethod
    def validate(settings):
        """Check that imported settings are valid."""

        if not isinstance(settings, dict):
            raise ValueError("Settings must be a dictionary.")

        cleaned = {}

        for key in SettingsManager.REQUIRED_KEYS:
            if key not in settings:
                raise ValueError(f"Missing setting: {key}")
            try:
                cleaned[key] = float(settings[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Setting {key} must be numeric.") from exc

        target = cleaned["target_temperature_c"]
        hold = cleaned["hold_duration_s"]
        kp = cleaned["kp"]
        ki = cleaned["ki"]
        kd = cleaned["kd"]


        # Target temperature
        if not (
            config.MIN_SETPOINT
            <= target
            <= config.MAX_SETPOINT
        ):

            raise ValueError(
                f"Target temperature must be between "
                f"{config.MIN_SETPOINT} and "
                f"{config.MAX_SETPOINT} °C."
            )


        # Hold time
        if hold <= 0:

            raise ValueError(
                "Hold duration must be greater than 0."
            )


        # PID gains
        if kp < 0 or ki < 0 or kd < 0:

            raise ValueError(
                "PID gains cannot be negative."
            )


        return {
            "target_temperature_c": target,
            "hold_duration_s": hold,
            "kp": kp,
            "ki": ki,
            "kd": kd,
        }


    @staticmethod
    def export_settings(
        settings,
        file_path
    ):
        """
        Export settings to a CSV file.

        Returns the created file path.
        """

        settings = SettingsManager.validate(
            settings
        )


        file_path = Path(
            file_path
        )


        if file_path.suffix.lower() != ".csv":

            file_path = file_path.with_suffix(
                ".csv"
            )


        file_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )


        with file_path.open(
            "w",
            newline="",
            encoding="utf-8"
        ) as file:

            writer = csv.DictWriter(
                file,
                fieldnames=list(
                    SettingsManager.REQUIRED_KEYS
                )
            )
            writer.writeheader()
            writer.writerow(settings)


        return file_path


    @staticmethod
    def import_settings(
        file_path
    ):
        """
        Import settings from a CSV file.

        Returns a validated settings dictionary.
        """

        file_path = Path(
            file_path
        )


        if not file_path.exists():

            raise FileNotFoundError(
                f"Settings file does not exist: {file_path}"
            )


        with file_path.open(
            "r",
            newline="",
            encoding="utf-8"
        ) as file:

            reader = csv.DictReader(file)
            rows = list(reader)

        if len(rows) != 1:
            raise ValueError("Settings CSV must contain exactly one data row.")

        settings = rows[0]

        return SettingsManager.validate(
            {
                key: float(settings[key])
                for key in SettingsManager.REQUIRED_KEYS
            }
        )