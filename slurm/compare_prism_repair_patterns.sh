#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --time=01:00:00
#SBATCH --job-name=conus-prism-rain-only-pattern-comparison
#SBATCH --output=forcing/logs/prism-pattern-comparison-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
base_python="$(dirname "$(dirname "$HYDRO_OPS_PYTHON")")/../../bin/python"
export MPLBACKEND=Agg OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$base_python" bin/compare_prism_repair_patterns.py
