#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --tmp=120000
#SBATCH --time=12:00:00
#SBATCH --job-name=nwm-cnrfc-climatology-wy1980-2025-3tiles
#SBATCH --output=nwm/logs/climatology-benchmark-%j.out
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
source "$root/bin/project_environment.sh"
exec "$HYDRO_OPS_PYTHON" "$root/bin/benchmark_nwm_climatology.py" --workers 8 --tile-size 16 "$@"
