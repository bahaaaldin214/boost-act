# BOOST Actigraphy Pipeline: System Definition

## 1) Purpose and Context

The BOOST Actigraphy Pipeline is an automation service that ingests accelerometer CSV exports from RDSS, reconciles them with REDCap participant mappings, places files into study-specific LSS directory structures, runs GGIR processing, and produces quality-control (QC) and group-level summary outputs.

This repository implements the operational code path used by scheduled jobs and ad-hoc operator runs. The pipeline supports both:

- **Full processing mode**: ingest + manifest update + GGIR + QC + group visualizations.
- **Manifest rebuild mode**: deterministic reconstruction of `res/data.json` from existing LSS session folders with strict validation.

Primary entrypoint: `python -m act.main`.

## 2) System Scope

### In Scope

- Pulling subject mappings from REDCap reports `43327` + `41693` (Observational, `BOOST_TOKEN`) and `53846` (Targeting/Intervention, `TARGETING_TOKEN`); merge by `boost_id` (later reports override lab conflicts). Folder routing still by boost band: `6001–7999` → OBS, `≥8000` → INT.
- Reading RDSS filenames and extracting metadata (`labID`, acquisition date, filename).
- Matching BOOST IDs to RDSS lab IDs and handling duplicates.
- Copying canonical accelerometer files to LSS study/session destinations.
- Maintaining a subject/session manifest (`res/data.json`).
- Running GGIR via R scripts and collecting derived outputs.
- Running QC checks and writing QC status CSV.
- Generating subject/session group plots from GGIR outputs.
- Operating under multiple filesystem profiles (`vosslnx`, `vosslnxft`, `local`, `argon`).

### Out of Scope

- Device firmware handling or raw accelerometer collection.
- REDCap schema management.
- Dashboard hosting and downstream BI presentation.
- Distributed job orchestration beyond shell cron wrappers in this repo.

## 3) External Systems and Interfaces

### REDCap API

- Endpoint: `https://redcap.icts.uiowa.edu/redcap/api/`.
- Access pattern: POST using token + report export parameters.
- Used by `ID_COMPARISONS._return_report()`.
- Required secret: `BOOST_TOKEN` (in env or passed CLI).

### RDSS Filesystem

- Source repository of incoming CSV files.
- Filename convention is parsed for `labID` and date, e.g. `1201 (2025-03-01)RAW.csv`.
- Read-only data source from pipeline perspective.

### LSS Project Storage

- Target location for curated/canonicalized accelerometer files.
- Separate roots for intervention and observational studies.
- Session-oriented layout expected by pipeline and GGIR.

### R + GGIR Runtime

- GGIR execution delegated through `Rscript act/core/acc_new.R`.
- Expected derivative structure under `derivatives/GGIR-3.2.6/`.

## 4) Core Functional Requirements

1. **CLI argument validation**
   - `--token` non-empty.
   - `--daysago` integer >= 0.
   - `--system` among configured profiles.

2. **System-specific path resolution**
   - Resolve INT/OBS/RDSS roots from `Pipe._SYSTEM_PATHS`.

3. **Subject-file reconciliation**
   - Build matched records from REDCap IDs and RDSS candidates.
   - Support duplicate-handling pathway.

4. **Canonical file placement**
   - Enforce destination naming pattern: `sub-####_ses-#_accel.csv`.
   - Place files under subject/session directories in study roots.

5. **Manifest lifecycle**
   - Load existing manifest defensively.
   - Normalize and serialize deterministic payload.
   - Support atomic manifest write path.

6. **GGIR and QC execution**
   - Run GGIR per study root.
   - Run QC checks and update QC status table.

7. **Group-level outputs**
   - Produce subject and session activity composition plots.

8. **Manifest-only deterministic rebuild**
   - Discover sessions from LSS folder structure.
   - Re-map subject->lab via REDCap.
   - Resolve RDSS metadata for each run.
   - Fail on strict conflicts (multiple candidates, missing mappings, unresolved metadata).

## 5) Non-Functional Requirements

### Reliability

- Manifest rebuild path writes atomically (`temp -> fsync -> replace`) to reduce corruption risk.
- Subject transaction logic in save layer includes rename planning and rollback helpers to preserve consistency.

### Observability

- Runtime logs via Python `logging` and optional `LOG_FILE` output.
- Cron wrappers persist dated logs under `logs/<system>/`.
- QC outcomes accumulated in `act/logs/GGIR_QC_errs.csv`.

### Portability

- Multiple system profiles map to different mount topologies.
- `local` profile enables workstation-like testing against local mount aliases.

### Security

