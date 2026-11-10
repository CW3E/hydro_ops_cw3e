#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --tmp=120000
#SBATCH --time=04:00:00
#SBATCH --job-name=nwm-cnrfc-climatology-wy1980-2025-tile
#SBATCH --output=nwm/logs/climatology-tile-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" "$HYDRO_OPS_PROJECT_ROOT/bin/produce_nwm_climatology_tile.py" "$@"
