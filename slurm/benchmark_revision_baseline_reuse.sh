#!/usr/bin/env bash
#SBATCH --job-name=nwm-revision-baseline-reuse-benchmark
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --tmp=120000
#SBATCH --time=04:00:00
#SBATCH --output=forcing/logs/revision-baseline-reuse-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" bin/benchmark_revision_baseline_reuse.py "$@"
