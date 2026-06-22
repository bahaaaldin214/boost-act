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
        self.INTDIR = intdir.rstrip("/") + "/"
        self.OBSDIR = obsdir.rstrip("/") + "/"
        # Resolve per-project output roots; explicit output_dir overrides both.
        int_out = output_dir or int_out_dir or intdir
        obs_out = output_dir or obs_out_dir or obsdir
        self.INT_OUT_DIR = int_out.rstrip("/") + "/"
        self.OBS_OUT_DIR = obs_out.rstrip("/") + "/"
        self.DERIVATIVES = "derivatives/GGIR-3.2.6/"  # Defined within the class
        self.system = system

    def run_gg(self):
        """
        Run GGIR for both the internal and observational project directories.
        After each GGIR run, invoke the QC pipeline for that project.
        """
        # Assume QC is available at this import path
        from act.utils.qc import QC

        # Tabular logging data
        report = []

        project_outputs = {
            self.INTDIR: self.INT_OUT_DIR,
            self.OBSDIR: self.OBS_OUT_DIR,
        }

        for project_dir in [self.INTDIR, self.OBSDIR]:
            output_dir = project_outputs[project_dir]
            # Construct command with new I/O flags
            command = f"Rscript act/core/acc_new.R --input_dir {project_dir}"
            command += f" --output_dir {output_dir}"
            command += f" --deriv_dir {self.DERIVATIVES}"

            project_type = "int" if project_dir.rstrip("/") == self.INTDIR.rstrip("/") else "obs"
            status = "SUCCESS"
            details = "GGIR and QC complete"

            try:
                # Execute the command in a new subprocess
                logger.info("Running GGIR for %s project (Dir: %s)", project_type, project_dir)
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                    universal_newlines=True,
                )

                # Stream output line-by-line
                for line in process.stdout:
                    logger.info(line.rstrip())

                process.stdout.close()
                process.wait()

                if process.returncode != 0:
                    raise subprocess.CalledProcessError(process.returncode, command)

                logger.info("GGIR completed successfully for %s project.", project_type)

                # Run QC for this project
                logger.info("Starting QC pipeline for %s project.", project_type)
                qc_runner = QC(project_type, system=self.system)
                qc_runner.qc()
                logger.info("QC pipeline finished for %s project.", project_type)

            except subprocess.CalledProcessError as e:
                status = "FAILED"
                details = f"GGIR process exit {e.returncode}"
                logger.exception("Error running GGIR for %s", project_dir)
            except Exception as e:
                status = "ERROR"
                details = str(e)
                logger.exception("Unexpected error when processing %s", project_dir)

            report.append({
                "Project": project_type,
                "Input": project_dir,
                "Status": status,
                "Details": details
            })

        # Print Tabular Report (similiar to Globus transfer)
        print("\n" + "="*80)
        print(f"{'Project':<10} | {'Status':<10} | {'Details'}")
        print("-" * 80)
        for r in report:
            print(f"{r['Project']:<10} | {r['Status']:<10} | {r['Details']}")
        print("="*80 + "\n")
