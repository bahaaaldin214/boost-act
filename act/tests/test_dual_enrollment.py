import logging
import os

from act.utils.save import Save


def _make_save(temp_study_roots):
    save = Save.__new__(Save)
    save.INT_DIR = os.fspath(temp_study_roots["int"])
    save.OBS_DIR = os.fspath(temp_study_roots["obs"])
    save.RDSS_DIR = os.fspath(temp_study_roots["rdss"])
    save.symlink = False
    save.matches = {}
    save.dupes = []
    save.manifest = {}
    save.subject_ids = None
    save.study_filter = None
    save.logger = logging.getLogger("act.utils.save")
    return save


def test_expand_combined_boost_keys_routes_to_dupes(temp_study_roots):
    save = _make_save(temp_study_roots)
    save.matches = {
        "7178, 8066": [
            {
                "filename": "1369 (2025-06-26)RAW.csv",
                "labID": "1369",
                "date": "2025-06-26",
            }
        ]
    }
    save._expand_combined_boost_keys()
    assert "7178, 8066" not in save.matches
    boost_ids = {str(d["boost_id"]) for d in save.dupes}
    assert boost_ids == {"7178", "8066"}
    assert all(d["lab_id"] == "1369" for d in save.dupes)


def test_dual_handler_uses_ses1_path(temp_study_roots):
    save = _make_save(temp_study_roots)
    save.matches = {}
    dupes = [
        {
            "lab_id": "1369",
            "boost_id": "7178",
            "filenames": ["1369 (2025-06-26)RAW.csv"],
            "dates": ["2025-06-26"],
        },
        {
            "lab_id": "1369",
            "boost_id": "8066",
            "filenames": ["1369 (2025-06-26)RAW.csv"],
            "dates": ["2025-06-26"],
        },
    ]
    out = save._handle_and_merge_duplicates(dupes)
    assert "7178" in out
    obs_path = out["7178"][0]["file_path"]
    assert obs_path.replace("\\", "/").endswith(
        "sub-7178/accel/ses-1/sub-7178_ses-1_accel.csv"
    )


def test_filter_matches_by_subject_and_study(temp_study_roots):
    save = _make_save(temp_study_roots)
    save.subject_ids = {"7178", "8001"}
    save.study_filter = "obs"
    matches = {
        "7178": [{"study": "obs", "run": 1}],
        "8001": [{"study": "int", "run": 1}],
        "7100": [{"study": "obs", "run": 1}],
    }
    filtered = save._filter_matches(matches)
    assert set(filtered.keys()) == {"7178"}
