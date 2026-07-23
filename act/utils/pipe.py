from act.utils.save import Save
from act.core.gg import GG
import os


class Pipe:
    # class-level "exported" attributes
    INT_DIR: str = ""
    OBS_DIR: str = ""
    RDSS_DIR: str = ""
    # Output roots where GGIR derivatives are written/read. When a profile omits
    # them they default to the matching input dir (legacy behaviour: derivatives
    # live under the input folder). GGIR writes <OUT_DIR>/derivatives/GGIR-3.2.6/.
    INT_OUT_DIR: str = ""
    OBS_OUT_DIR: str = ""

    _SYSTEM_PATHS = {
        "vosslnx": dict(
            INT_DIR="/mnt/nfs/lss/vosslabhpc/Projects/BOOST/InterventionStudy/3-experiment/data/act-int-test",
            OBS_DIR="/mnt/nfs/lss/vosslabhpc/Projects/BOOST/ObservationalStudy/3-experiment/data/act-obs-test",
            RDSS_DIR="/mnt/nfs/rdss/vosslab/Repositories/Accelerometer_Data",
        ),
        "vosslnxft": dict(
            # Intervention input is now the canonical Globus/manual landing zone;
            # derivatives are written to a separate sibling output/ folder.
            INT_DIR="/mnt/nfs/lss/vosslabhpc/Projects/BOOST/InterventionStudy/3-experiment/inputs/act-int-ready",
            INT_OUT_DIR="/mnt/nfs/lss/vosslabhpc/Projects/BOOST/InterventionStudy/3-experiment/output",
            OBS_DIR="/mnt/nfs/lss/vosslabhpc/Projects/BOOST/ObservationalStudy/3-experiment/data/act-obs-final-test-2",
            RDSS_DIR="/mnt/nfs/rdss/vosslab/Repositories/Accelerometer_Data",
        ),
        "local": dict(
            INT_DIR="/mnt/lss/Projects/BOOST/InterventionStudy/3-experiment/data/act-int-final-test-2",
            OBS_DIR="/mnt/lss/Projects/BOOST/ObservationalStudy/3-experiment/data/act-obs-final-test-2",
            RDSS_DIR="/mnt/rdss/VossLab/Repositories/Accelerometer_Data",
        ),
        "argon": dict(
            INT_DIR="/Shared/vosslabhpc/Projects/BOOST/InterventionStudy/3-experiment/data/act-int-test",
            OBS_DIR="/Shared/vosslabhpc/Projects/BOOST/ObservationalStudy/3-experiment/data/act-obs-test",
            RDSS_DIR=None,
        ),
        "extend": dict(
            INT_DIR="/mnt/nfs/lss/vosslabhpc/Projects/BikeExtend/3-Experiment/2-Data/BIDS",
            OBS_DIR="",
            INT_OUT_DIR="/mnt/nfs/lss/vosslabhpc/Projects/BikeExtend/3-Experiment/2-Data/BIDS",
            OBS_OUT_DIR="",
            RDSS_DIR="/mnt/nfs/rdss/vosslab/Repositories/Accelerometer_Data",
        ),
    }

    @classmethod
    def available_systems(cls) -> tuple[str, ...]:
        return tuple(cls._SYSTEM_PATHS.keys())

    @classmethod
    def system_paths(cls, system: str = "vosslnx") -> dict:
        try:
            return cls._SYSTEM_PATHS[system]
        except KeyError as e:
            raise ValueError(f"Unknown system: {system}") from e

    @classmethod
    def configure(cls, system: str = "vosslnx") -> None:
        paths = cls.system_paths(system)
        cls.INT_DIR = paths["INT_DIR"]
        cls.OBS_DIR = paths["OBS_DIR"]
        cls.RDSS_DIR = paths["RDSS_DIR"]
        # Output dirs default to the matching input dir when not specified.
        cls.INT_OUT_DIR = paths.get("INT_OUT_DIR") or paths["INT_DIR"]
        cls.OBS_OUT_DIR = paths.get("OBS_OUT_DIR") or paths["OBS_DIR"]

    def __init__(
        self,
        token,
        daysago,
        system="vosslnx",
        output_dir=None,
        rebuild_manifest_only=False,
        reconcile_manifest_only=False,
        ggir_only=False,
        subject_ids=None,
        study_filter=None,
    ):
        # ensure class attrs are set for everyone (Pipe.INT_DIR etc.)
        type(self).configure(system)
        self.token = token
        self.daysago = daysago
        self.system = system
        self.output_dir = output_dir
        self.rebuild_manifest_only = rebuild_manifest_only
        self.reconcile_manifest_only = reconcile_manifest_only
        self.ggir_only = ggir_only
        self.subject_ids = subject_ids
        self.study_filter = study_filter

    def _apply_ggir_filters(self):
        if self.subject_ids:
            os.environ["GGIR_SUBJECTS"] = ",".join(str(s) for s in self.subject_ids)
        if self.study_filter in {"obs", "int"}:
            os.environ["GGIR_STUDY"] = self.study_filter

    def run_pipe(self):
        if self.ggir_only:
            int_out = self.output_dir or type(self).INT_OUT_DIR
            obs_out = self.output_dir or type(self).OBS_OUT_DIR
            self._apply_ggir_filters()
            GG(
                matched={},
                intdir=type(self).INT_DIR,
                obsdir=type(self).OBS_DIR,
                system=self.system,
                int_out_dir=int_out,
                obs_out_dir=obs_out,
            ).run_gg()
            return None

        save_instance = Save(
            intdir=type(self).INT_DIR,
            obsdir=type(self).OBS_DIR,
            rdssdir=type(self).RDSS_DIR,
            token=self.token,
            daysago=self.daysago,
            symlink=False,
            subject_ids=self.subject_ids,
            study_filter=self.study_filter,
        )

        try:
            if self.rebuild_manifest_only:
                rebuilt_payload = save_instance.rebuild_manifest_payload_from_lss()
                save_instance._atomic_write_manifest(
                    rebuilt_payload,
                    save_instance.manifest_path,
                )
                return None

            if self.reconcile_manifest_only:
                return save_instance.reconcile_manifest()

            matched = save_instance.save()

            import json
            import pathlib

            pathlib.Path("res").mkdir(exist_ok=True)
            with open("res/data.json", "w") as f:
                json.dump(matched, f, indent=2)

            if not self.rebuild_manifest_only:
                # Per-project output roots: an explicit --output-dir overrides both;
                # otherwise each project uses its profile output dir.
                int_out = self.output_dir or type(self).INT_OUT_DIR
                obs_out = self.output_dir or type(self).OBS_OUT_DIR
                self._apply_ggir_filters()
                GG(
                    matched=matched,
                    intdir=type(self).INT_DIR,
                    obsdir=type(self).OBS_DIR,
                    system=self.system,
                    int_out_dir=int_out,
                    obs_out_dir=obs_out,
                ).run_gg()
        finally:
            Save.remove_symlink_directories([type(self).INT_DIR, type(self).OBS_DIR])

        return None
