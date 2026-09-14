#!/usr/bin/env bash

# set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source /opt/anaconda3-2024.10-1/etc/profile.d/conda.sh
conda activate act

cd "${REPO_ROOT}"

git pull --ff-only

if [[ -z "${BOOST_TOKEN:-}" ]]; then
  echo "BOOST_TOKEN is required. Aborting." >&2
  exit 1
fi

SYSTEM="${BOOST_SYSTEM:-vosslnxft}"
DAYS_AGO="${DAYS_AGO:-30}"

mkdir -p "logs/${SYSTEM}"
LOG_FILE="logs/${SYSTEM}/$(date +%Y%m%d_%H%M%S).log"
export LOG_FILE
python -m act.main --daysago "${DAYS_AGO}" --token "${BOOST_TOKEN}" --system "${SYSTEM}" >> "${LOG_FILE}" 2>&1

# No auto-commit: GGIR outputs are written to the LSS share (outside this repo),
# and run logs live under logs/ which is git-ignored. Auto-committing logs used
# to diverge the box from the fork and break `git pull --ff-only` on the next run.
