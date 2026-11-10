#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --tmp=120000
#SBATCH --time=00:30:00
#SBATCH --job-name=cnrfc-prism-stratified-calendar-test
#SBATCH --output=forcing/logs/prism-stratified-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" bin/test_prism_strategy_campaign.py "$@"
