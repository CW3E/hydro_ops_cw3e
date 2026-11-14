"""Use real revised source records against v6; no forced dates or remote writes."""
import json
import os
import time
from datetime import date
from pathlib import Path

import build_web_daily_collection as cycle
import numpy as np
from PIL import Image

from hydro_ops.web.globus_transfer import atomic_json


def compare(base, publication):
    comparisons = []
    for domain in ('cnrfc', 'ucrb'):
        old = {r['id']: r for r in cycle.read(base / domain / 'availability.json')['assets']}
        for row in cycle.read(publication / domain / 'availability.json')['assets']:
            previous = old[row['id']]
            with Image.open(base / domain / previous['href']) as im:
                before = np.asarray(im.convert('RGBA'))
            with Image.open(publication / domain / row['href']) as im:
                after = np.asarray(im.convert('RGBA'))
            comparisons.append({'domain': domain, 'id': row['id'], 'variable': row['variable'],
                'date': row['date'], 'source': row['source'],
                'sourceContentChanged': previous['sourceSha256'] != row['sourceSha256'],
                'pngChanged': previous['sha256'] != row['sha256'],
                'changedPixels': int(np.count_nonzero(np.any(before != after, axis=2))),
                'oldSourceSha256': previous['sourceSha256'], 'newSourceSha256': row['sourceSha256'],
                'oldPngSha256': previous['sha256'], 'newPngSha256': row['sha256']})
    return comparisons


def main():
    root = Path(__file__).resolve().parents[1]
    job = os.environ['SLURM_JOB_ID']
    if not job.isdecimal():
        raise ValueError('SLURM required')
    work = root / 'web/work/daily-tests' / f'real-revision-job-{job}'
    work.mkdir(parents=True, exist_ok=False)
    parent = root / 'web/publications/collection-v6-mexico-sm-20251001-20261111'
    report = {'status': 'running', 'job_id': job, 'start': '2026-11-01', 'end': '2026-11-03',
              'test': True, 'remoteWrites': False, 'generations': []}
    atomic_json(work / 'report.json', report)
    try:
        for generation in (1, 2):
            started = time.monotonic()
            output = work / f'generation-{generation}'
            cycle.prepare(parent, output, date(2026, 11, 1), date(2026, 11, 3), set(), [])
            plan = cycle.read(output / 'cycle.json')
            for task in range(plan['tasks']):
                cycle.worker(output, task)
            cycle.finalize(output)
            rows = compare(parent, output / 'publication')
            atomic_json(output / 'comparison.json', rows)
            summary = {'generation': generation, 'elapsedSeconds': time.monotonic() - started,
                'changedSources': plan['changedSources'], 'reusedImages': len(plan['reused']),
                'sourceChangedAssets': sum(r['sourceContentChanged'] for r in rows),
                'changedPngs': sum(r['pngChanged'] for r in rows),
                'changedPixels': sum(r['changedPixels'] for r in rows), 'images': len(rows)}
            report['generations'].append(summary)
            atomic_json(work / 'report.json', report)
            if generation == 1:
                if not (summary['sourceChangedAssets'] and summary['changedPixels']
                        and summary['reusedImages']):
                    raise ValueError('Test must exercise real content/pixel changes and unchanged reuse')
            elif plan['tasks'] or summary['changedPngs'] or summary['reusedImages'] != len(rows):
                raise ValueError('Unchanged repeat must reuse every image with no render tasks')
            parent = output / 'publication'
        report['status'] = 'passed'
        atomic_json(work / 'acceptance.json', report)
    except BaseException as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        atomic_json(work / 'report.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
