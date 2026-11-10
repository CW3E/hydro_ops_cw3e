#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --tmp=120000
#SBATCH --time=04:00:00
#SBATCH --output=forcing/logs/bounded-prism-publication-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
# No implicit --publish: submission without it remains a dry run.
"$HYDRO_OPS_PYTHON" bin/publish_bounded_prism_replay.py "$@"
# An accepted unchanged repeat must perform no data writes.
"$HYDRO_OPS_PYTHON" bin/publish_bounded_prism_replay.py "$@"
