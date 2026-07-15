#!/usr/bin/env python3
"""
Average EXTEND GGIR part5 night metrics (accelerometer section / ses-accel*).

Reads part5_daysummary_MM_*.csv under BikeExtend GGIR derivatives and averages
over available nights (unique calendar_date rows with numeric values):

  N_atleast5minwakenight
  sleep_efficiency
  dur_spt_sleep_min

Default preferred session is ses-accel1. If that session is missing or has no
viable part5 nights, falls back to the earliest viable ses-accel* and records
which session was used.

Example:

  python scripts/extend/avg_accel1_night_metrics.py \\
    --ggir-dir "$EXTEND_BIDS_DIR/derivatives/GGIR-3.2.6" \\
    --ids-file scripts/extend/giovanna_section1_ids.txt \\
    --out logs/extend_accel1_night_averages.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

METRIC_COLS = (
    "N_atleast5minwakenight",
    "sleep_efficiency",
    "dur_spt_sleep_min",
)

PART5_NAME = "part5_daysummary_MM_L44.8M100.6V428.8_T5A5.csv"
SESSION_RE = re.compile(r"^ses-accel(\d+)$")

DEFAULT_GGIR_CANDIDATES = (
    os.environ.get("EXTEND_GGIR_DIR", ""),
    (
        Path(os.environ["EXTEND_BIDS_DIR"]) / "derivatives" / "GGIR-3.2.6"
        if os.environ.get("EXTEND_BIDS_DIR")
        else ""
    ),
    r"Z:\Projects\BikeExtend\3-Experiment\2-Data\BIDS\derivatives\GGIR-3.2.6",
    "/mnt/nfs/lss/vosslabhpc/Projects/BikeExtend/3-Experiment/2-Data/BIDS/derivatives/GGIR-3.2.6",
    "/Shared/vosslabhpc/Projects/BikeExtend/3-Experiment/2-Data/BIDS/derivatives/GGIR-3.2.6",
    "/Volumes/vosslabhpc/Projects/BikeExtend/3-Experiment/2-Data/BIDS/derivatives/GGIR-3.2.6",
)


def _resolve_ggir_dir(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_dir():
            raise FileNotFoundError(f"--ggir-dir not found: {path}")
        return path
    for candidate in DEFAULT_GGIR_CANDIDATES:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_dir():
            return path
    raise FileNotFoundError(
        "No GGIR derivatives dir found. Pass --ggir-dir or set EXTEND_GGIR_DIR."
    )


def _load_ids(ids_file: Path | None, ids: list[str]) -> list[str]:
    out: list[str] = []
    if ids_file:
        text = ids_file.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            token = line.split(",")[0].strip().strip('"')
            if token.lower() in {"extend.study.id", "study_id", "id", "sub"}:
                continue
            token = token.removeprefix("sub-")
            if token.isdigit():
                out.append(token)
    for raw in ids:
        token = raw.strip().removeprefix("sub-")
        if token.isdigit():
            out.append(token)
    seen: set[str] = set()
    ordered: list[str] = []
    for sid in out:
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    if not ordered:
        raise ValueError("No study IDs provided (--ids-file and/or --id).")
    return ordered


def _session_sort_key(name: str) -> tuple[int, str]:
    match = SESSION_RE.match(name)
    if match:
        return (int(match.group(1)), name)
    return (10_000, name)


def _list_accel_sessions(sub_dir: Path) -> list[str]:
    if not sub_dir.is_dir():
        return []
    sessions = [
        p.name
        for p in sub_dir.iterdir()
        if p.is_dir() and SESSION_RE.match(p.name)
    ]
    return sorted(sessions, key=_session_sort_key)


def _find_part5(sub_dir: Path, session: str) -> Path | None:
    """Prefer canonical nested results path; fall back to any non-QC match."""
    direct = (
        sub_dir
        / session
        / f"output_{session}"
        / f"output_{session}"
        / "results"
        / PART5_NAME
    )
    if direct.is_file():
        return direct
    alt = sub_dir / session / f"output_{session}" / "results" / PART5_NAME
    if alt.is_file():
        return alt
    session_root = sub_dir / session
    if not session_root.is_dir():
        return None
    matches = [p for p in session_root.rglob(PART5_NAME) if "QC" not in p.parts]
    return matches[0] if matches else None


def _to_float(value: str) -> float | None:
    text = (value or "").strip()
    if not text or text.lower() in {"na", "nan", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _night_averages(part5: Path) -> tuple[int, dict[str, float]]:
    """
    Average metrics over unique calendar_date nights with all metrics present.
    Duplicate calendar_date rows keep the first occurrence.
    """
    with part5.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Empty CSV: {part5}")
        missing = [c for c in METRIC_COLS if c not in reader.fieldnames]
        if missing:
            raise ValueError(f"Missing columns {missing} in {part5}")

        by_date: dict[str, dict[str, float]] = {}
        for row in reader:
            date = (row.get("calendar_date") or "").strip()
            if not date or date in by_date:
                continue
            vals: dict[str, float] = {}
            ok = True
            for col in METRIC_COLS:
                num = _to_float(row.get(col, ""))
                if num is None:
                    ok = False
                    break
                vals[col] = num
            if ok:
                by_date[date] = vals

    n = len(by_date)
    if n == 0:
        return 0, {col: float("nan") for col in METRIC_COLS}

    means = {
        col: sum(night[col] for night in by_date.values()) / n for col in METRIC_COLS
    }
    return n, means


def _try_session(
    sub_dir: Path, session: str
) -> tuple[str, Path | None, int, dict[str, float], str]:
    """
    Returns (status, part5, n_nights, means, note).
    status is ok | missing_part5 | no_valid_nights | error
    """
    part5 = _find_part5(sub_dir, session)
    if part5 is None:
        return "missing_part5", None, 0, {}, f"{PART5_NAME} not found under {session}"
    try:
        n_nights, means = _night_averages(part5)
    except ValueError as exc:
        return "error", part5, 0, {}, str(exc)
    if n_nights == 0:
        return "no_valid_nights", part5, 0, means, "no numeric nights after dedupe"
    return "ok", part5, n_nights, means, ""


def summarize_subject(
    ggir_dir: Path,
    study_id: str,
    preferred_session: str,
    *,
    fallback_earliest: bool = True,
) -> dict[str, object]:
    sub_dir = ggir_dir / f"sub-{study_id}"
    sessions = _list_accel_sessions(sub_dir)
    row: dict[str, object] = {
        "study_id": study_id,
        "session_requested": preferred_session,
        "session_used": "",
        "fallback_used": "no",
        "status": "",
        "n_nights": 0,
        "mean_N_atleast5minwakenight": "",
        "mean_sleep_efficiency": "",
        "mean_dur_spt_sleep_min": "",
        "part5_path": "",
        "available_sessions": ",".join(sessions),
        "note": "",
    }
    if not sub_dir.is_dir():
        row["status"] = "missing_subject"
        row["note"] = "no GGIR subject folder"
        return row
    if not sessions:
        row["status"] = "missing_session"
        row["note"] = "no ses-accel* folders"
        return row

    candidates: list[str] = []
    if preferred_session in sessions:
        candidates.append(preferred_session)
    if fallback_earliest:
        for ses in sessions:
            if ses not in candidates:
                candidates.append(ses)

    last_note = ""
    for ses in candidates:
        status, part5, n_nights, means, note = _try_session(sub_dir, ses)
        last_note = note
        if status != "ok":
            continue
        row["session_used"] = ses
        row["fallback_used"] = "yes" if ses != preferred_session else "no"
        row["status"] = "ok"
        row["n_nights"] = n_nights
        row["part5_path"] = str(part5)
        row["mean_N_atleast5minwakenight"] = round(
            means["N_atleast5minwakenight"], 6
        )
        row["mean_sleep_efficiency"] = round(means["sleep_efficiency"], 6)
        row["mean_dur_spt_sleep_min"] = round(means["dur_spt_sleep_min"], 6)
        if ses != preferred_session:
            row["note"] = (
                f"{preferred_session} unavailable/unusable; "
                f"used earliest viable {ses}"
            )
        return row

    row["status"] = "no_viable_session"
    row["note"] = last_note or "no ses-accel* with usable part5 nights"
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Average EXTEND part5 night metrics; prefer ses-accel1, else earliest "
            "viable ses-accel*."
        )
    )
    parser.add_argument(
        "--ggir-dir",
        default=None,
        help="Path to derivatives/GGIR-3.2.6 (auto-detects Z:/LSS/Argon/Mac mounts)",
    )
    parser.add_argument(
        "--ids-file",
        type=Path,
        default=None,
        help="Text/CSV file with one study ID per line (or first CSV column)",
    )
    parser.add_argument(
        "--id",
        action="append",
        default=[],
        help="Study ID (repeatable). Combined with --ids-file.",
    )
    parser.add_argument(
        "--session",
        default="ses-accel1",
        help="Preferred accel session (default: ses-accel1)",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Do not fall back to earlier/other ses-accel* if preferred is missing",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output CSV path",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    ggir_dir = _resolve_ggir_dir(args.ggir_dir)
    study_ids = _load_ids(args.ids_file, args.id)
    fallback = not args.no_fallback
    logger.info("GGIR dir: %s", ggir_dir)
    logger.info(
        "Subjects: %d | preferred=%s | fallback_earliest=%s",
        len(study_ids),
        args.session,
        fallback,
    )

    rows = [
        summarize_subject(
            ggir_dir, sid, args.session, fallback_earliest=fallback
        )
        for sid in study_ids
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "study_id",
        "session_requested",
        "session_used",
        "fallback_used",
        "status",
        "n_nights",
        "mean_N_atleast5minwakenight",
        "mean_sleep_efficiency",
        "mean_dur_spt_sleep_min",
        "available_sessions",
        "note",
        "part5_path",
    ]
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    ok = sum(1 for r in rows if r["status"] == "ok")
    fb = sum(1 for r in rows if r["fallback_used"] == "yes")
    logger.info(
        "Wrote %s (%d ok / %d total; %d used session fallback)",
        args.out,
        ok,
        len(rows),
        fb,
    )
    for r in rows:
        if r["status"] != "ok":
            logger.warning(
                "sub-%s %s | %s | available=%s",
                r["study_id"],
                r["status"],
                r["note"],
                r["available_sessions"],
            )
        elif r["fallback_used"] == "yes":
            logger.info(
                "sub-%s fallback %s -> %s",
                r["study_id"],
                r["session_requested"],
                r["session_used"],
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
