#!/usr/bin/env bash
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --tmp=120000
#SBATCH --time=03:00:00
#SBATCH --job-name=retro-mar-apr-envelope-reassembly
#SBATCH --output=forcing/logs/retro-envelope-reassembly-%A_%a.out
set -euo pipefail
source "${SLURM_SUBMIT_DIR:?}/bin/project_environment.sh"
exec "$HYDRO_OPS_PYTHON" bin/reassemble_retro_envelope.py "$@"
