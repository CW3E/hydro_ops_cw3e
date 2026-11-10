#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --tmp=120000
#SBATCH --time=01:00:00
#SBATCH --job-name=cnrfc-prism-calendar-assembly-test
#SBATCH --output=forcing/logs/prism-calendar-test-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
days=(2014-12-10 2015-03-31 2016-12-10 2014-12-31)
exec "$HYDRO_OPS_PYTHON" bin/test_prism_calendar_strategy.py "${days[${SLURM_ARRAY_TASK_ID:?}]}"
