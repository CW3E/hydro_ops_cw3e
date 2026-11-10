#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --tmp=20000
#SBATCH --time=00:30:00
#SBATCH --job-name=stage4-native-hourly-sixhour-diagnosis
#SBATCH --output=forcing/logs/stage4-native-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
days=(20021211 20111211 20141211 20151211 20161211)
day="${days[${SLURM_ARRAY_TASK_ID:?}]}"
exec "$HYDRO_OPS_PYTHON" bin/diagnose_stage4_native.py "$day" \
  --output "forcing/status/stage4-native-${SLURM_ARRAY_JOB_ID}/${day}.json"
