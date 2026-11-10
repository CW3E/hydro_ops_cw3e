#!/usr/bin/env bash
#SBATCH --job-name=rain-repair-summary-followup
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --tmp=40000
#SBATCH --time=04:00:00
#SBATCH --output=forcing/logs/rain-summary-followup-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" bin/refresh_rain_repair_summaries.py "$@"
