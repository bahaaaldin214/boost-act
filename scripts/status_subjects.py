#!/usr/bin/env python3
"""Status check for BOOST actigraphy subjects (OBS/INT staging + GGIR).

Runs from bahaa tools or boost-act. Uses GGIR_REPO for act.utils.pipe paths.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    env = os.environ.get("GGIR_REPO")
    if env:
        return Path(env)
    # boost-act/scripts/status_subjects.py -> repo root
    here = Path(__file__).resolve()
    if here.parent.name == "scripts":
        return here.parents[1]
    return Path(env or ".")


def _load_pipe_paths(system: str) -> dict:
    root = _repo_root()
    sys.path.insert(0, str(root))
    from act.utils.pipe import Pipe

    return Pipe.system_paths(system)


def _csv_count(session_dir: Path) -> int:
    if not session_dir.is_dir():
        return 0
    return sum(1 for p in session_dir.glob("*_accel.csv") if p.is_file())


def _has_ggir(deriv_root: Path, subject: str) -> bool:
    sub = deriv_root / f"sub-{subject}"
    if not sub.is_dir():
        return False
    for path in sub.rglob("part5_daysummary*.csv"):
        if path.is_file():
            return True
    return sub.is_dir() and any(sub.rglob("meta"))


def _subject_report(
    subject: str,
    int_dir: Path,
    obs_dir: Path,
    int_out: Path,
    obs_out: Path,
    rdss_dir: Path | None,
    manifest: dict,
) -> dict:
    band = "unknown"
    try:
        sid = int(subject)
        if 7000 <= sid <= 7699:
            band = "BOOST_OBS"
        elif 7700 <= sid <= 7999:
            band = "BOOST_OO"
        elif sid >= 9000:
            band = "BOOST_INT_NEU"
        elif sid >= 8000:
            band = "BOOST_INT"
    except ValueError:
        pass

    obs_accel = obs_dir / f"sub-{subject}" / "accel"
    int_accel = int_dir / f"sub-{subject}" / "accel"
    obs_sessions = (
        sorted(p.name for p in obs_accel.glob("ses-*") if p.is_dir())
        if obs_accel.is_dir()
        else []
    )
    int_sessions = (
        sorted(p.name for p in int_accel.glob("ses-*") if p.is_dir())
        if int_accel.is_dir()
        else []
    )

    obs_csv = sum(_csv_count(obs_accel / ses) for ses in obs_sessions)
    int_csv = sum(_csv_count(int_accel / ses) for ses in int_sessions)

    obs_deriv = _has_ggir(obs_out / "derivatives" / "GGIR-3.2.6", subject)
    int_deriv = _has_ggir(int_out / "derivatives" / "GGIR-3.2.6", subject)

    manifest_hits = manifest.get(str(subject)) or manifest.get(subject) or []
    lab_ids = sorted(
        {
            str(rec.get("labID") or rec.get("lab_id") or "")
            for rec in manifest_hits
            if isinstance(rec, dict)
        }
        - {""}
    )

    rdss_hits = []
    if rdss_dir and rdss_dir.is_dir() and lab_ids:
        for lab in lab_ids:
            rdss_hits.extend(
                sorted(p.name for p in rdss_dir.glob(f"{lab} (*)RAW.csv"))[:5]
            )

    return {
        "subject": subject,
        "band": band,
        "obs_root_exists": obs_dir.is_dir(),
        "int_root_exists": int_dir.is_dir(),
        "obs_sessions": obs_sessions,
        "int_sessions": int_sessions,
        "obs_accel_csv_count": obs_csv,
        "int_accel_csv_count": int_csv,
        "obs_ggir": obs_deriv,
        "int_ggir": int_deriv,
        "manifest_records": len(manifest_hits) if isinstance(manifest_hits, list) else 0,
        "lab_ids_from_manifest": lab_ids,
        "rdss_sample": rdss_hits,
    }


def _discover_band(obs_dir: Path, int_dir: Path, lo: int, hi: int) -> list[str]:
    found = set()
    for root in (obs_dir, int_dir):
        if not root.is_dir():
            continue
        for path in root.glob("sub-*"):
            if not path.is_dir():
                continue
            digits = path.name.replace("sub-", "")
            try:
                val = int(digits)
            except ValueError:
                continue
            if lo <= val <= hi:
                found.add(digits)
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", default="vosslnxft")
    parser.add_argument("--ids", default="")
    parser.add_argument("--band", choices=("7000", "8000"), default=None)
    parser.add_argument("--manifest", default="")
    parser.add_argument("--also-check-obs-test", action="store_true")
    args = parser.parse_args(argv)

    paths = _load_pipe_paths(args.system)
    int_dir = Path(paths["INT_DIR"])
    obs_dir = Path(paths["OBS_DIR"])
    int_out = Path(paths.get("INT_OUT_DIR") or paths["INT_DIR"])
    obs_out = Path(paths.get("OBS_OUT_DIR") or paths["OBS_DIR"])
    rdss = Path(paths["RDSS_DIR"]) if paths.get("RDSS_DIR") else None

    ids: list[str] = [x.strip() for x in args.ids.split(",") if x.strip()]
    if args.band == "7000":
        ids = sorted(set(ids) | set(_discover_band(obs_dir, int_dir, 7000, 7999)))
    elif args.band == "8000":
        ids = sorted(set(ids) | set(_discover_band(obs_dir, int_dir, 8000, 8999)))
    if not ids:
        ids = ["7178", "8066"]

    manifest_path = (
        Path(args.manifest)
        if args.manifest
        else (_repo_root() / "res" / "data.json")
    )
    manifest: dict = {}
    if manifest_path.is_file():
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                manifest = raw
        except json.JSONDecodeError:
            pass

    print(f"system={args.system}")
    print(f"GGIR_REPO={_repo_root()}")
    print(f"INT_DIR={int_dir}")
    print(f"OBS_DIR={obs_dir}")
    print(f"INT_OUT={int_out}")
    print(f"OBS_OUT={obs_out}")
    print(f"RDSS_DIR={rdss}")
    print(f"subjects={','.join(ids)}")
    print("---")

    rows = []
    for sid in ids:
        row = _subject_report(sid, int_dir, obs_dir, int_out, obs_out, rdss, manifest)
        rows.append(row)
        print(
            f"{sid} band={row['band']} "
            f"obs_csv={row['obs_accel_csv_count']} int_csv={row['int_accel_csv_count']} "
            f"obs_ggir={row['obs_ggir']} int_ggir={row['int_ggir']} "
            f"obs_ses={row['obs_sessions'] or '-'} int_ses={row['int_sessions'] or '-'} "
            f"manifest_n={row['manifest_records']} labs={row['lab_ids_from_manifest'] or '-'}"
        )
        if row["rdss_sample"]:
            print(f"  rdss: {', '.join(row['rdss_sample'])}")

    if args.also_check_obs_test:
        test_paths = _load_pipe_paths("vosslnx")
        test_obs = Path(test_paths["OBS_DIR"])
        print("---")
        print(f"also vosslnx OBS_DIR={test_obs}")
        for sid in ids:
            accel = test_obs / f"sub-{sid}" / "accel"
            sessions = (
                sorted(p.name for p in accel.glob("ses-*") if p.is_dir())
                if accel.is_dir()
                else []
            )
            csv_n = sum(_csv_count(accel / ses) for ses in sessions)
            print(f"{sid} obs-test csv={csv_n} ses={sessions or '-'}")

    missing_obs = [
        r["subject"]
        for r in rows
        if int(r["subject"]) < 8000 and r["obs_accel_csv_count"] == 0
    ]
    missing_ggir = [
        r["subject"]
        for r in rows
        if (r["obs_accel_csv_count"] and not r["obs_ggir"])
        or (r["int_accel_csv_count"] and not r["int_ggir"])
    ]
    print("---")
    print(f"missing_obs_staging={missing_obs or 'none'}")
    print(f"staged_but_no_ggir={missing_ggir or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
