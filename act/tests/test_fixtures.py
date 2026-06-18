from pathlib import Path
import tempfile


def _matched_lab_ids(
    signature_fixture: dict[str, list[dict[str, str]]],
) -> set[str]:
    report_ids = {row["lab_id"] for row in signature_fixture["report_rows"]}
    rdss_ids = {row["ID"] for row in signature_fixture["rdss_files"]}
    return report_ids & rdss_ids


def test_temp_study_roots_are_local(temp_study_roots):
    temp_root = Path(tempfile.gettempdir())
    for root in temp_study_roots.values():
        assert root.exists()
        assert root.is_dir()
        assert temp_root in root.parents or root == temp_root


def test_accel_filename_factory(accel_filename_factory):
    assert accel_filename_factory("8001", 3) == "sub-8001_ses-3_accel.csv"


def test_accel_path_factory_is_deterministic(accel_path_factory):
    first = accel_path_factory("int", "8001", 2)
    second = accel_path_factory("int", "8001", 2)

    assert isinstance(first, Path)
    assert first == second
    assert first.name == "sub-8001_ses-2_accel.csv"
    assert first.parts[-4:] == (
        "sub-8001",
        "accel",
        "ses-2",
        "sub-8001_ses-2_accel.csv",
    )


def test_signature_known_good_fixture(signature_known_good):
    assert _matched_lab_ids(signature_known_good) == {"1101", "1102"}


def test_signature_mismatch_fixture(signature_mismatch):
    assert _matched_lab_ids(signature_mismatch) == {"1201"}
