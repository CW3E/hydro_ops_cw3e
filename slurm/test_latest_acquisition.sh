#!/usr/bin/env bash
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --tmp=240000
#SBATCH --time=03:00:00
#SBATCH --output=forcing/logs/latest-acquisition-test-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" bin/test_latest_acquisition.py
