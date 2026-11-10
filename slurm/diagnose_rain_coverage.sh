#!/usr/bin/env bash
#SBATCH --job-name=retro-mar-apr-rain-cell-diagnosis
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --time=01:00:00
#SBATCH --array=0-1%2
#SBATCH --output=forcing/logs/rain-coverage-diagnosis-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
exec "$HYDRO_OPS_PYTHON" bin/diagnose_rain_coverage.py
