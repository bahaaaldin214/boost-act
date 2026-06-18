import subprocess
import logging

logger = logging.getLogger(__name__)


class GG:
    """
    Class to execute GGIR processing for matched subject records.
    """

    def __init__(self, matched, intdir, obsdir, system, output_dir=None):
        """
        Initialize the GG instance.

        Args:
            matched (dict): Mapping of subject IDs to their records.
            intdir (str): Path to the internal directory (Input).
            obsdir (str): Path to the observational directory (Input).
            system (str): Active system profile.
            output_dir (str, optional): Path to the output directory. Defaults to None (uses project_dir).
        """
        self.matched = matched
        self.INTDIR = intdir.rstrip("/") + "/"
        self.OBSDIR = obsdir.rstrip("/") + "/"
        self.OUTPUT_DIR = output_dir.rstrip("/") + "/" if output_dir else None
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

        for project_dir in [self.INTDIR, self.OBSDIR]:
            # Construct command with new I/O flags
            command = f"Rscript act/core/acc_new.R --input_dir {project_dir}"
            if self.OUTPUT_DIR:
                command += f" --output_dir {self.OUTPUT_DIR}"
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
