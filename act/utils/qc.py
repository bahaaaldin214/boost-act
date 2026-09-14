import os
import glob
import pandas as pd
from act.utils.pipe import Pipe
from act.utils.plots import ACT_PLOTS, create_json


class QC:
    def __init__(self, project: str, system: str = "vosslnx"):
        """
        Initialize a QC instance.

        Parameters:
        -----------
        project : str
            Either 'obs' for observational study (7 days worn) or 'int' for
            intervention study (9 days worn). Determines expected wear-time.

        Attributes:
        -----------
        n_days_worn : int
            Number of days the device is expected to be worn, based on project.
        base_dir : str
            Full path to the parent “GGIR‐3.2.6‐test” directory containing all subjects.
        csv_path : str
            Path to the master CSV where QC outcomes are logged.
        """

        # Ensure directories are configured for the active system
        Pipe.configure(system)
        self.system = system

        # Determine the expected days worn based on project type. Derivatives are
        # read from the per-project output root (may differ from the input dir).
        if project == "obs":
            self.base_dir = os.path.join(Pipe.OBS_OUT_DIR, "derivatives", "GGIR-3.2.6")
            self.n_days_worn = 7
        elif project == "int":
            self.n_days_worn = 9
            self.base_dir = os.path.join(Pipe.INT_OUT_DIR, "derivatives", "GGIR-3.2.6")
        else:
            raise ValueError("Project must be 'obs' or 'int'")

        # Path to the master CSV that accumulates QC errors/warnings
        self.csv_path = "./act/logs/GGIR_QC_errs.csv"

    def qc(self) -> None:
        """
        Loop through each subject under self.base_dir, locate the three QC files
        (QC report, person summary, day summary), extract metrics, and run all
        QC checks for that subject/session. After processing each subject, delete
        loaded data from memory to free up resources.

        This method updates self.csv_path with pass/error/warning flags for:
          - Calibration error
          - Hours considered
          - Valid days
          - Cleaning code

        Returns:
        --------
        None
        """
        # Ensure base directory exists
        if not os.path.isdir(self.base_dir):
            raise FileNotFoundError(f"Base directory not found: {self.base_dir}")

        # Iterate over all subject directories in base_dir
        for entry in os.listdir(self.base_dir):
            sub_path = os.path.join(self.base_dir, entry)
            if not os.path.isdir(sub_path) or not entry.startswith("sub-"):
                continue

            accel_dir = os.path.join(sub_path, "accel")
            if not os.path.isdir(accel_dir):
                continue

            # Track per-session MM summary files in case aggregated outputs are missing
            session_records = []

            # Iterate over session folders inside the accel directory
            for session_folder in os.listdir(accel_dir):
                if not session_folder.startswith("ses"):
                    continue

                ses_path = os.path.join(accel_dir, session_folder)
                if not os.path.isdir(ses_path):
                    continue

                # Now construct path to results
                results_dir = os.path.join(
                    ses_path, f"output_{session_folder}", "results"
                )  # new design needs to use output_{session_folder} instead of output_accel
                if not os.path.isdir(results_dir):
                    continue

                # 1. QC report is still fixed
                qc_file = os.path.join(results_dir, "QC", "data_quality_report.csv")
                if not os.path.isfile(qc_file):
                    continue

                # 2. Locate the person summary using glob for MM
                person_matches = glob.glob(
                    os.path.join(results_dir, "part5_personsummary_MM*.csv")
                )
                if not person_matches:
                    continue
                person_file = person_matches[0]

                # 3. Locate the day summary using glob for MM
                day_matches = glob.glob(
                    os.path.join(results_dir, "part5_daysummary_MM*.csv")
                )
                if not day_matches:
                    continue
                day_file = day_matches[0]

                # Extract subject/session and metrics
                dfs = [qc_file, person_file, day_file]
                metrics, sub, ses = self.extract_metrics(dfs)
                cal_err, h_considered, valid_days, clean_code_series, calendar_date = (
                    metrics
                )

                # Run QC checks
                self.cal_error_check(cal_err, sub, ses)
                self.h_considered_check(h_considered, sub, ses)
                self.valid_days_check(sub, ses)
                self.cleaning_code_check(clean_code_series, calendar_date, sub, ses)

                # Store session-level MM files for fallback plotting
                session_records.append(
                    {
                        "sub": sub,
                        "ses": ses,
                        "person": person_file,
                        "day": day_file,
                    }
                )

                # Clean up
                try:
                    del metrics, cal_err, h_considered, valid_days, clean_code_series
                    del dfs, person_file, day_file, qc_file
                except UnboundLocalError:
                    pass

            # After per‐session QC, make summary plots using the MM files
            all_ses_dir = os.path.join(sub_path, "accel", "output_accel", "results")
            agg_person = glob.glob(
                os.path.join(all_ses_dir, "part5_personsummary_MM*.csv")
            )
            agg_day = glob.glob(os.path.join(all_ses_dir, "part5_daysummary_MM*.csv"))

            if agg_person and agg_day:
                person = sorted(agg_person)[0]
                day = sorted(agg_day)[0]
                plot_sub = entry
                plot_ses = "ses-agg"
            elif session_records:
                record = session_records[0]
                person = record["person"]
                day = record["day"]
                plot_sub = record["sub"]
                plot_ses = record["ses"]
            else:
                print(f"No person/day summary found for {sub_path}")
                continue

            plotter = ACT_PLOTS(plot_sub, plot_ses, person=person, day=day)
            plotter.summary_plot()
            plotter.day_plots()

        # create the json file used in the application
        create_json("plots")
        # End of qc loop

    def extract_metrics(self, dfs: list) -> tuple:
        """
        Given a list of exactly three file paths [qc_csv, person_csv, day_csv],
        read each into a DataFrame, extract the relevant metrics, and return
        (metrics_list, subject, session).

        Parameters:
        -----------
        dfs : list of str
            dfs[0]: Full path to QC/data_quality_report.csv
            dfs[1]: Full path to part5_personsummary_*.csv
            dfs[2]: Full path to part5_daysummary_*.csv

        Returns:
        --------
        metrics : list
            [calibration_error (float),
             hours_considered (int),
             valid_days (int),
             cleaning_code (pd.Series)]
        sub : str
            Subject identifier, e.g., 'sub-7001'
        ses : str
            Session identifier, e.g., 'ses-1'

        Raises:
        -------
        FileNotFoundError if any of the three paths are invalid.
        """
        qc_path, person_path, day_path = dfs
        for df in dfs:
            if not os.path.isfile(df):
                raise FileNotFoundError(f"File not found: {df}")

        # Read the CSVs into DataFrames
        qc_df = pd.read_csv(qc_path)
        person_df = pd.read_csv(person_path)
        day_df = pd.read_csv(day_path)

        # The QC report’s 'filename' column holds something like 'sub-7001_ses-1_xxx.ext'
        # Split on '_' to get subject and session identifiers
        file_id = qc_df["filename"].iloc[0]
        parts = file_id.split("_")
        sub = parts[0]  # e.g., 'sub-7001'
        print(f"Processing subject: {sub}")
        ses = parts[1]  # e.g., 'ses-1'
        print(f"Processing session: {ses}")

        # Extract metrics from each DataFrame:
        # 1) Calibration error: from qc_df column 'cal.error.end'
        cal_err = qc_df["cal.error.end"].iloc[0]
        # 2) Hours considered: from qc_df column 'n.hours.considered'
        h_considered = qc_df["n.hours.considered"].iloc[0]
        # 3) Valid days: from person summary column 'Nvaliddays'
        valid_days = person_df["Nvaliddays"].iloc[0]
        # 4) Cleaning code series: from day summary column 'cleaningcode'
        clean_code = day_df["cleaningcode"]
        # 5) calendar dates series from day summary column 'calendar_date'
        calendar_date = day_df["calendar_date"]

        metrics = [cal_err, h_considered, valid_days, clean_code, calendar_date]
        self.df_day = day_df
        return metrics, sub, ses

    # ─────────────────────────────────────────────────────────────────────
    # #2: Append/Update Master QC CSV with human-readable messages
    # ─────────────────────────────────────────────────────────────────────

    def create_and_return_csv(
        self, check: str, code: int, sub: str, ses: str, date=None, clean_code=None
    ) -> None:
        """
        Appends or updates a row in self.csv_path for (sub, ses), setting
        the human-readable interpretation for the specified QC check.

        If an expected variable wasn’t set (code = 3), writes a “missing variable”
        message into the CSV for that check.
        """
        # Map internal check names to CSV column names
        name_map = {
            "cal_err": "Calibration_Error",
            "h_considered": "Hours_Considered",
            "clean_code": "Cleaning_Code",
            "val_days": "Valid_Days",
        }
        col = name_map.get(check, check)

        # Human-readable interpretations for each check / response code
        meanings = {
            "Calibration_Error": {
                0: "Pass",
                1: "ERROR: Calibration error too high",
                3: "ERROR: Calibration error missing",
            },
            "Hours_Considered": {
                0: "Pass",
                1: "ERROR: Too few hours considered",
                2: "WARNING: Exceeds expected hours (possible worn-day mismatch)",
                3: "ERROR: Hours considered missing",
            },
            "Cleaning_Code": {
                0: "Pass",
                1: "ERROR: Invalid cleaning code found",
                2: "WARNING: Missing (NaN) cleaning codes present",
                3: "ERROR: Cleaning code missing",
            },
            "Valid_Days": {
                0: "Pass: ≥2 weekend days and ≥3 weekdays",
                1: "ERROR: Fewer than 2 weekend days for at least one session",
                2: "ERROR: Fewer than 3 weekdays for at least one session",
                3: "ERROR: No valid-days data found for at least one session",
            },
        }
        interpretation = meanings[col].get(code, "ERROR: Unknown code")

        # Append the actual cleaning codes and dates if applicable
        if check == "clean_code" and code == 1 and clean_code is not None:
            invalids = clean_code[
                ~clean_code.isin([0, 1]) & clean_code.notna()
            ].unique()
            if len(invalids) > 0:
                interpretation += f" — Invalid codes: {', '.join(map(str, invalids))}"
                if date is not None and len(date) > 0:
                    interpretation += f" on date(s): {', '.join(date)}"

        # Load existing master CSV if it exists; otherwise create a fresh DataFrame
        if os.path.exists(self.csv_path):
            master_df = pd.read_csv(self.csv_path)
        else:
            cols = ["Subject", "Session"] + list(name_map.values())
            master_df = pd.DataFrame(columns=cols)

        # Check if a row for this (sub, ses) already exists
        mask = (master_df["Subject"] == sub) & (master_df["Session"] == ses)
        if mask.any():
            # Overwrite only this column’s value
            master_df.loc[mask, col] = interpretation
        else:
            # Create a new row, with blanks in all QC columns except this one
            new_row = {c: "" for c in master_df.columns}
            new_row["Subject"] = sub
            new_row["Session"] = ses
            new_row[col] = interpretation
            master_df = pd.concat(
                [master_df, pd.DataFrame([new_row])], ignore_index=True
            )

        # Write back to CSV (index=False to avoid an extra column)
        master_df.to_csv(self.csv_path, index=False)

    # ─────────────────────────────────────────────────────────────────────
    # #3: Individual QC Check Methods (append to CSV via create_and_return_csv)
    #      Modified to catch “variable not set” and report code = 3
    # ─────────────────────────────────────────────────────────────────────

    def cal_error_check(
        self, cal_error: float, sub: str, ses: str, threshold: float = 0.03
    ) -> int:
        """
        Check calibration error against a threshold.

        Returns:
        --------
        0 → OK
        1 → Calibration error exceeds threshold
        3 → Calibration error variable was not set
        """
        try:
            # If cal_error is None or NaN, treat as missing
            if cal_error is None or (
                isinstance(cal_error, float) and pd.isna(cal_error)
            ):
                raise ValueError("cal_error is missing")

            if cal_error > threshold:
                self.create_and_return_csv("cal_err", 1, sub, ses)
                return 1
            else:
                self.create_and_return_csv("cal_err", 0, sub, ses)
                return 0

        except Exception:
            # Any exception (NameError, ValueError, etc.) means variable not set
            self.create_and_return_csv("cal_err", 3, sub, ses)
            return 3

    def h_considered_check(
        self, h_considered: int, sub: str, ses: str, tolerance: int = 5
    ) -> int:
        """
        Check if the number of hours considered is within the expected window.

        Returns:
        --------
        0 → OK
        1 → Too few hours considered
        2 → More hours than expected (possible mismatch with n_days_worn)
        3 → Hours considered variable was not set
        """
        try:
            # If h_considered is None or NaN, treat as missing
            if h_considered is None or (
                isinstance(h_considered, (int, float)) and pd.isna(h_considered)
            ):
                raise ValueError("h_considered is missing")

            expected_hours = self.n_days_worn * 24
            if h_considered < (expected_hours - tolerance):
                self.create_and_return_csv("h_considered", 1, sub, ses)
                return 1
            elif h_considered > expected_hours:
                self.create_and_return_csv("h_considered", 2, sub, ses)
                return 2
            else:
                self.create_and_return_csv("h_considered", 0, sub, ses)
                return 0

        except Exception:
            self.create_and_return_csv("h_considered", 3, sub, ses)
            return 3

    def valid_days_check(self, sub: str, ses: str) -> int:
        """
        QC: ensure that for this session (ses) we have at least
        2 weekend days (Sat/Sun) and 3 weekdays (Mon–Fri).

        Returns:
        --------
        0 → OK
        1 → ERROR: fewer than 2 weekend days
        2 → ERROR: fewer than 3 weekdays
        3 → ERROR: session data missing
        """
        try:
            print(f"Valid Days - Checking session {ses} for subject {sub}")

            # select only the rows for this session
            mask = self.df_day["filename"].str.contains(f"{ses}_")
            session_df = self.df_day[mask]

            if session_df.empty:
                # no rows for this session at all
                raise ValueError("No data for session")

            # count weekends vs weekdays
            is_weekend = session_df["weekday"].isin(["Saturday", "Sunday"])
            weekend_count = is_weekend.sum()
            weekday_count = (~is_weekend).sum()

            # QC rules
            if weekend_count < 2:
                self.create_and_return_csv("val_days", 1, sub, ses)
                return 1

            if weekday_count < 3:
                self.create_and_return_csv("val_days", 2, sub, ses)
                return 2

            # all good
            self.create_and_return_csv("val_days", 0, sub, ses)
            return 0

        except Exception as e:
            # anything else counts as “missing”
            print(str(e))
            self.create_and_return_csv("val_days", 3, sub, ses)
            return 3

    def cleaning_code_check(
        self, clean_code: pd.Series, calendar_dates: pd.Series, sub: str, ses: str
    ) -> int:
        """
        Inspect the 'cleaningcode' column from the day summary.

        Returns:
        --------
        0 → All codes valid (0 or 1)
        1 → Found invalid code (not 0 or 1)
        2 → Only NaNs, no invalid codes
        3 → Cleaning code variable was not set
        """
        try:
            if clean_code is None or not isinstance(clean_code, pd.Series):
                raise ValueError("clean_code is missing")

            # If any value outside {0,1} appears and is not NaN → error
            invalid_mask = ~clean_code.isin([0, 1]) & clean_code.notna()
            invalid_codes = clean_code[invalid_mask].unique()

            if len(invalid_codes) > 0:
                invalid_dates = (
                    calendar_dates[invalid_mask].dt.strftime("%Y-%m-%d").unique()
                )
                print(
                    f"Found invalid cleaning codes: {invalid_codes} on dates: {invalid_dates}"
                )
                self.create_and_return_csv(
                    "clean_code",
                    1,
                    sub,
                    ses,
                    clean_code=clean_code,
                    date=invalid_dates,  # Pass the list of dates to include in interpretation
                )
                return 1
            # If all values are NaN → warning (no cleaning codes present)
            elif clean_code.isna().all():
                self.create_and_return_csv(
                    "clean_code",
                    2,
                    sub,
                    ses,
                )
                return 2
            else:
                self.create_and_return_csv("clean_code", 0, sub, ses)
                return 0

        except Exception:
            self.create_and_return_csv("clean_code", 3, sub, ses)
            return 3
