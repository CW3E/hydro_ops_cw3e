#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=00:30:00
#SBATCH --array=0-5%6
#SBATCH --job-name=monthly-forcing-percentile-maps
#SBATCH --output=forcing/logs/monthly-percentile-maps-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
base_python="$(dirname "$(dirname "$HYDRO_OPS_PYTHON")")/../../bin/python"
exec "$base_python" bin/map_monthly_forcing_climatology.py
