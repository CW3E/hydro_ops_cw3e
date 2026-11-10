#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --tmp=120000
#SBATCH --time=01:00:00
#SBATCH --job-name=cnrfc-prism-fallback-spatial-test
#SBATCH --output=forcing/logs/prism-fallback-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
days=(2014-12-11 2015-04-01 2016-12-11)
exec "$HYDRO_OPS_PYTHON" bin/test_prism_fallback_strategy.py "${days[${SLURM_ARRAY_TASK_ID:?}]}"
