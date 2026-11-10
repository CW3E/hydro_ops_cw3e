#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --time=00:15:00
#SBATCH --job-name=forcing-cnrfc-prism-extent-summary
#SBATCH --output=nwm/logs/prism-extent-summary-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
base_python="$(dirname "$(dirname "$HYDRO_OPS_PYTHON")")/../../bin/python"
exec "$base_python" "$HYDRO_OPS_PROJECT_ROOT/bin/summarize_cnrfc_prism_extent.py" "$@"
