import os
import glob
import json
import argparse
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

# TODO
# - look at the large comment sections and implement changes


# TODO
# - change the summary plots to use summaries by session as they come in?
# - change the indices for daily plots to have date as index - not day number


class ACT_PLOTS:

    def __init__(self, sub, ses, person, day, out_dir=None):
        self.df_person = pd.read_csv(person)
        self.df_day = pd.read_csv(day)
        self.sub = str(sub).split("-")[1]
        self.ses = str(ses).split("-")[1]
        self.out_dir = out_dir
        self.create_paths()

    def create_paths(self):
        # Explicit override: write plots straight into the given directory
        if self.out_dir is not None:
            self.path = self.out_dir
            os.makedirs(self.path, exist_ok=True)
            return None

        if str(self.sub).startswith("9"):
            proj = "int"
            site = "NE"
            path = os.path.join("plots", proj, site, str(self.sub))
        elif str(self.sub).startswith("8"):
            proj = "int"
            site = "UI"
            path = os.path.join("plots", proj, site, str(self.sub))
        elif str(self.sub).startswith("7"):
            proj = "obs"
            site = "UI"
            path = os.path.join("plots", proj, site, str(self.sub))
        else:
            print("stupid NE and their dumb tests god...")
            proj = "junk"
            path = os.path.join("plots", "junk", str(self.sub))

        self.path = path
        os.makedirs(self.path, exist_ok=True)

        return None

    """
    below plot needs to change to be multiple bar graphs (one for each session)
    this should now be calculated by session iterating through the session folder
    logic for create_paths needs to change for this -> should be a list of person files by session
    """

    def summary_plot(
        self, act_cycles=["IN", "LIG", "MOD", "VIG"], sleep_col="dur_spt_min_pla"
    ):
        """
        Plots a horizontal stacked bar of daily activity composition:
        Sleep, Inactivity, Light activity, MVPA, and Unidentified time (if any).
        """
        # Collect durations
        durations = {
            cycle: self.df_person[f"dur_day_total_{cycle}_min_pla"].iloc[0]
            for cycle in act_cycles
        }
        mvpa = durations.pop("MOD") + durations.pop("VIG")
        durations = {
            "Sleep": self.df_person[sleep_col].iloc[0],
            "Inactivity": durations["IN"],
            "Light": durations["LIG"],
            "MVPA": mvpa,
        }
        total_minutes = 24 * 60
        identified = sum(durations.values())
        unidentified = total_minutes - identified
        if unidentified > 0:
            durations["Unidentified"] = unidentified

        # Order of segments
        segments = ["Sleep", "Inactivity", "Light", "MVPA"]
        if "Unidentified" in durations:
            segments.append("Unidentified")
        values = [durations[s] for s in segments]

        # Styling
        sns.set_theme(style="whitegrid", rc={"axes.facecolor": "white"})
        fig, ax = plt.subplots(figsize=(10, 3))
        palette = sns.color_palette("pastel", n_colors=len(segments))
        y_max = max(0.6 + 0.3, 0.5)  # max text_y + padding
        ax.set_ylim(-1, y_max)
        ax.set_xlim(
            0, 1440 + 200
        )  # Add padding to the right (or whatever consistent total you want)

        left = 0
        min_width_for_inside_label = 40  # in minutes

        for seg, val, color in zip(segments, values, palette):
            bar_height = 0.4
            ax.barh(
                y=0,
                width=val,
                left=left,
                height=0.5,
                color=color,
                edgecolor="black",
                linewidth=0.8,
            )
            center_x = left + val / 2

            if val >= min_width_for_inside_label:
                text_y = -bar_height / 2 - 0.15
            else:
                text_y = 0.6  # move label above bar

            ax.text(
                center_x,
                text_y,
                seg,
                ha="center",
                va="bottom" if val < min_width_for_inside_label else "top",
                fontsize=10,
                fontweight="bold",
            )
            ax.text(
                center_x,
                text_y + (0.12 if val < min_width_for_inside_label else -0.12),
                f"{val/60:.1f} h",
                ha="center",
                va="bottom" if val < min_width_for_inside_label else "top",
                fontsize=9,
            )

            left += val

        ax.set_xlim(0, total_minutes)
        ax.grid(False)  # ← disables gridlines
        ax.set_yticks([])
        ax.set_xticks([])
        ax.set_title(
            "Average Daily Activity Composition (over the wear period)",
            fontsize=14,
            pad=12,
        )
        ax.spines[["top", "left", "right", "bottom"]].set_visible(False)
        plt.tight_layout()
        plt.savefig(os.path.join(self.path, "summary_plot.png"), bbox_inches="tight")
        plt.close()
        return None

    """
    new logic needs to create these day plots by combining all sessions
    each new session gets the dashed line
    indices for the y-axis on the graph needs to be dates, not days
    """

    def day_plots(self, sleep_col="dur_spt_sleep_min", dates_col="calendar_date"):
        """
        Plots a horizontal stacked bar of daily activity composition:
        Sleep, Inactivity, Light, MVPA, and Unidentified time.
        Also places the duration (in hours) beneath each segment.
        Expects columns:
        dur_day_total_IN_min, dur_day_total_LIG_min,
        dur_day_total_MOD_min, dur_day_total_VIG_min, dur_spt_min
        and a 'Day' column for labeling.
        """
        df_day = self.df_day

        # Define colors
        colors = {
            "Sleep": "#782D73",  # Soft blue
            "Inactivity": "#E57A44",  # Neutral light gray
            "Light": "#95D075",  # Muted green
            "MVPA": "#7A89C2",  # Soft coral red
            "Unidentified": "#B0B0B0",  # Medium gray
        }

        sns.set_theme(style="white")
        total_minutes = 24 * 60
        bar_height = 0.4

        fig, ax = plt.subplots(figsize=(10, 3 + 0.3 * len(df_day)))

        # --- 1) Compute custom y-positions with extra space between sessions ---
        # extract session numbers from filename; fall back to the session passed
        # in when the GGIR filename carries no ses-N token (e.g. raw inputs that
        # were not renamed to sub-####_ses-#_accel by the pipeline)
        fallback_ses = "".join(ch for ch in str(self.ses) if ch.isdigit()) or "1"
        session_nums = (
            df_day["filename"]
            .str.extract(r"ses-(\d+)")[0]
            .fillna(fallback_ses)
            .astype(int)
        )
        default_space = 1.9
        extra_space = 0.8
        y_positions = []
        boundary_ys = []
        current_y = 0.0

        for i, sess in enumerate(session_nums):
            if i == 0:
                # first row
                y_positions.append(current_y)
            else:
                if sess != session_nums.iat[i - 1]:
                    # session changed → add extra_space
                    current_y += default_space + extra_space
                    # record midpoint for dotted line
                    boundary = current_y - (default_space + extra_space) / 2
                    boundary_ys.append(boundary)
                else:
                    # same session → normal spacing
                    current_y += default_space
                y_positions.append(current_y)

        # --- 2) Plot each day's stacked bar at its computed y-position ---
        min_width_for_inside_label = 45  # minutes
        for i, (idx, row) in enumerate(df_day.iterrows()):
            y_val = y_positions[i]
            # build durations
            durations = {
                "Sleep": row[sleep_col],
                "Inactivity": row["dur_day_total_IN_min"],
                "Light": row["dur_day_total_LIG_min"],
                "MVPA": row["dur_day_total_MOD_min"] + row["dur_day_total_VIG_min"],
            }
            identified = sum(durations.values())
            unidentified = total_minutes - identified
            if unidentified > 0:
                durations["Unidentified"] = unidentified

            left = 0.0
            above_toggle = False
            for cat, val in durations.items():
                ax.barh(
                    y=y_val,
                    width=val,
                    left=left,
                    height=bar_height,
                    color=colors[cat],
                    edgecolor="none",
                )
                center_x = left + val / 2
                # choose label position
                if val >= min_width_for_inside_label:
                    text_y = y_val - bar_height / 2 - 0.1
                    va = "top"
                else:
                    if above_toggle:
                        text_y = y_val + bar_height / 2 + 0.05
                        va = "bottom"
                    else:
                        text_y = y_val - bar_height / 2 - 0.25
                        va = "top"
                    above_toggle = not above_toggle

                if val > 1:
                    ax.text(
                        center_x,
                        text_y,
                        f"{val/60:.1f} h",
                        ha="center",
                        va=va,
                        fontsize=8,
                    )
                left += val

        # Day labels on y-axis
        ax.set_yticks(y_positions)
        date_labels = (
            df_day[dates_col].astype(str).str.replace(r"\s*00:00:00$", "", regex=True)
        )
        ax.set_yticklabels(date_labels)

        # Add dotted lines between sessions
        for b in boundary_ys:
            ax.axhline(y=b, color="black", linestyle="--", linewidth=0.7)

        # Clean up x-axis
        ax.set_xticks([0, total_minutes / 2, total_minutes])
        ax.set_xticklabels(["0 h", "12 h", "24 h"], fontsize=9)
        ax.spines[["top", "right", "left", "bottom"]].set_visible(False)
        ax.tick_params(axis="y", length=0)
        ax.set_xlim(0, total_minutes)

        # Legend
        handles = [plt.Rectangle((0, 0), 1, 1, color=colors[k]) for k in colors]
        ax.legend(
            handles,
            list(colors.keys()),
            bbox_to_anchor=(1.01, 1),
            loc="upper left",
            frameon=False,
        )

        fig.suptitle("Daily Activity Composition", fontsize=14, y=0.95)
        plt.tight_layout()
        plt.savefig(os.path.join(self.path, "daily_plot.png"))
        plt.close()
        return None


