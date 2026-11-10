#!/usr/bin/env bash
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --tmp=300000
#SBATCH --time=08:00:00
#SBATCH --job-name=nrt-march-boundary-summary-context-repair
#SBATCH --output=forcing/logs/nrt-march-context-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
exec "$HYDRO_OPS_PYTHON" bin/repair_nrt_revision_context.py
