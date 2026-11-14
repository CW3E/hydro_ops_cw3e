"""Isolated real daily extension, month rollover, and controlled monthly revision."""
import argparse
import json
import os
import shutil
import time
from datetime import date
from unittest.mock import patch

import build_web_daily_collection as cycle
import netCDF4
import numpy as np

from hydro_ops.web.globus_transfer import atomic_json, digest


def assets(folder):
    return {(d, r['id']): r for d in ('cnrfc', 'ucrb')
            for r in cycle.read(folder / d / 'availability.json')['assets']}


def build(parent, output, start, end, overrides=None):
    started = time.monotonic()
    original = cycle.renderer.plan

    def plan_with_test_sources(*args, **kwargs):
        original(*args, **kwargs)
        path = args[0] / 'plan.json'
        spec = cycle.read(path)
        for task in spec['tasks']:
            for item in task['items']:
                replacement = (overrides or {}).get(item['source'])
                if replacement:
                    item['source'] = str(replacement.relative_to(cycle.ROOT))
                    spec['pins'][item['source']] = cycle.renderer.identity(replacement)
        atomic_json(path, spec)

    with patch.object(cycle.renderer, 'plan', plan_with_test_sources):
        cycle.prepare(parent, output, start, end, set(), [])
    spec = cycle.read(output / 'cycle.json')
    for task in range(spec['tasks']):
        cycle.worker(output, task)
    cycle.finalize(output)
    publication = output / 'publication'
    old, new = assets(parent), assets(publication)
    shared = set(old) & set(new)
    comparison = {'newImages': len(set(new) - set(old)), 'removedImages': len(set(old) - set(new)),
                  'changedExisting': [list(k) for k in sorted(shared) if old[k]['sha256'] != new[k]['sha256']],
                  'reusedImages': len(spec['reused']), 'changedSources': spec['changedSources'],
                  'tasks': spec['tasks'], 'images': len(new), 'elapsedSeconds': time.monotonic() - started}
    atomic_json(output / 'comparison.json', comparison)
    return publication, comparison


def main(mode):
    root = cycle.ROOT
    job = os.environ['SLURM_JOB_ID']
    if not job.isdecimal():
        raise ValueError('SLURM required')
    work = root / 'web/work/daily-tests' / f'{mode}-job-{job}'
    work.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'job_id': job, 'mode': mode, 'remoteWrites': False, 'stages': []}
    atomic_json(work / 'report.json', report)

    def stage(label, parent, start, end, overrides=None):
        result, summary = build(parent, work / label, start, end, overrides)
        report['stages'].append({'label': label, **summary})
        atomic_json(work / 'report.json', report)
        return result, summary

    try:
        if mode == 'extension':
            parent = root / 'web/work/daily-tests/job-5308854/generation-2/publication'
            result, summary = stage('extend', parent, date(2026, 11, 10), date(2026, 11, 12))
            if summary['newImages'] != 8:
                raise ValueError('Expected eight new daily images')
            # Old dates may legitimately have changed since the prior test; report them.
            _, repeat = stage('repeat', result, date(2026, 11, 10), date(2026, 11, 12))
        else:
            parent = root / 'web/publications/collection-v6-mexico-sm-20251001-20261111'
            baseline, _ = stage('before-month-end', parent, date(2025, 10, 1), date(2025, 10, 30))
            if any(r['resolution'] == 'monthly' for r in assets(baseline).values()):
                raise ValueError('Incomplete month must not be published')
            complete, summary = stage('month-end', baseline, date(2025, 10, 1), date(2025, 10, 31))
            if summary['newImages'] != 12:
                raise ValueError('Expected eight daily and four monthly images at month end')
            overrides, fixtures = {}, []
            for domain in ('cnrfc', 'ucrb'):
                row = next(r for (d, _), r in assets(complete).items()
                           if d == domain and r['resolution'] == 'monthly')
                source = root / row['source']
                copy = work / 'fixtures' / domain / source.name
                copy.parent.mkdir(parents=True)
                shutil.copy2(source, copy)  # Never hardlink a file that will be edited.
                with netCDF4.Dataset(copy, 'r+') as ds:
                    for name, offset, factor in [('T2D', 3., 1.), ('RAINRATE', 0., 1.5)]:
                        values = np.ma.asarray(ds[name][:])
                        ds[name][:] = values * factor + offset
                    ds.setncattr('hydro_ops_test_fixture', 'Synthetic monthly revision: T2D +3 K, RAINRATE x1.5; not operational data')
                overrides[row['source']] = copy
                fixtures.append({'source': row['source'], 'originalSha256': digest(source),
                    'fixture': str(copy.relative_to(root)), 'fixtureSha256': digest(copy)})
            atomic_json(work / 'fixtures.json', {'synthetic': True, 'files': fixtures})
            revised, summary = stage('monthly-revision', complete, date(2025, 10, 1), date(2025, 10, 31), overrides)
            revised_assets = assets(revised)
            if summary['changedSources'] != 2 or summary['reusedImages'] != 248 or not summary['changedExisting']:
                raise ValueError('Monthly-only revision must rebuild two sources and reuse 248 daily PNGs')
            if any(revised_assets[tuple(k)]['resolution'] != 'monthly' for k in summary['changedExisting']):
                raise ValueError('Monthly-only change modified daily PNGs')
            _, repeat = stage('repeat', revised, date(2025, 10, 1), date(2025, 10, 31), overrides)
            for fixture in fixtures:
                if digest(root / fixture['source']) != fixture['originalSha256']:
                    raise ValueError('Production source changed during test')
        if repeat['tasks'] or repeat['newImages'] or repeat['changedExisting'] or repeat['reusedImages'] != repeat['images']:
            raise ValueError('Unchanged repeat must reuse everything')
        report['status'] = 'passed'
        atomic_json(work / 'acceptance.json', report)
    except BaseException as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        atomic_json(work / 'report.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('extension', 'monthly'))
    main(parser.parse_args().mode)