def create_json(data_folder, out_file="data.json"):
    """
    Constructs a JSON of PNG files organized by project, site, and subject,
    assuming each subject folder contains exactly two PNG files.
    Only scans 'int' and 'obs' folders at the top level.
    """
    master_data = {}
    projects = ["int", "obs"]

    for project in projects:
        project_path = os.path.join(data_folder, project)
        if not os.path.isdir(project_path):
            continue

        master_data.setdefault(project, {})

        for site in os.listdir(project_path):
            site_path = os.path.join(project_path, site)
            if not os.path.isdir(site_path):
                continue

            master_data[project].setdefault(site, {})

            for subject in os.listdir(site_path):
                subject_path = os.path.join(site_path, subject)
                if not os.path.isdir(subject_path):
                    continue

                png_files = [
                    os.path.join(subject_path, fname).replace("plots/", "data/", 1)
                    for fname in os.listdir(subject_path)
                    if fname.endswith(".png")
                ]
                png_files.sort()

                # Only include if there are exactly 2 PNGs
                if len(png_files) == 2:
                    master_data[project][site][subject] = png_files

    with open(out_file, "w") as f:
        json.dump(master_data, f, indent=2)

    return master_data


def _find_summary(results_dir, kind):
    """Locate a part5 MM summary file ('person' or 'day') in a results dir."""
    matches = sorted(
        glob.glob(os.path.join(results_dir, f"part5_{kind}summary_MM*.csv"))
    )
    if not matches:
        raise FileNotFoundError(
            f"No part5_{kind}summary_MM*.csv found in {results_dir}"
        )
    return matches[0]


