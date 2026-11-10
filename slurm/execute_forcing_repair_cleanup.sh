#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=04:00:00
#SBATCH --output=forcing/logs/repair-cleanup-execute-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" bin/execute_forcing_repair_cleanup.py \
  --plan forcing/status/cleanup/repair-originals-5188478.json \
  --journal "forcing/status/cleanup/repair-originals-deletion-${SLURM_JOB_ID}.jsonl" --execute
