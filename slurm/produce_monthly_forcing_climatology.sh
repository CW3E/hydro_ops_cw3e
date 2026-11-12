#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --time=04:00:00
#SBATCH --output=forcing/logs/monthly-climatology-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
domain=${1:?domain required}
exec "$HYDRO_OPS_PYTHON" bin/produce_monthly_forcing_climatology.py \
  --domain "$domain" \
  --output-dir "forcing/analysis/$domain/retro/monthly-climatology/wy1980-2025/job_${SLURM_JOB_ID:?}"
