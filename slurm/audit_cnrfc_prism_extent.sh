#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --tmp=120000
#SBATCH --time=02:00:00
#SBATCH --job-name=forcing-cnrfc-prism-extent-by-year
#SBATCH --output=nwm/logs/prism-extent-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
base_python="$(dirname "$(dirname "$HYDRO_OPS_PYTHON")")/../../bin/python"
exec "$base_python" "$HYDRO_OPS_PROJECT_ROOT/bin/audit_cnrfc_prism_extent.py"
