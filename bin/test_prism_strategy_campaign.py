#!/usr/bin/env python3
"""Stratified, isolated real-data PRISM fallback/calendar regression campaign."""

import argparse
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

CASES = [f'{year}-{month:02d}-15' for year in (1981, 2002, 2011, 2014, 2016, 2023)
         for month in (1, 4, 7, 10)] + ['2000-02-28', '2012-02-28', '2015-12-31', '2020-06-30']


def summarize(root, campaign):
    directory = root / 'forcing/work/prism-fallback-tests' / campaign
    blocks, windows = [], []
    for start in CASES:
        acceptance = directory / f'calendar-{start}/acceptance.json'
        execution = directory / f'{start}.execution.json'
        entry = {'start': start, 'status': 'missing_report'}
        if acceptance.exists():
            entry.update(json.loads(acceptance.read_text()))
        if execution.exists():
            entry['returncode'] = json.loads(execution.read_text())['returncode']
        blocks.append(entry)
        for offset in range(3):
            day = str(date.fromisoformat(start) + timedelta(days=offset))
            path = directory / f'{day}.json'
            result = json.loads(path.read_text()) if path.exists() else {'status': 'missing_report'}
            windows.append({'day': day, 'status': result['status'],
                            'local_fallback': result.get('local_fallback'),
                            'roundtrip': result.get('roundtrip'),
                            'weak_support': result.get('weak_support'),
                            'elapsed_seconds': result.get('elapsed_seconds')})
    passed = sum(b['status'] == 'passed' and b.get('returncode') == 0 for b in blocks)
    report = {'campaign': campaign, 'status': 'passed' if passed == len(CASES) else 'needs_review',
              'expected_blocks': len(CASES), 'passed_blocks': passed,
              'expected_windows': len(CASES)*3,
              'passed_windows': sum(w['status'] == 'passed' for w in windows),
              'fallback_applied_windows': sum(bool((w['local_fallback'] or {}).get('applied'))
                                              for w in windows),
              'scope': 'CNRFC complete-support cells; rainfall-only diagnostic',
              'production_modified': False, 'blocks': blocks, 'windows': windows}
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'campaign-summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('blocks', 'windows')}, indent=2))
    return 0 if passed == len(CASES) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', help='Campaign array job ID to audit, including failed tasks')
    parser.add_argument('--weak-support', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    if args.summary:
        return summarize(root, args.summary)
    start = CASES[int(os.environ['SLURM_ARRAY_TASK_ID'])]
    result = subprocess.run([sys.executable, str(root / 'bin/test_prism_calendar_strategy.py'),
                             start, '--local-fallback',
                             *(['--weak-support'] if args.weak_support else [])], check=False)
    directory = root / 'forcing/work/prism-fallback-tests' / os.environ['SLURM_ARRAY_JOB_ID']
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{start}.execution.json').write_text(json.dumps({
        'start': start, 'returncode': result.returncode,
        'job_id': os.environ['SLURM_JOB_ID'],
        'array_task_id': os.environ['SLURM_ARRAY_TASK_ID'],
    }, indent=2) + '\n')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
