# BOOST Actigraphy Pipeline: CLI Deep Dive

## 1) Entry Point

The pipeline CLI entrypoint is:

```bash
python -m act.main
```

The parser is defined in `act/main.py`, and the runtime execution branches through `act/utils/pipe.py`.

At the code level, the CLI currently exposes **three required flags** and **two optional mutually exclusive mode flags**.

## 2) Canonical Command Shape

The normal/full invocation shape is:

```bash
python -m act.main --token "$BOOST_TOKEN" --daysago 1 --system vosslnx
```

The order of flags does **not** matter because the CLI uses `argparse` named options.

Examples:

```bash
python -m act.main --system local --daysago 7 --token "$BOOST_TOKEN"
python -m act.main --token "$BOOST_TOKEN" --daysago 1 --system vosslnx --rebuild-manifest-only
python -m act.main --token "$BOOST_TOKEN" --daysago 1 --system vosslnx --reconcile-manifest-only
python -m act.main --token "$BOOST_TOKEN" --daysago 0 --system vosslnxft --subjects 7178,8066 --study both
python -m act.main --daysago 0 --system vosslnxft --ggir-only --study obs --subjects 7178
```

Subject / study filters:

- `--subjects 7178,8066` limits ingest + GGIR to those BOOST IDs (also sets `GGIR_SUBJECTS` for R).
- `--study obs|int|both` limits which study roots are ingested / run (also sets `GGIR_STUDY`).
- Dual-enrollment (OBS+INT sharing one lab_id, or a comma-joined boost_id cell like `7178, 8066`) is expanded into the duplicate handler instead of being dropped.

## 3) Argument Reference

### `--token`

- **Required:** yes
- **Type:** string
- **Validation:** must be non-empty after `.strip()`
- **Purpose:** authenticates the REDCap API request used to fetch subject/lab mappings

How it is used:

- Passed from `act.main` into `Pipe(token=...)`.
- Passed from `Pipe` into `Save(token=...)`.
- Used by `ID_COMPARISONS._return_report()` to call the REDCap API and export report `43327`.

Operational notes:

- The CLI does not read `BOOST_TOKEN` by itself. The shell expands `"$BOOST_TOKEN"` before Python starts.
- A blank string such as `--token ""` fails argument validation immediately.
- Because `Save` bootstraps REDCap/RDSS matching during initialization, **all current runtime modes still require a valid token**, including `--rebuild-manifest-only` and `--reconcile-manifest-only`.

### `--daysago`

- **Required:** yes
- **Type:** integer
- **Validation:** must parse as `int` and be `>= 0`
- **Purpose:** controls RDSS recency filtering during subject/file matching

How it is used:

- Passed from `act.main` into `Pipe(daysago=...)`.
- Passed into `Save(daysago=...)`.
- Forwarded into `ID_COMPARISONS(..., daysago=daysago)`.
- Used in RDSS file discovery to filter candidate files by acquisition date.

Important behavior detail:

- In `act/utils/comparison_utils.py`, the recency filter is guarded by `if daysago:`.
- That means `--daysago 0` is accepted by the parser, but it behaves like **no explicit recency window**, not “today only”.
- When `daysago` is falsy (`0`), the code falls back to a default lower date threshold of `2024-08-05`.

Practical interpretation:

- `--daysago 1` means “consider RDSS files from the last 1 day”.
- `--daysago 30` means “consider RDSS files from the last 30 days”.
- `--daysago 0` currently means “use the default hard-coded threshold” rather than a zero-day window.

Mode note:

- `--daysago` is still mandatory even in reconcile-only mode, even though reconcile logic operates from the existing manifest and on-disk files.

### `--system`

- **Required:** yes
- **Type:** string enum
- **Allowed values:** `vosslnx`, `vosslnxft`, `local`, `argon`
- **Purpose:** selects the filesystem path profile used for intervention, observational, and RDSS roots

How it is used:

- Parsed with fixed choices supplied by `Pipe.available_systems()`.
- Passed into `Pipe.configure(system)`.
- Sets class-level `Pipe.INT_DIR`, `Pipe.OBS_DIR`, and `Pipe.RDSS_DIR`.
- Propagates to `Save` for ingest/rebuild/reconcile and to `Group`/`QC` for downstream plotting/QC behavior.

Current path profiles:

- `vosslnx`: NFS-backed lab paths with RDSS enabled
- `vosslnxft`: alternate NFS-backed final-test paths with RDSS enabled
- `local`: local mount aliases under `/mnt/lss` and `/mnt/rdss`
- `argon`: shared LSS-like roots, but `RDSS_DIR` is currently `None`

