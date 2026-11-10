#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --tmp=120000
#SBATCH --time=00:30:00
#SBATCH --job-name=cnrfc-prism-local-fit-calendar-test
#SBATCH --output=forcing/logs/prism-local-test-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export SLURM_ARRAY_JOB_ID="${SLURM_JOB_ID:?}"
exec "$HYDRO_OPS_PYTHON" bin/test_prism_calendar_strategy.py 2016-12-10 --local-fallback
