#!/usr/bin/env bash
#SBATCH --job-name=rain-summary-publish-subset-interrupt-retry-198610
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --tmp=40000
#SBATCH --time=04:00:00
#SBATCH --output=forcing/logs/rain-summary-publication-test-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" bin/test_rain_summary_publication.py
