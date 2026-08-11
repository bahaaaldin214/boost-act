# BOOST Actigraphy Pipeline: Architecture

## 1) Architectural Overview

The system is a single-repo, file-centric data pipeline with a Python orchestration layer and an R/GGIR processing layer.

At a high level:

1. Python CLI (`act/main.py`) validates runtime inputs.
2. Pipeline coordinator (`act/utils/pipe.py`) configures environment-specific paths and execution mode.
3. Save/matching engine (`act/utils/save.py` + `act/utils/comparison_utils.py`) reconciles REDCap and RDSS and materializes canonical files + manifest.
4. GGIR wrapper (`act/core/gg.py`) invokes R processing script (`act/core/acc_new.R`).
5. QC and plots (`act/utils/qc.py`, `act/utils/plots.py`, `act/utils/group.py`) validate outputs and generate visual summaries.

## 2) Component Model

## 2.1 Entry and Control Plane

### `act/main.py`

Responsibilities:

- Define typed CLI interface (`--token`, `--daysago`, `--system`, `--rebuild-manifest-only`).
- Configure runtime logging to stdout or file via `LOG_FILE`.
- Instantiate `Pipe` and trigger pipeline execution.
- Trigger group plotting in full mode.

Control decisions:

- `--rebuild-manifest-only` gates whether GGIR and group plotting execute.
- Any `ValueError` from pipeline returns non-zero process exit.

### `act/utils/pipe.py`

Responsibilities:

- Centralize environment/system path profiles via `_SYSTEM_PATHS`.
- Export configured class-level dirs (`INT_DIR`, `OBS_DIR`, `RDSS_DIR`).
- Construct `Save` and dispatch either:
  - ingest/full pipeline path (`save()`, write manifest, run GGIR), or
  - manifest-only rebuild path (`rebuild_manifest_payload_from_lss()`, atomic write).
- Always call final cleanup hook `Save.remove_symlink_directories(...)`.

## 2.2 Data Reconciliation and Persistence Plane

### `act/utils/comparison_utils.py` (`ID_COMPARISONS`)

Responsibilities:

- Fetch REDCap report rows using API token.
- Remove problematic boost IDs mapped to multiple lab IDs.
- Detect duplicate report rows.
- Parse RDSS CSV filenames into a dataframe (`ID`, `Date`, `filename`).
- Apply recency/default date filters.
- Return:
  - `matches`: boost_id -> list of metadata dicts.
  - `duplicates`: duplicate overlap records across report and RDSS.

### `act/utils/save.py` (`Save`)

Responsibilities:

- Load and normalize prior manifest.
- Determine run indices, study membership, and canonical destination paths.
- Process duplicates and merge into unified subject record sets.
- Execute per-subject transaction logic for copy/rename consistency.
- Persist manifest (`_save_manifest` or `_atomic_write_manifest`).
- Discover LSS sessions and deterministically rebuild manifest with strict checks.

Key architectural traits:

- **Transactional subject processing:** plan operations, apply renames in two phases, rollback where possible on failure.
- **Deterministic serialization:** normalize payload shape and date formatting.
- **Strict rebuild semantics:** aggregate subject-level errors and abort rebuild if any strict conflict remains.

## 2.3 Processing and Analytics Plane

### `act/core/gg.py` (`GG`)

Responsibilities:

- Iterate over INT and OBS study roots.
- Shell out to `Rscript act/core/acc_new.R` with project and derivative dirs.
- Stream GGIR output to logger.
- On successful GGIR run, invoke QC runner (`QC(project_type, system)` -> `qc()`).

Failure behavior:

- Logs subprocess and unexpected exceptions per project directory.
- Continues control flow according to current exception handling (logs instead of hard stop inside loop).

### `act/core/acc_new.R`

Responsibilities:

- Parse `--project_dir` and `--deriv_dir`.
- Infer sleep-log target from project root naming (`act-int` vs `act-obs`).
- Enumerate `*accel.csv` files and create derivative folders.
- Execute GGIR (`mode=1:6`) with pipeline-specific parameters.

Notable configuration points:

- Timezone: `America/Chicago`.
- Activity thresholds configured in script.
- Derivative output rooted under project-specific `derivatives/GGIR-3.2.6/`.

### `act/utils/qc.py` (`QC`)

Responsibilities:

- Determine project derivative root and expected wear-time days.
- Traverse subject/session result trees.
- Parse GGIR QC/person/day summary files.
- Run QC checks:
  - calibration error,
  - hours considered,
  - valid days,
  - cleaning codes.
- Upsert human-readable outcomes into `act/logs/GGIR_QC_errs.csv`.
- Trigger per-session plot JSON/figure generation through plot helper integration.

### `act/utils/plots.py` (`ACT_PLOTS`)

Responsibilities:

- Build output paths for subject/session visualizations.
- Produce summary stacked composition plots.
- Produce day-level activity composition plots with session boundaries.

### `act/utils/group.py` (`Group`)

Responsibilities:

- Aggregate GGIR person summaries across study roots.
- Build subject-level and session-level stacked activity composition views.
- Write HTML outputs under `./plots/group`.

## 2.4 Infrastructure Helper Plane

### `act/utils/mnt.py`

Responsibilities:

- Optionally create symlinks to configured INT/OBS/RDSS roots under a local target directory.
- Resolve target paths via `Pipe.system_paths`.

## 3) Execution Flows

## 3.1 Full Pipeline Flow

