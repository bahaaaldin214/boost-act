# EXTEND GGIR Runbook

Run GGIR on BikeExtend actigraphy already staged under LSS BIDS.

## What we found

### EXTEND BIDS layout (different from BOOST)

| | BOOST | EXTEND |
|---|---|---|
| Session folder | `sub-8001/accel/ses-1/` | `sub-2002/ses-accel1/` |
| Accel file | `sub-8001_ses-1_accel.csv` | `sub-2002/ses-accel1/beh/sub-2002_ses-accel1_accel.csv` |
| GGIR derivatives | `.../derivatives/GGIR-3.2.6/sub-*/accel/ses-*` | `.../derivatives/GGIR-3.2.6/sub-*/ses-accel*/output_ses-accel*` |

Confirmed on `Z:\Projects\BikeExtend\3-Experiment\2-Data\BIDS\sub-2002`.

### RDSS raw storage (BOOST source)

Flat directory of CSV exports:

```
/mnt/nfs/rdss/vosslab/Repositories/Accelerometer_Data/
  2002 (2022-05-10)RAW.csv
  1005 (2022-05-10)RAW.csv
```

Parsed by `act/utils/comparison_utils.py` (`lab_id`, date, filename).

### Sleep logs

- `acc_new.R` supports external sleep logs via `loglocation`.
- BOOST expects `sleep_log_intervention.csv` / `sleep_log_observational.csv` beside inputs.
- EXTEND copy to LSS **did not bring sleep logs over** (no EXTEND copy script in `boost-act`).
- Sleep-log suffix on RDSS is **not documented in this repo** — confirm with Zak if sync finds zero files.

### Code changes in this branch

1. `extend` system profile in `act/utils/pipe.py`
2. `--ggir-only` in `act/main.py` (skip REDCap ingest + group plots)
3. `acc_new.R` session-path fix for `ses-accel*/beh/` layout
4. `scripts/sync_extend_sleep_logs.py` — copy RDSS sleep CSVs → BIDS + build `sleep_log_extend.csv`
5. `act/core/gg.py` — skip BOOST QC for `extend`, auto-set `GGIR_LAYOUT=extend`

## vosslink commands

```bash
cd ~/zaccel/boost-act-final   # or your boost-act clone
git pull

# 1) Optional: sync sleep logs from RDSS first
python scripts/sync_extend_sleep_logs.py \
  --bids-dir /mnt/nfs/lss/vosslabhpc/Projects/BikeExtend/3-Experiment/2-Data/BIDS \
  --rdss-dir /mnt/nfs/rdss/vosslab/Repositories/Accelerometer_Data

# Inspect what was found
ls sourcedata/sleep_logs/
head sleep_log_extend.csv

# 2) Run GGIR only (no REDCap token needed)
screen -S extend-ggir
GGIR_NCORES=3 GGIR_USE_SLEEP_LOG=TRUE \
python -m act.main --system extend --daysago 0 --ggir-only
# Ctrl+a, d

# Resume from GGIR cache after partial run
GGIR_OVERWRITE=FALSE GGIR_NCORES=3 \
python -m act.main --system extend --daysago 0 --ggir-only
```

### Direct Rscript (without Python wrapper)

```bash
GGIR_LAYOUT=extend GGIR_USE_SLEEP_LOG=TRUE GGIR_NCORES=3 \
Rscript act/core/acc_new.R \
  --input_dir /mnt/nfs/lss/vosslabhpc/Projects/BikeExtend/3-Experiment/2-Data/BIDS \
  --output_dir /mnt/nfs/lss/vosslabhpc/Projects/BikeExtend/3-Experiment/2-Data/BIDS \
  --layout extend
```

## Outputs

Per session:

```
BIDS/derivatives/GGIR-3.2.6/sub-2002/ses-accel1/output_ses-accel1/results/
  QC/data_quality_report.csv
  part5_personsummary_MM*.csv
  part5_daysummary_MM*.csv
```

## If sleep-log sync finds nothing

Ping Zak for:

1. Exact RDSS sleep-log filename suffix (e.g. `SleepDiary.csv`, `sleep.csv`)
2. Location of original EXTEND copy script (not in `boost-act`; `BIDS/sourcedata/` has imaging scripts only)

Update `SLEEP_HINTS` / `SKIP_HINTS` in `scripts/sync_extend_sleep_logs.py` once confirmed.

## Note on `_accel.csv` vs RAW

BOOST ingest copies RDSS `RAW.csv` into LSS as `_accel.csv`. EXTEND BIDS files are large processed CSVs under `beh/`. If results look wrong, compare against RDSS `RAW.csv` for the same lab ID and date before rerunning.