- Tokens are expected from environment/CLI, not source code.
- Filesystem permissions are inherited from executing user and mount ACLs.

## 6) Runtime Modes

## Full Pipeline Mode

Invocation example:

```bash
python -m act.main --daysago 1 --token "$BOOST_TOKEN" --system vosslnx
```

Behavior:

1. Parse CLI and configure logging.
2. Configure system paths.
3. Instantiate `Save` and compare IDs.
4. Save/move files and write manifest JSON.
5. Run GGIR over INT and OBS roots.
6. Run QC for each project.
7. Generate group plots (`Group.plot_person()` and `Group.plot_session()`).
8. Remove symlink directories as final cleanup hook.

## Manifest Rebuild-Only Mode

Invocation example:

```bash
python -m act.main --daysago 1 --token "$BOOST_TOKEN" --system vosslnx --rebuild-manifest-only
```

Behavior:

1. Discover `sub-*/accel/ses-*/*_accel.csv` in LSS roots.
2. Validate single candidate per session.
3. Build subject->lab mapping from REDCap report.
4. Reconcile run ordering with RDSS metadata rows.
5. Fail if strict validation issues exist.
6. Atomically replace `res/data.json`.
7. Skip GGIR, QC, and group plotting.

## 7) Data Contracts

## 7.1 Manifest (`res/data.json`)

Top-level shape:

```json
{
  "8001": [
    {
      "filename": "1201 (2025-03-01)RAW.csv",
      "labID": "1201",
      "date": "2025-03-01",
      "run": 1,
      "study": "int",
      "file_path": "/.../sub-8001/accel/ses-1/sub-8001_ses-1_accel.csv"
    }
  ]
}
```

Normalization expectations:

- Subject IDs serialized as strings.
- Value per subject is a list of dict records.
- Date values normalized to ISO-like strings when possible.
- Records sorted by run where relevant.

## 7.2 Matching Payload (Save layer)

Internally generated records include:

- `filename`
- `labID`
- `date`
- derived `run`
- derived `study` (`int` or `obs`)
- derived `file_path`

## 7.3 QC Master CSV

Output file: `act/logs/GGIR_QC_errs.csv`.

Columns include subject/session keys and human-readable status for checks:

- Calibration error
- Hours considered
- Cleaning code
- Valid days

## 8) Filesystem Conventions

### Source Pattern (RDSS)

- CSV files in configured RDSS root.
- Filename expected to contain `labID` and `(YYYY-MM-DD)` date token.

### Destination Pattern (LSS)

- `.../sub-<subject_id>/accel/ses-<run>/sub-<subject_id>_ses-<run>_accel.csv`

### Derivative Pattern (GGIR)

- `.../derivatives/GGIR-3.2.6/sub-<subject>/accel/ses-<run>/...`
- QC scans session result directories and expected `part5_*` files.

## 9) Failure Semantics

### Hard Failures (non-zero or exception)

- Missing/invalid CLI args.
- Unconfigured RDSS path for ingest operations.
- Base GGIR derivative directories missing during QC.
- Strict manifest rebuild conflicts:
  - multiple session candidates,
  - missing REDCap subject mapping,
  - unresolved RDSS metadata for required run.

### Soft/Recoverable Behaviors

- Missing existing manifest falls back to empty payload.
- JSON decode issues on old manifest fallback to empty payload.
- Missing source file during copy logs/skips specific record path.

## 10) Deployment and Automation Model

- `cron.sh` is the production-style automation wrapper:
  - activates conda env,
  - pulls latest git state,
  - runs pipeline,
  - commits/pushes generated changes if diff exists,
  - writes time-stamped logs under `logs/<system>/`.

- `cron_local.sh` mirrors behavior for local runs and defaults to manifest-only mode.

## 11) Testing and Verification Boundaries

Test suite emphasizes:

- CLI and pipeline smoke tests with mocked dependencies.
- Save-layer edge cases and manifest reindex logic.
- Deterministic manifest rebuild behavior and strict error aggregation.

Primary tests are under `act/tests/test_*.py`, with focused coverage around `Pipe` and manifest rebuild semantics.

## 12) Known Constraints and Current Tradeoffs

- Pipeline assumes stable RDSS filename schema for metadata extraction.
- REDCap report ID is currently fixed in code path.
- GGIR invocation is shell-based and depends on local R environment correctness.
- Some modules still emit `print()` in addition to logger output.
- `argon` profile sets `RDSS_DIR=None`; ingest paths that require RDSS are expected to fail fast.

---

This document defines the current implemented system behavior based on repository code and README guidance as of this revision.
