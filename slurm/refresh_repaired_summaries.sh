#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --tmp=40000
#SBATCH --time=12:00:00
#SBATCH --job-name=retro-repaired-summaries-1981-1985
#SBATCH --output=forcing/logs/repaired-summaries-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" bin/backfill_forcing_summaries.py \
  --input-root forcing/outputs/conus/retro/hourly --output-root forcing/outputs/conus/retro \
  --stream retro --replace-stale-retro --parallel-years --workers 8 "$@"