Important caveat about `argon`:

- The parser accepts `--system argon`.
- The runtime currently instantiates `Save(...)` before branching into full/rebuild/reconcile mode.
- `Save.__init__` raises `ValueError` when `RDSS_DIR` is missing.
- As a result, `argon` is currently parse-valid but **runtime-invalid for the main CLI path**.

### `--rebuild-manifest-only`

- **Required:** no
- **Type:** boolean flag (`store_true`)
- **Default:** `False`
- **Mutual exclusion:** cannot be combined with `--reconcile-manifest-only`
- **Purpose:** rebuild `res/data.json` from the current LSS layout without doing ingest copy, GGIR, QC, or group plotting

What happens when enabled:

1. `act.main` passes `rebuild_manifest_only=True` into `Pipe`.
2. `Pipe.run_pipe()` creates a `Save` instance.
3. `Save.rebuild_manifest_payload_from_lss()` discovers `sub-*/accel/ses-*/*_accel.csv` files in the selected INT/OBS roots.
4. REDCap rows are fetched again to map subject IDs to lab IDs.
5. RDSS metadata is used to enrich each session with `filename`, `labID`, and `date`.
6. The rebuilt payload is written atomically to `res/data.json`.
7. The process exits without running `GG.run_gg()` or `Group.plot_*()`.

Strict failure behavior:

- Multiple candidate accel CSVs in one session directory
- Missing subject-to-lab mapping from REDCap
- Missing RDSS metadata for a discovered session

Exit behavior:

- Returns `0` on success
- Returns `1` if a `ValueError` bubbles up to `main()`

### `--reconcile-manifest-only`

- **Required:** no
- **Type:** boolean flag (`store_true`)
- **Default:** `False`
- **Mutual exclusion:** cannot be combined with `--rebuild-manifest-only`
- **Purpose:** verify or repair existing canonical `ses-*` CSVs against the RDSS source files referenced by the manifest

What happens when enabled:

1. `act.main` passes `reconcile_manifest_only=True` into `Pipe`.
2. `Pipe.run_pipe()` creates a `Save` instance.
3. `Save.reconcile_manifest()` loads `res/data.json`.
4. For each manifest record, it:
   - derives the expected RDSS source path,
   - validates the session directory contains a single canonical CSV candidate,
   - compares source and destination by size and SHA-256,
   - repairs mismatches by atomically replacing the destination file from RDSS.
5. `main()` logs a `reconcile_summary ...` line.
6. The process exits without running ingest copy, GGIR, QC, or group plotting.

Failure/reporting behavior:

- Missing RDSS source file increments `missing_source`
- Missing canonical destination increments `missing_dest`
- Multiple CSV candidates in a session directory increments `ambiguous_dest`
- Unrepairable replacement failures are appended to `errors`

Exit behavior:

- Returns `0` when `report["errors"]` is empty
- Returns `1` when `report["errors"]` is non-empty

Non-obvious detail:

- Even though reconcile mode works from the manifest, the current code still constructs `Save(...)` first, which means it still depends on a configured RDSS root and a working REDCap bootstrap path.

## 4) What the CLI Actually Runs

## Full Mode

If neither maintenance flag is passed, the CLI runs the full processing path:

1. Configure logging.
2. Parse and validate CLI flags.
3. Configure system paths through `Pipe`.
4. Create `Save(...)` and immediately run REDCap/RDSS matching bootstrap.
5. Copy/reindex canonical accel CSVs and persist manifest data.
6. Write `res/data.json`.
7. Run GGIR for intervention and observational roots.
8. Run QC after each GGIR project run.
9. Generate group plots with `Group.plot_person()` and `Group.plot_session()`.
10. Run final cleanup via `Save.remove_symlink_directories(...)`.

## Rebuild Mode

If `--rebuild-manifest-only` is passed, the CLI:

- still configures logging and validates the same required flags,
- still constructs `Save(...)`,
- rebuilds and atomically replaces the manifest,
- skips ingest copy,
- skips GGIR/QC,
- skips group plots.

## Reconcile Mode

If `--reconcile-manifest-only` is passed, the CLI:

- still configures logging and validates the same required flags,
- still constructs `Save(...)`,
- loads and verifies the existing manifest,
- repairs mismatched destination files where possible,
- skips ingest copy,
- skips GGIR/QC,
- skips group plots.

## 5) Validation Rules and Parse Errors

