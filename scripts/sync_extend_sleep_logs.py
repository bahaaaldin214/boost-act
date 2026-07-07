#!/usr/bin/env python3
"""
Copy EXTEND sleep-log CSVs from RDSS into the BikeExtend BIDS tree and build
sleep_log_extend.csv for GGIR.

RDSS layout (same as BOOST ingest):
  /mnt/nfs/rdss/vosslab/Repositories/Accelerometer_Data/
    2002 (2022-05-10)RAW.csv
    2002 (2022-05-10)SleepDiary.csv   # example; confirm suffix with Zak

EXTEND BIDS layout:
  .../BIDS/sub-2002/ses-accel1/beh/sub-2002_ses-accel1_accel.csv

Lab IDs are inferred from subject folder names: sub-2002 -> 2002.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_BIDS = (
    "/mnt/nfs/lss/vosslabhpc/Projects/BikeExtend/3-Experiment/2-Data/BIDS"
)
DEFAULT_RDSS = "/mnt/nfs/rdss/vosslab/Repositories/Accelerometer_Data"

SLEEP_HINTS = ("sleep", "diary", "log", "bed", "spt")
SKIP_HINTS = ("raw", "60sec", "gt3x", "accel", "export")


def _parse_rdss_filename(filename: str) -> tuple[str, str] | None:
    match = re.match(r"^(\d+)\s+\(([^)]+)\)(.+)$", filename)
    if not match:
        return None
    return match.group(1), match.group(2)


def _looks_like_sleep_file(filename: str) -> bool:
    lower = filename.lower()
    if not lower.endswith(".csv"):
        return False
    if any(hint in lower for hint in SKIP_HINTS):
        return False
    return any(hint in lower for hint in SLEEP_HINTS)


def _subject_lab_id(subject_dir: Path) -> str | None:
    match = re.fullmatch(r"sub-(\d+)", subject_dir.name)
    return match.group(1) if match else None


def discover_bids_subjects(bids_dir: Path) -> list[tuple[str, Path]]:
    subjects = []
    for entry in sorted(bids_dir.iterdir()):
        if not entry.is_dir():
            continue
        lab_id = _subject_lab_id(entry)
        if lab_id:
            subjects.append((lab_id, entry))
    return subjects


def discover_rdss_sleep_files(rdss_dir: Path) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {}
    if not rdss_dir.is_dir():
        raise FileNotFoundError(f"RDSS directory not found: {rdss_dir}")

    for entry in sorted(rdss_dir.iterdir()):
        if not entry.is_file() or entry.suffix.lower() != ".csv":
            continue
        if not _looks_like_sleep_file(entry.name):
            continue
        parsed = _parse_rdss_filename(entry.name)
        if not parsed:
            logger.warning("Skipping RDSS sleep candidate with unexpected name: %s", entry.name)
            continue
        lab_id, _date = parsed
        grouped.setdefault(lab_id, []).append(entry)
    return grouped


def copy_sleep_files(
    bids_dir: Path,
    rdss_dir: Path,
    dry_run: bool = False,
) -> list[Path]:
    copied: list[Path] = []
    sleep_by_lab = discover_rdss_sleep_files(rdss_dir)
    dest_root = bids_dir / "sourcedata" / "sleep_logs"
    dest_root.mkdir(parents=True, exist_ok=True)

    for lab_id, subject_dir in discover_bids_subjects(bids_dir):
        matches = sleep_by_lab.get(lab_id, [])
        if not matches:
            logger.warning("No RDSS sleep-log files found for %s (lab %s)", subject_dir.name, lab_id)
            continue

        subject_dest = dest_root / subject_dir.name
        if not dry_run:
            subject_dest.mkdir(parents=True, exist_ok=True)

        for source in matches:
            target = subject_dest / source.name
            logger.info("Copy %s -> %s", source, target)
            if not dry_run:
                shutil.copy2(source, target)
            copied.append(target)
    return copied


def build_aggregate_sleep_log(bids_dir: Path, dry_run: bool = False) -> Path | None:
    sleep_root = bids_dir / "sourcedata" / "sleep_logs"
    if not sleep_root.is_dir():
        logger.warning("No sourcedata/sleep_logs directory to aggregate.")
        return None

    rows: list[dict[str, str]] = []
    for csv_path in sorted(sleep_root.rglob("*.csv")):
        if csv_path.name == "sleep_log_extend.csv":
            continue
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    continue
                for row in reader:
                    enriched = dict(row)
                    enriched.setdefault("source_file", str(csv_path))
                    rows.append(enriched)
        except Exception as exc:
            logger.warning("Could not read %s: %s", csv_path, exc)

    if not rows:
        logger.warning("No sleep-log rows found to aggregate.")
        return None

    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    aggregate_path = bids_dir / "sleep_log_extend.csv"
    logger.info("Writing aggregate sleep log (%s rows) -> %s", len(rows), aggregate_path)
    if dry_run:
        return aggregate_path

    with aggregate_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return aggregate_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync EXTEND sleep logs from RDSS into BikeExtend BIDS.",
    )
    parser.add_argument(
        "--bids-dir",
        default=os.getenv("EXTEND_BIDS_DIR", DEFAULT_BIDS),
        help="BikeExtend BIDS root",
    )
    parser.add_argument(
        "--rdss-dir",
        default=os.getenv("RDSS_DIR", DEFAULT_RDSS),
        help="RDSS accelerometer repository root",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions without copying or writing files",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    args = build_parser().parse_args(argv)

    bids_dir = Path(args.bids_dir)
    rdss_dir = Path(args.rdss_dir)
    if not bids_dir.is_dir():
        logger.error("BIDS directory not found: %s", bids_dir)
        return 1

    copied = copy_sleep_files(bids_dir, rdss_dir, dry_run=args.dry_run)
    aggregate = build_aggregate_sleep_log(bids_dir, dry_run=args.dry_run)

    logger.info("Copied %s sleep-log file(s).", len(copied))
    if aggregate:
        logger.info("Aggregate sleep log: %s", aggregate)
    else:
        logger.warning(
            "No aggregate sleep log written. Confirm RDSS sleep-log filename "
            "suffixes with Zak, then rerun."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