```text
CLI (act.main)
  -> configure logging
  -> Pipe.configure(system)
  -> Save.__init__ (ID comparison bootstrap)
  -> Save.save()
      -> determine run/study/location
      -> subject transactions (copy/rename/manifest merge)
      -> manifest write
  -> write res/data.json
  -> GG.run_gg()
      -> Rscript acc_new.R on INT root
      -> QC(int)
      -> Rscript acc_new.R on OBS root
      -> QC(obs)
  -> Group.plot_person()
  -> Group.plot_session()
  -> cleanup symlink directories
```

## 3.2 Manifest Rebuild-Only Flow

```text
CLI (--rebuild-manifest-only)
  -> Pipe.configure(system)
  -> Save.__init__
  -> Save.rebuild_manifest_payload_from_lss()
      -> discover_lss_sessions()
      -> REDCap subject->lab mapping
      -> RDSS metadata reconciliation by run index
      -> strict conflict aggregation
      -> deterministic payload
  -> Save._atomic_write_manifest(res/data.json)
  -> cleanup symlink directories
  -> exit (no GGIR/QC/group plots)
```

## 4) Data and State Boundaries

### Input Boundaries

- **API input:** REDCap report CSV payload.
- **Filesystem input:** RDSS raw CSV filenames and contents, existing LSS session folders.
- **Runtime input:** CLI args and environment variables.

### Internal State

- `Pipe` class-level path state reflects selected system profile.
- `Save.manifest` is mutable in-memory representation during transaction cycle.

### Output Boundaries

- Manifest JSON (`res/data.json` and `act/res/data.json` contexts exist in repo usage patterns).
- Copied canonical accel CSV files in LSS subject/session paths.
- GGIR derivatives under each study root.
- QC status table (`act/logs/GGIR_QC_errs.csv`).
- Group and per-session plot artifacts.
- Runtime logs (`logs/<system>/timestamp.log`).

## 5) Directory-Oriented Architecture Map

```text
act/
  main.py                  # CLI entry and top-level orchestration
  core/
    gg.py                  # Python -> R GGIR bridge + QC trigger
    acc_new.R              # GGIR execution script
    environment.yml        # R/GGIR conda environment reference
  utils/
    pipe.py                # system profile config + mode dispatch
    comparison_utils.py    # REDCap/RDSS reconciliation data source
    save.py                # canonical placement + manifest lifecycle
    qc.py                  # QC evaluation + QC CSV updates
    plots.py               # per-session/subject plotting primitives
    group.py               # cohort-level aggregation/plot outputs
    mnt.py                 # optional symlink helpers
  tests/
    test_pipeline_smoke.py
    test_manifest_rebuild_from_lss.py
    test_save_*.py         # manifest and save-edge behavior guarantees
```

## 6) Configuration Architecture

### Runtime Flags

- `--token`: REDCap token.
- `--daysago`: RDSS recency window.
- `--system`: path profile.
- `--rebuild-manifest-only`: mode switch.

### Environment Variables

- `BOOST_TOKEN`: passed into CLI in wrappers.
- `BOOST_SYSTEM`: wrapper-level system override.
- `DAYS_AGO`: wrapper-level window override.
- `LOG_FILE`: optional Python logger file sink path.

### System Profiles (`Pipe._SYSTEM_PATHS`)

Each profile defines:

- intervention root (`INT_DIR`),
- observational root (`OBS_DIR`),
- RDSS root (`RDSS_DIR`),
- optional GGIR output roots (`INT_OUT_DIR`, `OBS_OUT_DIR`; default to the matching input dir).

On vosslink, `vosslnx` and `vosslnxft` both use production INT `act-int-ready` + sibling `output/`, and OBS `act-obs-final-test-2`.

`argon` intentionally has `RDSS_DIR=None`, making ingest calls invalid by design.

## 7) Error-Handling Architecture

- **Input validation errors** surface early in CLI parsing.
- **System profile errors** raise `ValueError` for unknown systems.
- **Manifest loading errors** downgrade to warnings + empty fallback payload.
- **Strict rebuild conflicts** aggregate and raise single `ValueError` with per-subject details.
- **GGIR subprocess failures** are logged at exception level within project loop.
- **Cleanup hook** in `Pipe.run_pipe()` executes via `finally` semantics.

## 8) Test Architecture and Confidence Model

Current tests verify critical control points:

- `test_pipeline_smoke.py`
  - mocked full flow wiring,
  - rebuild-only mode skipping GGIR,
  - CLI integration expectations.
- `test_manifest_rebuild_from_lss.py`
  - discovery behavior,
  - strict error conditions,
  - deterministic ordering and payload structure.
- `test_save_*`
  - manifest normalization/reindex behavior,
  - save edge-case handling.

This provides high confidence in orchestration and manifest semantics, while GGIR numeric correctness remains largely delegated to GGIR outputs and manual QA artifacts.

## 9) Operational Architecture (Cron)

### `cron.sh` (production-style)

- Activates conda env.
- Pulls latest main branch.
- Executes CLI with configured token/system.
- Writes timestamped logs.
- Commits/pushes repo changes if artifacts changed.

### `cron_local.sh` (workstation)

- Validates token.
- Runs local profile command (currently manifest-only default).
- Streams output to tee + timestamped log.

## 10) Architectural Risks and Improvement Targets

1. **Hardcoded REDCap report id** reduces multi-study flexibility.
2. **Filename parsing dependency** on RDSS naming format can break silently if naming changes.
3. **Mixed `print` and logger usage** weakens log consistency.
4. **Shell-based GGIR invocation** couples success to local R env and script path assumptions.
5. **Manifest location duplication** (`res/` and `act/res/` present in tree) may create ambiguity for operators.

---

This architecture document reflects the current implementation in this repository and is intended as a maintainer-facing reference for onboarding, operations, and future refactoring.
