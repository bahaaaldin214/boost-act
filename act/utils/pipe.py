from act.utils.save import Save
from act.core.gg import GG


class Pipe:
    # class-level "exported" attributes
    INT_DIR: str = ""
    OBS_DIR: str = ""
    RDSS_DIR: str = ""

    _SYSTEM_PATHS = {
        "vosslnx": dict(
            INT_DIR="/mnt/nfs/lss/vosslabhpc/Projects/BOOST/InterventionStudy/3-experiment/data/act-int-test",
            OBS_DIR="/mnt/nfs/lss/vosslabhpc/Projects/BOOST/ObservationalStudy/3-experiment/data/act-obs-test",
            RDSS_DIR="/mnt/nfs/rdss/vosslab/Repositories/Accelerometer_Data",
        ),
        "vosslnxft": dict(
            INT_DIR="/mnt/nfs/lss/vosslabhpc/Projects/BOOST/InterventionStudy/3-experiment/data/act-int-final-test-2",
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

    def __init__(
        self,
        token,
        daysago,
        system="vosslnx",
        output_dir=None,
        rebuild_manifest_only=False,
        reconcile_manifest_only=False,
    ):
        # ensure class attrs are set for everyone (Pipe.INT_DIR etc.)
        type(self).configure(system)
        self.token = token
        self.daysago = daysago
        self.system = system
        self.output_dir = output_dir
        self.rebuild_manifest_only = rebuild_manifest_only
        self.reconcile_manifest_only = reconcile_manifest_only

    def run_pipe(self):
        save_instance = Save(
            intdir=type(self).INT_DIR,
            obsdir=type(self).OBS_DIR,
            rdssdir=type(self).RDSS_DIR,
            token=self.token,
            daysago=self.daysago,
            symlink=False,
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
                GG(
                    matched=matched,
                    intdir=type(self).INT_DIR,
                    obsdir=type(self).OBS_DIR,
                    system=self.system,
                    output_dir=self.output_dir,
                ).run_gg()
        finally:
            Save.remove_symlink_directories([type(self).INT_DIR, type(self).OBS_DIR])

        return None
