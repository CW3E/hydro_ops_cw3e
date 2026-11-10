"""Classify the exact outstanding revision queue without modifying production."""
import json
import os
from collections import Counter
from datetime import date
from pathlib import Path

from run_bounded_revision_queue import missing_publication_context

from hydro_ops.forcing.prism_replay import plan
from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    state = json.loads((root/'forcing/status/prism-revisions/queue.json').read_text())
    report = plan(state, root)
    for item in report['items']:
        item['missing_context'] = missing_publication_context(root, item['stream'], date.fromisoformat(item['day']))
        item['eligible'] = item['status'] != 'blocked_sources' and not item['missing_context']
    report['counts'] = dict(Counter(f"{i['stream']}/{'eligible' if i['eligible'] else 'deferred'}"
                                    for i in report['items']))
    job = os.environ['SLURM_JOB_ID']
    target = root/f'forcing/work/revision-backlog-audit/{job}/acceptance.json'
    atomic_json(target, report)
    print(json.dumps({'report': str(target), 'counts': report['counts'], 'production_modified': False}))


if __name__ == '__main__':
    main()
