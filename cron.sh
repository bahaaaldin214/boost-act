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

# Push automated result commits to a writable fork remote, since origin
# (HBCLab/boost-act) is read-only for the service account. Configure the fork
# remote name via RESULTS_REMOTE (default: personal).
RESULTS_REMOTE="${RESULTS_REMOTE:-personal}"
if ! git diff --quiet; then
  git add .
  git commit -m "automated commit by vosslab linux"
  if git remote get-url "${RESULTS_REMOTE}" >/dev/null 2>&1; then
    git push "${RESULTS_REMOTE}" HEAD
  else
    echo "Results remote '${RESULTS_REMOTE}' not configured; skipping push." >&2
  fi
fi
