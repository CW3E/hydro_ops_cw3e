#!/usr/bin/env bash
#SBATCH --partition=compute-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --tmp=120G
#SBATCH --time=04:00:00
#SBATCH --output=forcing/logs/nrt-calendar-receipt-repair-%j.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
exec "$HYDRO_OPS_PYTHON" bin/repair_nrt_calendar_receipts.py "$@"
