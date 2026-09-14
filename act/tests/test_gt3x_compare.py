from __future__ import annotations

import csv
from pathlib import Path


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _session_key(results_dir: Path) -> tuple[str, str]:
    subject = next(part for part in results_dir.parts if part.startswith("sub-"))
    session = next(part for part in results_dir.parts if part.startswith("ses-"))
    return subject, session


def _collect_gt3x_vs_raw_outputs(base_dir: Path) -> dict[tuple[str, str], dict[str, object]]:
    signatures = {}
    for qc_file in base_dir.rglob("data_quality_report.csv"):
        results_dir = qc_file.parent.parent
        key = _session_key(results_dir)
        person_files = sorted(results_dir.glob("part5_personsummary_MM*.csv"))
        day_files = sorted(results_dir.glob("part5_daysummary_MM*.csv"))
        if not person_files or not day_files:
            continue

        with qc_file.open("r", encoding="utf-8", newline="") as handle:
            qc_row = next(csv.DictReader(handle))
        with person_files[0].open("r", encoding="utf-8", newline="") as handle:
            person_row = next(csv.DictReader(handle))
        with day_files[0].open("r", encoding="utf-8", newline="") as handle:
            day_row = next(csv.DictReader(handle))

        signatures[key] = {
            "cal.error.end": qc_row.get("cal.error.end"),
            "n.hours.considered": qc_row.get("n.hours.considered"),
            "Nvaliddays": person_row.get("Nvaliddays"),
            "cleaningcode": day_row.get("cleaningcode"),
            "calendar_date": day_row.get("calendar_date"),
        }
    return signatures


def compare_gt3x_vs_raw_outputs(raw_dir: Path, gt3x_dir: Path) -> dict[str, object]:
    raw = _collect_gt3x_vs_raw_outputs(raw_dir)
    gt3x = _collect_gt3x_vs_raw_outputs(gt3x_dir)

    mismatches = []
    missing = []

    for key in sorted(raw.keys() | gt3x.keys()):
        if key not in raw or key not in gt3x:
            missing.append(key)
            continue
        if raw[key] != gt3x[key]:
            mismatches.append({"session": key, "raw": raw[key], "gt3x": gt3x[key]})

    return {
        "matched_sessions": sorted(raw.keys() & gt3x.keys()),
        "missing_sessions": missing,
        "mismatches": mismatches,
    }


def _seed_results(root: Path, subject: str, session: str, payload: dict[str, object]) -> None:
    results_dir = root / subject / "accel" / session / f"output_{session}" / "results"
    _write_csv(
        results_dir / "QC" / "data_quality_report.csv",
        [
            {
                "filename": f"{subject}_{session}_source.csv",
                "cal.error.end": payload["cal.error.end"],
                "n.hours.considered": payload["n.hours.considered"],
            }
        ],
    )
    _write_csv(
        results_dir / "part5_personsummary_MM1.csv",
        [{"Nvaliddays": payload["Nvaliddays"]}],
    )
    _write_csv(
        results_dir / "part5_daysummary_MM1.csv",
        [
            {
                "cleaningcode": payload["cleaningcode"],
                "calendar_date": payload["calendar_date"],
            }
        ],
    )


def test_compare_gt3x_vs_raw_outputs_matches_on_key_metrics(tmp_path):
    raw_root = tmp_path / "raw"
    gt3x_root = tmp_path / "gt3x"
    payload = {
        "cal.error.end": "0.12",
        "n.hours.considered": "23",
        "Nvaliddays": "7",
        "cleaningcode": "1",
        "calendar_date": "2026-06-01",
    }

    _seed_results(raw_root, "sub-7001", "ses-1", payload)
    _seed_results(gt3x_root, "sub-7001", "ses-1", payload)

    report = compare_gt3x_vs_raw_outputs(raw_root, gt3x_root)

    assert report["missing_sessions"] == []
    assert report["mismatches"] == []
    assert report["matched_sessions"] == [("sub-7001", "ses-1")]


def test_compare_gt3x_vs_raw_outputs_flags_metric_drift(tmp_path):
    raw_root = tmp_path / "raw"
    gt3x_root = tmp_path / "gt3x"

    raw_payload = {
        "cal.error.end": "0.12",
        "n.hours.considered": "23",
        "Nvaliddays": "7",
        "cleaningcode": "1",
        "calendar_date": "2026-06-01",
    }
    gt3x_payload = {
        "cal.error.end": "0.20",
        "n.hours.considered": "22",
        "Nvaliddays": "7",
        "cleaningcode": "2",
        "calendar_date": "2026-06-01",
    }

    _seed_results(raw_root, "sub-7001", "ses-1", raw_payload)
    _seed_results(gt3x_root, "sub-7001", "ses-1", gt3x_payload)

    report = compare_gt3x_vs_raw_outputs(raw_root, gt3x_root)

    assert report["missing_sessions"] == []
    assert len(report["mismatches"]) == 1
    assert report["mismatches"][0]["session"] == ("sub-7001", "ses-1")
    assert report["mismatches"][0]["raw"]["cal.error.end"] == "0.12"
    assert report["mismatches"][0]["gt3x"]["cal.error.end"] == "0.20"