def render_from_results(results_dir, out_dir, sub=None, ses="ses-1"):
    """
    Render summary_plot.png and daily_plot.png from a GGIR results directory.

    results_dir : folder containing part5_personsummary_MM*.csv and
                  part5_daysummary_MM*.csv (e.g. .../output_ses-1/results).
    out_dir     : where the two PNGs are written.
    sub         : subject id like 'sub-9002'; inferred from the summary filename
                  when omitted.
    """
    person = _find_summary(results_dir, "person")
    day = _find_summary(results_dir, "day")

    if sub is None:
        first = pd.read_csv(person, nrows=1)
        ident = str(first["ID"].iloc[0]) if "ID" in first.columns else "0000"
        sub = f"sub-{ident}"

    plotter = ACT_PLOTS(sub, ses, person=person, day=day, out_dir=out_dir)
    plotter.summary_plot()
    plotter.day_plots()
    print(f"Wrote plots to {out_dir} (person={os.path.basename(person)}, "
          f"day={os.path.basename(day)})")
    return out_dir


def main():
    parser = argparse.ArgumentParser(
        description="Render GGIR activity-composition plots from a results dir."
    )
    parser.add_argument(
        "results_dir",
        help="GGIR results directory containing part5_*summary_MM*.csv files",
    )
    parser.add_argument(
        "-o",
        "--out",
        default=None,
        help="Output directory for PNGs (default: <results_dir>/plots)",
    )
    parser.add_argument(
        "--sub", default=None, help="Subject id, e.g. sub-9002 (inferred if omitted)"
    )
    parser.add_argument("--ses", default="ses-1", help="Session id (default: ses-1)")
    args = parser.parse_args()

    out_dir = args.out or os.path.join(args.results_dir, "plots")
    render_from_results(args.results_dir, out_dir, sub=args.sub, ses=args.ses)


if __name__ == "__main__":
    main()
