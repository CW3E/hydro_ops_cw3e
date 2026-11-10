#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=06:00:00
#SBATCH --output=forcing/logs/pipeline-cleanup-execute-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
exec "$HYDRO_OPS_PYTHON" bin/execute_pipeline_cleanup.py \
  --plan "forcing/status/cleanup/pipeline-deletion-${SLURM_JOB_ID}.json" --execute
