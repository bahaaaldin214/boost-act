import os
import glob
import pandas as pd
import plotly.graph_objects as go
import logging
from act.utils.pipe import Pipe

logger = logging.getLogger(__name__)


class Group:
    def __init__(self, system: str = "vosslnx"):
        Pipe.configure(system)
        self.system = system
        # Derivatives are read from the per-project output roots.
        self.obs_path = Pipe.OBS_OUT_DIR
        self.int_path = Pipe.INT_OUT_DIR
        self.paths = [
            os.path.join(self.obs_path, "derivatives", "GGIR-3.2.6"),
            os.path.join(self.int_path, "derivatives", "GGIR-3.2.6"),
        ]
        self.path = "./plots/group"

    """
    New person logic:
        GGIR with ncp and sleep no longer are outputting the output_accel files for all sessions (may be premature)
        New goal is to iterate through the folders and grab the metrics, then average
    """

    def _parse_person_file(self, file_path):
        try:
            df = pd.read_csv(file_path)
            sleep = df["dur_spt_min_pla"].iloc[0]
            inactivity = df["dur_day_total_IN_min_pla"].iloc[0]
            light = df["dur_day_total_LIG_min_pla"].iloc[0]
            mod = df["dur_day_total_MOD_min_pla"].iloc[0]
            vig = df["dur_day_total_VIG_min_pla"].iloc[0]
            mvpa = mod + vig
            total = sleep + inactivity + light + mvpa
            logger.debug(
                f"Parsed {file_path}: sleep={sleep}, inactivity={inactivity}, light={light}, mvpa={mvpa}, total={total}"
            )
            if total == 0:
                logger.warning(
                    f"Total was 0 for {file_path}. Columns found: {df.columns.tolist()}"
                )
                return None
            if df.empty:
                logger.warning(f"{file_path} is empty.")
                return None
            session = (
                df.get("filename", [""])[0].split("_")[-2]
                if "filename" in df
                else "unknown"
            )
            return {
                "Sleep": sleep / total * 1440,
                "Inactivity": inactivity / total * 1440,
                "Light": light / total * 1440,
                "MVPA": mvpa / total * 1440,
                "Session": session,
            }
        except Exception as e:
            logger.warning(f"Error reading file {file_path}: {e}")
            return None

    def plot_person(self):
        durations = []

        for base_dir in self.paths:
            for entry in sorted(os.listdir(base_dir)):
                if not entry.startswith("sub-"):
                    continue

                results_dir = os.path.join(
                    base_dir, entry, "accel", "output_accel", "results"
                )
                if not os.path.isdir(results_dir):
                    logger.debug(
                        "Results directory missing for %s: %s", entry, results_dir
                    )
                    continue
                pattern = os.path.join(results_dir, "part5_personsummary_MM*.csv")
                matches = glob.glob(pattern)
                if not matches:
                    logger.debug(
                        f"No matching files in {results_dir}, skipping {entry}"
                    )
                    continue

                for person_file in matches:
                    values = self._parse_person_file(person_file)
                    if not values:
                        continue

                    values["Subject"] = entry
                    durations.append(values)

        df_all = pd.DataFrame(durations)
        if not df_all.empty and "Subject" in df_all.columns:
            df_all = df_all[~df_all["Subject"].str.startswith("sub-6")].reset_index(
                drop=True
            )
            df_all = df_all.sort_values("Subject").reset_index(drop=True)
        else:
            logger.warning("No valid data found — skipping Subject filtering.")

        self._plot_stacked_bar(
            df_all,
            title="Normalized Average Activity Composition by Subject (All Sessions)",
            filename="avg_plot_all.html",
        )

    def plot_session(self):
        durations = []

        for base_dir in self.paths:
            for entry in sorted(os.listdir(base_dir)):
                if not entry.startswith("sub-"):
                    continue

                subject_path = os.path.join(base_dir, entry, "accel")
                if not os.path.isdir(subject_path):
                    continue

                for session_folder in sorted(os.listdir(subject_path)):
                    if not session_folder.startswith("ses"):
                        continue

                    results_dir = os.path.join(
                        subject_path,
                        session_folder,
                        f"output_{session_folder}",
                        "results",
                    )
                    if not os.path.isdir(results_dir):
                        logger.debug(
                            "Results directory missing for %s/%s: %s",
                            entry,
                            session_folder,
                            results_dir,
                        )
                        continue
                    pattern = os.path.join(results_dir, "part5_personsummary_MM*.csv")
                    matches = glob.glob(pattern)
                    if not matches:
                        logger.debug(
                            f"No matching files in {results_dir}, skipping {entry}/{session_folder}"
                        )
                        continue

                    for person_file in matches:
                        values = self._parse_person_file(person_file)
                        if not values:
                            continue

                        values["Subject"] = entry
                        values["Session"] = session_folder
                        durations.append(values)

        df_all = pd.DataFrame(durations)
        if not df_all.empty and {"Subject", "Session"}.issubset(df_all.columns):
            df_all = df_all[~df_all["Subject"].str.startswith("sub-6")].reset_index(
                drop=True
            )
            df_all = df_all.sort_values(["Session", "Subject"]).reset_index(drop=True)
        else:
            logger.warning("No valid data found — skipping Subject/Session filtering.")

        for session, group_df in df_all.groupby("Session"):
            self._plot_stacked_bar(
                group_df,
                title=f"Average Activity Composition — Session {session.upper()}",
                y_key="Subject",
                filename=f"avg_plot_{session}.html",
            )

    def _plot_stacked_bar(self, df, title, filename, y_key="Subject"):
        if df.empty:
            logger.warning("No data to plot.")
            return

        activities = ["Sleep", "Inactivity", "Light", "MVPA"]
        colors = {
            "Sleep": "#A8DADC",
            "Inactivity": "#F1FAEE",
            "Light": "#FFD6A5",
            "MVPA": "#FF9F1C",
        }

        fig = go.Figure()
        for activity in activities:
            fig.add_trace(
                go.Bar(
                    y=df[y_key],
                    x=df[activity],
                    name=activity,
                    orientation="h",
                    marker_color=colors[activity],
                    hovertemplate=df[y_key]
                    + f" - {activity} = "
                    + (df[activity] / 60).round(2).astype(str)
                    + " hr<extra></extra>",
                )
            )

        fig.update_layout(
            barmode="stack",
            title=title,
            xaxis=dict(title="Hours (normalized to 24)"),
            yaxis=dict(title=y_key),
            height=30 * len(df),
            margin=dict(l=20, r=20, t=40, b=20),
            showlegend=True,
            autosize=True,
        )
        os.makedirs(self.path, exist_ok=True)
        fig.write_html(os.path.join(self.path, filename))
