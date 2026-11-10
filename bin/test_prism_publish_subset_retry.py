#!/usr/bin/env python3
"""Real NetCDF sandbox rehearsal of audited replacement, crash retry and subsets."""
import json
import os
from pathlib import Path

from backfill_cnrfc_forcing import process

from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json, publish, sandbox


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    output = sandbox(root / 'forcing/work/prism-publish-subset-test' / job, root)
    output.mkdir(parents=True, exist_ok=False)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}/publish-test")
    scratch.mkdir(parents=True, exist_ok=False)
    audit = root / 'forcing/work/prism-rain-pipeline/5092471/acceptance.json'
    accepted = json.loads(audit.read_text())
    if accepted['status'] != 'passed':
        raise ValueError('Wet month not accepted')
    selected = {Path(r['path']).name: r for r in accepted['files']}
    report = {'status': 'running', 'production_modified': False, 'days': [],
              'scope': 'sandbox replacement only; originals/restarts retained'}
    atomic_json(output / 'acceptance.json', report)
    try:
        for label in ('20230101', '20230102'):
            filename = label + '.LDASIN_DOMAIN1'
            candidate = Path(selected[filename]['path'])
            original = root / 'forcing/outputs/conus/retro/hourly/2023/01' / filename
            target = output / 'published/conus/retro/hourly/2023/01' / filename
            backup = output / 'originals/2023/01' / filename
            for p in (target, backup):
                p.parent.mkdir(parents=True, exist_ok=True)
                os.link(original, p)
            old_identity = list(fingerprint(original))
            plan = {'target': str(target), 'candidate': str(candidate),
                    'original_snapshot': str(backup), 'old_identity': list(fingerprint(target)),
                    'old_sha256': digest(original), 'new_sha256': selected[filename]['sha256'],
                    'audit': str(audit), 'audit_sha256': digest(audit),
                    'receipt': str(target) + '.repair.json'}
            plan_path = output / (label + '.plan.json')
            atomic_json(plan_path, plan)
            try:
                publish(plan_path, root, interrupt_after_replace=True)
            except RuntimeError as error:
                if str(error) != 'Injected interruption after replacement':
                    raise
            else:
                raise AssertionError('Fault injection did not occur')
            before_retry = fingerprint(target)
            result = publish(plan_path, root)
            if not result.get('recovered_after_replace') or fingerprint(target) != before_retry:
                raise AssertionError('Retry should finalize receipt without replacing data again')
            if publish(plan_path, root)['status'] != 'already_published':
                raise AssertionError('Duplicate retry not skipped')
            day = {'day': label, 'publication': result, 'subsets': {}}
            for domain in ('cnrfc', 'b120', 'ucrb', 'yampa'):
                mask = root / f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
                child = output / f'published/{domain}/retro/hourly/2023/01' / filename
                task = (target, child, mask, digest(mask), scratch, list(fingerprint(target)))
                first = process(task)
                if first['status'] != 'published':
                    raise RuntimeError(f'Subset failed: {first}')
                before = fingerprint(child)
                retry = process(task)
                if retry['status'] != 'skipped' or fingerprint(child) != before:
                    raise AssertionError('Subset retry changed accepted output')
                day['subsets'][domain] = {'first': first, 'retry': retry}
            if list(fingerprint(original)) != old_identity or digest(original) != plan['old_sha256']:
                raise AssertionError('Production original changed')
            day['original_preserved'] = True
            day['hourly_subsets_ready'] = True
            day['summaries_pending'] = True
            report['days'].append(day)
            atomic_json(output / 'acceptance.json', report)
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(output / 'acceptance.json', report)


if __name__ == '__main__':
    main()
