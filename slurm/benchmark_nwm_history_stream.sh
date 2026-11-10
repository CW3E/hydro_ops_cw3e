#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --tmp=120000
#SBATCH --time=04:00:00
#SBATCH --job-name=nwm-cnrfc-history-stream-alltiles-1980
#SBATCH --output=nwm/logs/history-stream-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
exec "$HYDRO_OPS_PYTHON" "$HYDRO_OPS_PROJECT_ROOT/bin/benchmark_nwm_history_stream.py" "$@"
