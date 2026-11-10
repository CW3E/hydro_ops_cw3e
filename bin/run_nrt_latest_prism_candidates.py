"""Gated monthly NRT precipitation candidates; never publish operational data."""
import json
import os
import subprocess
import sys
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    for job in ('5174409', '5174410'):
        p = root/'forcing/work/nrt-latest-prism-repair'/f'job-{job}'/'acceptance.json'
        d = json.loads(p.read_text())
        if d.get('status') != 'passed' or len(d.get('files', [])) != 2:
            raise ValueError('NRT latest-release pilot not passed')
    tasks = [('2026-03-03',29), ('2026-04-01',30), ('2026-05-01',31),
             ('2026-06-01',30), ('2026-07-01',31), ('2026-08-01',31),
             ('2026-09-01',30), ('2026-10-01',28)]
    index = int(os.environ['SLURM_ARRAY_TASK_ID'])
    if not 0 <= index < len(tasks):
        raise ValueError('Task outside approved constrained NRT dates')
    start, days = tasks[index]
    report = {'status': 'running', 'start': start, 'days': days,
              'job_id': os.environ['SLURM_JOB_ID'], 'production_modified': False}
    path = root/'forcing/work/nrt-latest-prism-campaign'/f'task-{index}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        previous = json.loads(path.read_text())
        if os.environ.get('NRT_RETRY_FAILED') != '1' or previous.get('status') != 'failed':
            raise FileExistsError('Inspect previous attempt before rerunning')
        saved = path.with_name(f'task-{index}.failed-{previous["job_id"]}.json')
        if saved.exists():
            raise FileExistsError('Previous failure already archived')
        path.rename(saved)
    atomic_json(path, report)
    try:
        subprocess.run([sys.executable,'bin/test_nrt_latest_prism_repair.py',
                        '--start',start,'--days',str(days)],check=True)
        candidate = root/'forcing/work/nrt-latest-prism-repair'/f"job-{os.environ['SLURM_JOB_ID']}"/'acceptance.json'
        accepted = json.loads(candidate.read_text())
        if accepted.get('status') != 'passed' or len(accepted.get('files',[])) != days:
            raise ValueError('Incomplete monthly candidate acceptance')
        report.update(status='passed', candidate_report=str(candidate))
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        atomic_json(path,report)


if __name__ == '__main__':
    main()
