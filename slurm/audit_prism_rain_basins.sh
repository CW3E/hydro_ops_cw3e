#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --tmp=20000
#SBATCH --time=02:00:00
#SBATCH --job-name=prism-wet-month-basin-totals-audit
#SBATCH --output=forcing/logs/prism-basin-audit-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
base_python="$(dirname "$(dirname "$HYDRO_OPS_PYTHON")")/../../bin/python"
exec "$base_python" bin/audit_prism_rain_basins.py "$@"
