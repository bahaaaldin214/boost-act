import os
import subprocess
import logging

logger = logging.getLogger(__name__)


class GG:
    """
    Class to execute GGIR processing for matched subject records.
    """

    def __init__(
        self,
        matched,
        intdir,
        obsdir,
        system,
        int_out_dir=None,
        obs_out_dir=None,
        output_dir=None,
    ):
        """
        Initialize the GG instance.

        Args:
            matched (dict): Mapping of subject IDs to their records.
            intdir (str): Path to the intervention input directory.
            obsdir (str): Path to the observational input directory.
            system (str): Active system profile.
            int_out_dir (str, optional): Output root for intervention derivatives.
                Defaults to intdir (legacy: derivatives under input).
            obs_out_dir (str, optional): Output root for observational derivatives.
                Defaults to obsdir.
            output_dir (str, optional): Legacy single override for both projects.
        """
        self.matched = matched
        self.INTDIR = intdir.rstrip("/") + "/" if intdir else ""
        self.OBSDIR = obsdir.rstrip("/") + "/" if obsdir else ""
        # Resolve per-project output roots; explicit output_dir overrides both.
        int_out = output_dir or int_out_dir or intdir or ""
        obs_out = output_dir or obs_out_dir or obsdir or ""
        self.INT_OUT_DIR = int_out.rstrip("/") + "/" if int_out else ""
        self.OBS_OUT_DIR = obs_out.rstrip("/") + "/" if obs_out else ""
        self.DERIVATIVES = "derivatives/GGIR-3.2.6/"  # Defined within the class
        self.system = system

    def _project_jobs(self):
        jobs = []
        seen = set()
        for project_type, input_dir, output_dir in (
            ("int", self.INTDIR, self.INT_OUT_DIR),
            ("obs", self.OBSDIR, self.OBS_OUT_DIR),
        ):
            if not input_dir or not os.path.isdir(input_dir.rstrip("/")):
                continue
            key = (input_dir.rstrip("/"), output_dir.rstrip("/"))
            if key in seen:
                continue
            seen.add(key)
            jobs.append(
                {
                    "project_type": project_type,
                    "input_dir": input_dir.rstrip("/"),
                    "output_dir": output_dir.rstrip("/"),
                }
            )
        study_filter = (os.environ.get("GGIR_STUDY") or "").strip().lower()
        if study_filter in {"obs", "int"}:
            jobs = [job for job in jobs if job["project_type"] == study_filter]
        return jobs

    def _ggir_env(self, input_dir: str) -> dict:
        env = os.environ.copy()
        if self.system == "extend" or "BikeExtend" in input_dir:
            env["GGIR_LAYOUT"] = "extend"
            env.setdefault("GGIR_USE_SLEEP_LOG", "TRUE")
        else:
            env.setdefault("GGIR_LAYOUT", "boost")
        return env

    def run_gg(self):
        """
        Run GGIR for configured project directories.
        After each GGIR run, invoke the QC pipeline for BOOST profiles only.
        """
        from act.utils.qc import QC

        report = []
        jobs = self._project_jobs()
        if not jobs:
            raise FileNotFoundError("No configured GGIR input directories exist.")

        for job in jobs:
            project_type = job["project_type"]
            project_dir = job["input_dir"]
            output_dir = job["output_dir"]
            command = (
                f"Rscript act/core/acc_new.R --input_dir {project_dir}"
                f" --output_dir {output_dir} --deriv_dir {self.DERIVATIVES}"
            )
            status = "SUCCESS"
            details = "GGIR complete"
            run_qc = self.system != "extend"

            try:
                logger.info(
                    "Running GGIR for %s project (Dir: %s)",
                    project_type,
                    project_dir,
                )
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                    universal_newlines=True,
                    env=self._ggir_env(project_dir),
                )

                for line in process.stdout:
                    logger.info(line.rstrip())

                process.stdout.close()
                process.wait()

                if process.returncode != 0:
                    raise subprocess.CalledProcessError(process.returncode, command)

                logger.info("GGIR completed successfully for %s project.", project_type)

                if run_qc:
                    logger.info("Starting QC pipeline for %s project.", project_type)
                    qc_runner = QC(project_type, system=self.system)
                    qc_runner.qc()
                    logger.info("QC pipeline finished for %s project.", project_type)
                    details = "GGIR and QC complete"
                else:
                    logger.info(
                        "Skipping BOOST QC plots for %s profile (EXTEND layout).",
                        self.system,
                    )

            except subprocess.CalledProcessError:
                status = "FAILED"
                details = "GGIR process exit non-zero"
                logger.exception("Error running GGIR for %s", project_dir)
            except Exception as exc:
                status = "ERROR"
                details = str(exc)
                logger.exception("Unexpected error when processing %s", project_dir)

            report.append(
                {
                    "Project": project_type,
                    "Input": project_dir,
                    "Status": status,
                    "Details": details,
                }
            )

        print("\n" + "=" * 80)
        print(f"{'Project':<10} | {'Status':<10} | {'Details'}")
        print("-" * 80)
        for row in report:
            print(f"{row['Project']:<10} | {row['Status']:<10} | {row['Details']}")
        print("=" * 80 + "\n")
