#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --tmp=40000
#SBATCH --time=24:00:00
#SBATCH --job-name=prism-rain-publish-1981-four-domain-subsets
#SBATCH --output=forcing/logs/prism-rain-publish-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" bin/publish_prism_repair_wave.py "$@"
