#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --tmp=120000
#SBATCH --time=01:00:00
#SBATCH --job-name=nwm-cnrfc-land-percentile-map-preview
#SBATCH --output=nwm/logs/climatology-map-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
base_python="$(dirname "$(dirname "$HYDRO_OPS_PYTHON")")/../../bin/python"
export MPLBACKEND=Agg
exec "$base_python" "$HYDRO_OPS_PROJECT_ROOT/bin/map_nwm_climatology.py" "$@"
