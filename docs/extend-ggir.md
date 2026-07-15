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
- Sleep-log RDSS naming: `{lab}_{date}_Sleep.csv` (ActiLife) or `{lab} ({date})SleepDiary.csv` (BOOST-style)

### Code changes in this branch

1. `extend` system profile in `act/utils/pipe.py`
2. `--ggir-only` in `act/main.py` (skip REDCap ingest + group plots)
3. `acc_new.R` session-path fix for `ses-accel*/beh/` layout
4. `scripts/sync_extend_sleep_logs.py` — copy RDSS sleep CSVs → BIDS + build `sleep_log_extend.csv`
5. `act/core/gg.py` — skip BOOST QC for `extend`, auto-set `GGIR_LAYOUT=extend`

## vosslink commands

See also **Argon** section below if running on HPC instead of vosslink.

**bahaa tools** (recommended):

```bash
bahaa pipeline extend              # sync sleep logs + GGIR
bahaa pipeline extend-sync         # RDSS -> BIDS sleep logs only
bahaa pipeline extend-ggir         # GGIR only
bahaa pipeline extend-sync --dry-run
```

Manual equivalents:

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

## Argon commands (HPC)

**bahaa tools** (after vosslink sync):

```bash
bahaa pipeline extend --skip-sync
# or
bahaa pipeline extend-ggir
```

Manual equivalents:

- Projects path: `/Shared/vosslabhpc/Projects/BikeExtend/3-Experiment/2-Data/BIDS`
- Home: `/Users/bmohammad/hbc-workspaces/boost-act` (avoid `/old_Users/bmohammad`)
- RDSS **not mounted** on Argon — use `GGIR_USE_SLEEP_LOG=FALSE` or sync on vosslink
- Conda: `act-newer` (create once with `conda env create -f act/core/environment.yml`)
- Interactive compute: `qlogin -q VOSSHBC -pe smp 3 -l h_rt=24:00:00` before long runs
- Match `GGIR_NCORES` to qlogin slot count

```bash
# Windows: argonhpc
qlogin -q VOSSHBC -pe smp 3 -l h_rt=24:00:00
screen -S extend-ggir

source ~/miniconda3/etc/profile.d/conda.sh
conda activate act-newer
cd /Users/bmohammad/hbc-workspaces/boost-act
git pull --ff-only origin feature/gt3x-compare-tests

GGIR_NCORES=3 GGIR_LAYOUT=extend GGIR_USE_SLEEP_LOG=FALSE \
Rscript act/core/acc_new.R \
  --input_dir /Shared/vosslabhpc/Projects/BikeExtend/3-Experiment/2-Data/BIDS \
  --output_dir /Shared/vosslabhpc/Projects/BikeExtend/3-Experiment/2-Data/BIDS \
  --layout extend
```

Screen: detach `Ctrl+a` then `d`; reattach `screen -r`. Exit qlogin when done.

## Outputs

Per session:

```
BIDS/derivatives/GGIR-3.2.6/sub-2002/ses-accel1/output_ses-accel1/results/
  QC/data_quality_report.csv
  part5_personsummary_MM*.csv
  part5_daysummary_MM*.csv
```

(Some runs nest an extra `output_ses-accel*/` level; the helper below finds either layout.)

### Section-1 night averages (Giovanna / analysis pulls)

Average `N_atleast5minwakenight`, `sleep_efficiency`, `dur_spt_sleep_min` over available
nights in `part5_daysummary_MM_L44.8M100.6V428.8_T5A5.csv`. Prefers `ses-accel1`; if
missing/unusable, uses the **earliest viable** `ses-accel*` and sets `fallback_used=yes`
plus `session_used` / `note`.

**bahaa tools** (vosslink / Argon):

```bash
bahaa pipeline extend-night-avg
bahaa pipeline extend-night-avg --ids-file /path/to/ids.txt --out ~/logs/extend_avg.csv
```

Or directly from `boost-act`:

```bash
python scripts/extend/avg_accel1_night_metrics.py \
  --ids-file scripts/extend/giovanna_section1_ids.txt \
  --out logs/extend_accel1_night_averages.csv
```

## If sleep-log sync finds nothing

Ping Zak for:

1. Exact RDSS sleep-log filename suffix (e.g. `SleepDiary.csv`, `sleep.csv`)
2. Location of original EXTEND copy script (not in `boost-act`; `BIDS/sourcedata/` has imaging scripts only)

Update `SLEEP_HINTS` / `SKIP_HINTS` in `scripts/sync_extend_sleep_logs.py` once confirmed.

## Note on `_accel.csv` vs RAW

BOOST ingest copies RDSS `RAW.csv` into LSS as `_accel.csv`. EXTEND BIDS files are large processed CSVs under `beh/`. If results look wrong, compare against RDSS `RAW.csv` for the same lab ID and date before rerunning.
