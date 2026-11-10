#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --tmp=120000
#SBATCH --time=06:00:00
#SBATCH --output=forcing/logs/isolated-revision-replay-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
set +e
"$HYDRO_OPS_PYTHON" bin/test_isolated_revision_replay.py "$@" --interrupt-after-hourly
first=$?
set -e
[[ "$first" == 75 ]] || { echo "Expected interruption exit 75; got $first"; exit 1; }
"$HYDRO_OPS_PYTHON" bin/test_isolated_revision_replay.py "$@"
"$HYDRO_OPS_PYTHON" bin/test_isolated_revision_replay.py "$@"
