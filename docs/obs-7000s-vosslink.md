# OBS / 7000-series GGIR on vosslink

Ali needs activity reports for BOOST **7000-series** (observational) subjects,
especially dual-enrollment **7178 / 8066**.

## Why 7000s looked "missing"

1. Production cron uses `--system vosslnxft`:
   - INT input: `.../InterventionStudy/3-experiment/inputs/act-int-ready`
   - OBS input: `.../ObservationalStudy/3-experiment/data/act-obs-final-test-2`
2. **7178** accel CSV already exists under the older **vosslnx** OBS test tree
   (`act-obs-test/sub-7178/...`) but was **never staged** into
   `act-obs-final-test-2`, so production GGIR never saw it.
3. Dual-enrollment IDs were historically **dropped** when REDCap stored a
   comma-joined boost_id (`"7178, 8066"`). Code now expands those into the
   OBS/INT duplicate handler instead of discarding.
4. Same lab_id with two boost_ids (OBS+INT) now routes through that dual
   handler (not only exact duplicate REDCap rows).

Subject bands:

| Band | Study |
|------|--------|
| 7000-7699 | BOOST_OBS → OBS_DIR |
| 7700-7999 | BOOST_OO → OBS_DIR |
| 8000+ | BOOST_INT → INT_DIR / act-int-ready |
| 9000+ | BOOST_INT from NEU → INT |

## vosslink steps (Bahaa)

Assume tools installed (`bahaa` on PATH) and `BOOST_TOKEN` available.

### 0) Pull code

```bash
bahaa repos update
# or: cd /home/vosslab-svc/zaccel/boost-act-final && git pull --ff-only
```

### 1) Status (dry / read-only)

```bash
bahaa pipeline ggir-status
bahaa pipeline ggir-status --ids 7178,8066 --also-check-obs-test
bahaa pipeline ggir-status --band 7000
```

Interpret:

- `obs_csv>0` on production OBS → staged for GGIR
- `obs-test csv>0` but production `obs_csv=0` → needs stage copy
- `staged_but_no_ggir` → run targeted GGIR

### 2) Stage 7178 from obs-test → production OBS (dry-run first)

```bash
bahaa pipeline ggir-stage-obs --ids 7178
bahaa pipeline ggir-stage-obs --ids 7178 --apply
```

### 3) Targeted OBS GGIR for 7178 (overwrite off for others)

```bash
export BOOST_TOKEN=...          # required only if you also ingest
export GGIR_OVERWRITE=FALSE     # skip already-processed subjects
bahaa pipeline ggir --ggir-only --study obs --subjects 7178
```

Full ingest+GGIR for dual pair (wide RDSS lookback):

```bash
DAYS_AGO=0 BOOST_TOKEN=... bahaa pipeline ggir --study both --subjects 7178,8066
```

`--daysago 0` uses the pipeline default lower date threshold (`2024-08-05`),
not "today only".

### 4) Re-check

```bash
bahaa pipeline ggir-status --ids 7178,8066 --also-check-obs-test
```

Olivia/Ali read INT derivatives under:

`.../InterventionStudy/3-experiment/output/derivatives/GGIR-3.2.6/`

OBS derivatives (vosslnxft) live under the OBS input tree:

`.../ObservationalStudy/3-experiment/data/act-obs-final-test-2/derivatives/GGIR-3.2.6/`

## Direct python (same machine)

```bash
cd /home/vosslab-svc/zaccel/boost-act-final
source /opt/anaconda3-2024.10-1/etc/profile.d/conda.sh
conda activate act

python scripts/status_subjects.py --ids 7178,8066 --system vosslnxft --also-check-obs-test

GGIR_OVERWRITE=FALSE GGIR_SUBJECTS=7178 GGIR_STUDY=obs \
  python -m act.main --daysago 0 --token "$BOOST_TOKEN" --system vosslnxft --ggir-only --study obs --subjects 7178
```

## Open questions

- If RDSS has no `1369 (*RAW.csv` (likely lab for 8066/7178), INT side cannot
  ingest from REDCap/RDSS — only OBS copy from `act-obs-test` helps 7178.
- Confirm with Megan/Olivia whether Ali reports should key off **7178** (OBS)
  or **8066** (INT) for this dual-enrolled person.
