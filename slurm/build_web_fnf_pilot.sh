#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --time=00:20:00
#SBATCH --job-name=web-cdec-fnf-full-history-gzip-pilot
#SBATCH --output=observations/logs/web-fnf-pilot-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
exec "$HYDRO_OPS_PYTHON" bin/build_web_fnf_pilot.py "$@"
