#!/usr/bin/env bash
# One-shot lightweight login-node waiter, not a recurring cron task.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/project_environment.sh"
cd "$HYDRO_OPS_PROJECT_ROOT"
job=${1:?cleanup job ID required}
[[ "$job" =~ ^[0-9]+$ ]] || exit 2
exec 9>forcing/status/cleanup/historical-inventory-submit.lock
flock -n 9 || exit 3
receipt="forcing/status/cleanup/historical-inventory-after-${job}.submission.txt"
[[ ! -e "$receipt" ]] || { echo "Submission receipt already exists"; exit 4; }
# The cleanup rejects unknown live scheduler entries, even pending ones. Wait
# outside SLURM rather than adding a dependency that would trip that guard.
deadline=$((SECONDS + 18000))
while [[ -n "$(squeue -h -j "$job" -o '%i')" ]]; do
  (( SECONDS < deadline )) || { echo "Timed out waiting for cleanup"; exit 5; }
  sleep 30
done
"$HYDRO_OPS_PYTHON" - "$job" <<'PY'
import json
import sys
from pathlib import Path
path = Path('forcing/status/cleanup') / f'repair-originals-deletion-{sys.argv[1]}.jsonl'
last = None
with path.open() as stream:
    for line in stream:
        last = json.loads(line)
if not last or last.get('event') != 'completed':
    raise SystemExit('Cleanup has no completed journal; inventory submission requires review')
PY
env -u SBATCH_RESERVATION sbatch --parsable \
  --job-name=historical-work-readonly-inventory \
  slurm/inventory_historical_work.sh | tee "$receipt"
