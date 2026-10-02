# Den sköter all datahantering. 
# Run ID
# Verklig tid
# Elapsed time
# Temperature
# Target temperature
# Heater output
# setting JSON  
from datetime import datetime, timedelta
from pathlib import Path
import json

from openpyxl import Workbook, load_workbook


class DataManager:

    MASTER_LOG_SHEET = "Oven Log"
    MASTER_INFO_SHEET = "Run Info"
    ERROR_SHEET = "Errors"

    def __init__(self, master_file):

        self.master_file = Path(master_file)

        self.master_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        self._create_master_file()


    # =====================================================
    # RUN ID
    # =====================================================

    @staticmethod
    def new_run_id():

        return datetime.now().strftime(
            "%Y%m%d-%H%M%S-%f"
        )


    # =====================================================
    # CREATE MASTER EXCEL
    # =====================================================

    def _create_master_file(self):

        if self.master_file.exists():

            workbook = load_workbook(
                self.master_file
            )

        else:

            workbook = Workbook()

            sheet = workbook.active
            sheet.title = self.MASTER_LOG_SHEET


        # -------------------------------------------------
        # OVEN LOG
        # -------------------------------------------------

        if self.MASTER_LOG_SHEET not in workbook.sheetnames:

            sheet = workbook.create_sheet(
                self.MASTER_LOG_SHEET
            )

        else:

            sheet = workbook[
                self.MASTER_LOG_SHEET
            ]


        if sheet["A1"].value is None:

            sheet.append([
                "Run ID",
                "Time",
                "Elapsed (s)",
                "Temperature (°C)",
                "Target (°C)",
                "Heater output (%)"
            ])


        # -------------------------------------------------
        # RUN INFO
        # -------------------------------------------------

        if self.MASTER_INFO_SHEET not in workbook.sheetnames:

            info_sheet = workbook.create_sheet(
                self.MASTER_INFO_SHEET
            )

            info_sheet.append([
                "Run ID",
                "Start time",
                "Status",
                "Target (°C)",
                "Hold (s)",
                "Kp",
                "Ki",
                "Kd"
            ])


        # -------------------------------------------------
        # ERRORS
        # -------------------------------------------------

        if self.ERROR_SHEET not in workbook.sheetnames:

            error_sheet = workbook.create_sheet(
                self.ERROR_SHEET
            )

            error_sheet.append([
                "Time",
                "Run ID",
                "Elapsed (s)",
                "Temperature (°C)",
                "Error code",
                "Message"
            ])


        workbook.save(
            self.master_file
        )


    # =====================================================
    # CHECK RUN DATA
    # =====================================================

    @staticmethod
    def _prepare_run_data(
        times,
        temperatures,
        targets,
        heater_outputs
    ):

        times = list(times)
        temperatures = list(temperatures)
        targets = list(targets)
        heater_outputs = list(heater_outputs)


        if not times:

            raise ValueError(
                "There is no run data."
            )


        if not (
            len(times)
            == len(temperatures)
            == len(targets)
            == len(heater_outputs)
        ):

            raise ValueError(
                "Run data does not have equal lengths."
            )


        return (
            times,
            temperatures,
            targets,
            heater_outputs
        )


    # =====================================================
    # SAVE RUN TO MASTER FILE
    # =====================================================

    def save_run_to_master(
        self,
        run_id,
        times,
        temperatures,
        targets,
        heater_outputs,
        settings,
        status
    ):

        (
            times,
            temperatures,
            targets,
            heater_outputs
        ) = self._prepare_run_data(
            times,
            temperatures,
            targets,
            heater_outputs
        )


        workbook = load_workbook(
            self.master_file
        )


        log_sheet = workbook[
            self.MASTER_LOG_SHEET
        ]


        info_sheet = workbook[
            self.MASTER_INFO_SHEET
        ]


        end_time = datetime.now()

        total_elapsed = float(
            times[-1]
        )

        start_time = (
            end_time
            - timedelta(
                seconds=total_elapsed
            )
        )


        # Measurement data

        for (
            elapsed,
            temperature,
            target,
            output
        ) in zip(
            times,
            temperatures,
            targets,
            heater_outputs
        ):

            measurement_time = (
                start_time
                + timedelta(
                    seconds=float(elapsed)
                )
            )


            log_sheet.append([
                run_id,

                measurement_time.strftime(
                    "%Y-%m-%d %H:%M:%S.%f"
                )[:-3],

                round(float(elapsed), 2),

                round(float(temperature), 2),

                round(float(target), 2),

                round(float(output), 1)
            ])


        # PID + run information

        info_sheet.append([
            run_id,

            start_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

            status,

            settings[
                "target_temperature_c"
            ],

            settings[
                "hold_duration_s"
            ],

            settings["kp"],

            settings["ki"],

            settings["kd"]
        ])


        workbook.save(
            self.master_file
        )


    # =====================================================
    # EXPORT ONE RUN
    # =====================================================

    def export_run(
        self,
        path,
        run_id,
        times,
        temperatures,
        targets,
        heater_outputs,
        settings,
        status
    ):

        (
            times,
            temperatures,
            targets,
            heater_outputs
        ) = self._prepare_run_data(
            times,
            temperatures,
            targets,
            heater_outputs
        )


        path = Path(path)

        if path.suffix.lower() != ".xlsx":

            path = path.with_suffix(
                ".xlsx"
            )


        workbook = Workbook()


        # =================================================
        # SHEET 1: GRAPH DATA
        # =================================================

        data_sheet = workbook.active

        data_sheet.title = "Run Data"


        data_sheet.append([
            "Elapsed (s)",
            "Temperature (°C)",
            "Target (°C)",
            "Heater output (%)"
        ])


        for (
            elapsed,
            temperature,
            target,
            output
        ) in zip(
            times,
            temperatures,
            targets,
            heater_outputs
        ):

            data_sheet.append([
                round(float(elapsed), 2),
                round(float(temperature), 2),
                round(float(target), 2),
                round(float(output), 1)
            ])


        # =================================================
        # SHEET 2: PID + SETTINGS
        # =================================================

        info_sheet = workbook.create_sheet(
            "Run Info"
        )


        info_sheet.append([
            "Setting",
            "Value"
        ])


        info_sheet.append([
            "Run ID",
            run_id
        ])


        info_sheet.append([
            "Exported at",
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ])


        info_sheet.append([
            "Status",
            status
        ])


        info_sheet.append([
            "Target temperature",
            settings[
                "target_temperature_c"
            ]
        ])


        info_sheet.append([
            "Hold duration",
            settings[
                "hold_duration_s"
            ]
        ])


        info_sheet.append([
            "Kp",
            settings["kp"]
        ])


        info_sheet.append([
            "Ki",
            settings["ki"]
        ])


        info_sheet.append([
            "Kd",
            settings["kd"]
        ])


        workbook.save(
            path
        )


    # =====================================================
    # IMPORT OLD RUN
    # =====================================================

    def import_run(self, path):

        workbook = load_workbook(
            path,
            data_only=True
        )


        if "Run Data" not in workbook.sheetnames:

            raise ValueError(
                "Run Data sheet was not found."
            )


        if "Run Info" not in workbook.sheetnames:

            raise ValueError(
                "Run Info sheet was not found."
            )


        data_sheet = workbook[
            "Run Data"
        ]


        times = []
        temperatures = []
        targets = []
        heater_outputs = []


        for row in data_sheet.iter_rows(
            min_row=2,
            values_only=True
        ):

            if not row or all(
                value is None
                for value in row
            ):
                continue


            times.append(
                float(row[0])
            )

            temperatures.append(
                float(row[1])
            )

            targets.append(
                float(row[2])
            )

            heater_outputs.append(
                float(row[3])
            )


        if not times:

            raise ValueError(
                "The imported run contains no data."
            )


        # -------------------------------------------------
        # PID INFORMATION
        # -------------------------------------------------

        info_sheet = workbook[
            "Run Info"
        ]


        information = {}


        for row in info_sheet.iter_rows(
            min_row=2,
            values_only=True
        ):

            key = row[0]
            value = row[1]


            if key is not None:

                information[
                    str(key)
                ] = value


        required = [
            "Target temperature",
            "Hold duration",
            "Kp",
            "Ki",
            "Kd"
        ]


        for key in required:

            if key not in information:

                raise ValueError(
                    f"Missing value: {key}"
                )


        return {

            "run_id":
                str(
                    information.get(
                        "Run ID",
                        "Imported"
                    )
                ),

            "times":
                times,

            "temperatures":
                temperatures,

            "targets":
                targets,

            "heater_outputs":
                heater_outputs,

            "target_temperature_c":
                float(
                    information[
                        "Target temperature"
                    ]
                ),

            "hold_duration_s":
                float(
                    information[
                        "Hold duration"
                    ]
                ),

            "kp":
                float(
                    information["Kp"]
                ),

            "ki":
                float(
                    information["Ki"]
                ),

            "kd":
                float(
                    information["Kd"]
                ),

            "status":
                str(
                    information.get(
                        "Status",
                        "Imported"
                    )
                )
        }


    # =====================================================
    # ERROR LOG
    # =====================================================

    def log_error(
        self,
        run_id,
        elapsed,
        temperature,
        error_code,
        message
    ):

        workbook = load_workbook(
            self.master_file
        )


        sheet = workbook[
            self.ERROR_SHEET
        ]


        sheet.append([
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

            run_id,

            round(float(elapsed), 2),

            round(float(temperature), 2),

            error_code,

            message
        ])


        workbook.save(
            self.master_file
        )


    # =====================================================
    # JSON SETTINGS
    # =====================================================

    @staticmethod
    def export_settings(
        path,
        settings
    ):

        path = Path(path)

        if path.suffix.lower() != ".json":

            path = path.with_suffix(
                ".json"
            )


        path.write_text(
            json.dumps(
                settings,
                indent=2
            ),
            encoding="utf-8"
        )


    @staticmethod
    def import_settings(path):

        return json.loads(
            Path(path).read_text(
                encoding="utf-8"
            )
        )
