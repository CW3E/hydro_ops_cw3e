#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --tmp=120000
#SBATCH --time=04:00:00
#SBATCH --job-name=prism-compact-rain-pipeline
#SBATCH --output=forcing/logs/prism-rain-pipeline-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" bin/trial_prism_rain_pipeline.py "$@"