The parser enforces these rules before any pipeline code runs:

- `--token` must be provided and non-empty
- `--daysago` must be provided and parse as an integer
- `--daysago` must be non-negative
- `--system` must be one of the configured choices
- `--rebuild-manifest-only` and `--reconcile-manifest-only` cannot be passed together

Examples that fail at parse time:

```bash
python -m act.main --token "$BOOST_TOKEN" --daysago 3
python -m act.main --token "$BOOST_TOKEN" --daysago -1 --system local
python -m act.main --token "$BOOST_TOKEN" --daysago three --system local
python -m act.main --token "" --daysago 3 --system local
python -m act.main --token "$BOOST_TOKEN" --daysago 3 --system unknown
python -m act.main --token "$BOOST_TOKEN" --daysago 3 --system local --rebuild-manifest-only --reconcile-manifest-only
```

Exit codes at this stage come from `argparse`:

- `2` for parse/usage errors

## 6) Runtime Errors and Exit Codes

After parsing succeeds, `main()` returns these codes:

- `0`: success
- `1`: runtime `ValueError` from pipeline setup/execution, or reconcile report with errors
- `2`: CLI usage/parse failure from `argparse`

Important nuance:

- `main()` only catches `ValueError` around `p.run_pipe()`.
- Reconcile mode explicitly converts report errors into exit code `1`.
- Full mode does **not** currently propagate GGIR subprocess failures back to the top-level exit code, because `GG.run_gg()` logs exceptions internally instead of re-raising them.

So today:

- argument mistakes fail fast with exit `2`,
- known pipeline validation/setup failures typically return `1`,
- GGIR failures may be visible in logs without necessarily forcing a non-zero CLI exit.

## 7) Logging and Environment Variables

The CLI accepts arguments via flags, but runtime logging behavior also depends on one environment variable:

### `LOG_FILE`

- If unset, logging goes to stderr/stdout through a stream handler.
- If set, `act.main` creates the parent directory (if needed) and writes logs to that file.

Example:

```bash
export LOG_FILE="logs/local/manual_run.log"
python -m act.main --token "$BOOST_TOKEN" --daysago 7 --system local
```

Wrapper note:

- `cron.sh` and `cron_local.sh` set up `LOG_FILE`, `BOOST_TOKEN`, `BOOST_SYSTEM`, and `DAYS_AGO` in the shell, then call the same CLI.
- Those wrapper variables are **not** parser flags; they are shell-level conveniences around the CLI.

## 8) Current Gotchas and Operator Notes

### `--daysago 0` is not “today only”

The parser allows `0`, but the current implementation treats falsy `daysago` as a signal to use the fallback threshold date `2024-08-05`.

### `argon` is accepted by the parser but fails at runtime

`Pipe` advertises `argon`, but `Save.__init__` requires a non-empty RDSS directory and `argon` currently sets `RDSS_DIR=None`.

### Reconcile mode is not fully offline

Although it operates on `res/data.json` plus on-disk CSVs, the current implementation still goes through `Save(...)` initialization, which depends on REDCap/RDSS bootstrap logic.

### The current CLI is flag-based, not positional

The canonical interface is named flags such as `--token`, `--daysago`, and `--system`. Any older positional form should be treated as obsolete unless a wrapper explicitly translates it.

### Symlink creation is not part of the current `Pipe.run_pipe()` path

There is a helper in `act/utils/mnt.py` for creating mount symlinks manually, but the main CLI path itself does not call that helper.

## 9) Recommended Invocation Patterns

### Full Pipeline

```bash
python -m act.main --token "$BOOST_TOKEN" --daysago 1 --system vosslnx
```

Use when you want ingest + manifest update + GGIR + QC + group plots.

### Manifest Rebuild

```bash
python -m act.main --token "$BOOST_TOKEN" --daysago 1 --system local --rebuild-manifest-only
```

Use when the LSS folder tree is the source of truth and you want to regenerate `res/data.json`.

### Manifest Reconciliation

```bash
python -m act.main --token "$BOOST_TOKEN" --daysago 1 --system vosslnx --reconcile-manifest-only
```

Use when the manifest is already canonical and you want to verify/repair destination CSVs against RDSS.

## 10) Source of Truth for This Document

This document is based on the currently implemented CLI/runtime behavior in:

- `act/main.py`
- `act/utils/pipe.py`
- `act/utils/save.py`
- `act/utils/comparison_utils.py`
- `act/core/gg.py`

Where repository prose and code differ, this document follows the current code path.
