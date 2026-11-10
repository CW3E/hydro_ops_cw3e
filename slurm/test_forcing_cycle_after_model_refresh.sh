#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=00:30:00
#SBATCH --output=forcing/logs/cron-recommissioning-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
unset SBATCH_RESERVATION
exec "$HYDRO_OPS_PYTHON" bin/test_forcing_cycle_after_model_refresh.py "$@"
