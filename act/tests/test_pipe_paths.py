"""Pipe filesystem profile path contracts."""

from act.utils.pipe import Pipe

_READY = (
    "/mnt/nfs/lss/vosslabhpc/Projects/BOOST/InterventionStudy/"
    "3-experiment/inputs/act-int-ready"
)
_INT_OUT = (
    "/mnt/nfs/lss/vosslabhpc/Projects/BOOST/InterventionStudy/"
    "3-experiment/output"
)
_OBS_FINAL = (
    "/mnt/nfs/lss/vosslabhpc/Projects/BOOST/ObservationalStudy/"
    "3-experiment/data/act-obs-final-test-2"
)
_RDSS = "/mnt/nfs/rdss/vosslab/Repositories/Accelerometer_Data"


def test_vosslnx_matches_vosslnxft_production_layout():
    vosslnx = Pipe.system_paths("vosslnx")
    vosslnxft = Pipe.system_paths("vosslnxft")
    for key in ("INT_DIR", "INT_OUT_DIR", "OBS_DIR", "RDSS_DIR"):
        assert vosslnx[key] == vosslnxft[key]


def test_vosslnx_int_ready_and_sibling_output():
    paths = Pipe.system_paths("vosslnx")
    assert paths["INT_DIR"] == _READY
    assert paths["INT_OUT_DIR"] == _INT_OUT
    assert paths["OBS_DIR"] == _OBS_FINAL
    assert paths["RDSS_DIR"] == _RDSS


def test_configure_vosslnx_sets_out_dirs_not_input_tree():
    Pipe.configure("vosslnx")
    assert Pipe.INT_DIR == _READY
    assert Pipe.INT_OUT_DIR == _INT_OUT
    assert Pipe.INT_OUT_DIR != Pipe.INT_DIR
    assert Pipe.OBS_DIR == _OBS_FINAL
    # OBS has no separate OUT in profile → defaults to OBS_DIR
    assert Pipe.OBS_OUT_DIR == _OBS_FINAL
