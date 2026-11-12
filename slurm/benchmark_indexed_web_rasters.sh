#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --time=00:10:00
#SBATCH --job-name=web-raster-indexed-png-benchmark
#SBATCH --output=forcing/logs/web-raster-indexed-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" bin/benchmark_indexed_web_rasters.py \
  --source web/publications/pilot-5268948 \
  --output "web/publications/indexed-pilot-${SLURM_JOB_ID}"
