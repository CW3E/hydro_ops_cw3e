#!/usr/bin/env python3
"""Submit the 2021–April 17, 2026 hourly repair and gated rain-only summary campaign."""
import argparse
import fcntl
import json
import os
import subprocess
from pathlib import Path

from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submit', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    wave = root/'forcing/work/prism-repair-wave7-202101-20260417'
    previous = root/'forcing/work/prism-repair-wave6-201101-202012'
    gate = json.loads((root/'forcing/work/rain-summary-publication-test/job-5118047/acceptance.json').read_text())
    if not (gate.get('status') == 'passed' and gate.get('idempotent_retry_verified')
            and gate.get('all_variables_reference_equal') and gate.get('published_files') == 160):
        raise ValueError('Rain-only summary publication integration gate failed')
    old = json.loads((previous/'plan.json').read_text())
    if (old.get('status') != 'prepared' or len(old.get('months', [])) != 120
            or old['months'][-1]['start'] != '2020-12-01'):
        raise ValueError('Preceding wave preparation missing/incompatible')
    audit_record = json.loads((previous/'acceptance.json').read_text())
    if audit_record.get('status') != 'passed' or audit_record.get('candidate_files') != 3653:
        raise ValueError('Previous repair audit has not passed')
    reports = [json.loads(p.read_text()) for p in (previous/'production').glob('job-*.json')]
    published = {r['day'] for d in reports if d.get('status') == 'passed' for r in d.get('days', [])}
    if len(published) != 3653 or any(d.get('status') != 'passed' for d in reports):
        raise ValueError('Previous publication is incomplete')
    summary = json.loads((previous/'summary-production/2020-job-5160155.json').read_text())
    if summary.get('status') != 'passed':
        raise ValueError('Preceding 2020 summary writer has not finished')
    plan = {'status': 'planned', 'wave': str(wave), 'jobs': [],
            'start': '2021-01-01', 'end': '2026-04-17', 'months': 64, 'days': 1933,
            'repair_concurrency': 6, 'repair_cpus_per_task': 64,
            'publication_concurrency': 8, 'publication_cpus_per_task': 16,
            'publication_day_workers': 4, 'summary_cpus_per_year': 16,
            'summary_start': '2020-12-01', 'summary_end': '2026-04-16',
            'deferred_daily': ['2026-04-17'], 'deferred_monthly': ['2026-04'],
            'old_campaigns_modified': False}
    if not args.submit:
        print(json.dumps(plan, indent=2))
        return
    directory = root/'forcing/work/repair-submissions'
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory/'2021-2026.json'
    with (directory/'2021-2026.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if manifest.exists() or wave.exists():
            raise FileExistsError('Campaign already exists; inspect ledger before retrying')
        plan['status'] = 'submitting'
        atomic_json(manifest, plan)

        def submit(stage, options, script, arguments, dependency=None):
            cmd = ['sbatch', '--parsable', *options]
            if dependency:
                cmd.append('--dependency=afterok:'+dependency)
            cmd += [script, *arguments]
            row = {'stage': stage, 'command': cmd, 'status': 'submission_pending'}
            plan['jobs'].append(row)
            atomic_json(manifest, plan)
            response = subprocess.check_output(cmd, cwd=root, text=True,
                                               env={k: v for k, v in os.environ.items()
                                                    if k != 'SBATCH_RESERVATION'}).strip()
            job = response.split(';')[0]
            if not job.isdigit():
                raise ValueError(f'Uncertain sbatch response: {response}')
            row.update(status='submitted', job_id=job)
            atomic_json(manifest, plan)
            return job

        script = 'slurm/prism_repair_wave.sh'
        prep = submit('prepare', ['--job-name=prism-rain-2021-2026-freeze-originals',
                      '--cpus-per-task=16', '--tmp=40000', '--time=00:30:00'], script,
                      ['prepare', '--wave', str(wave), '--start-month', '2021-01-01',
                       '--end-month', '2026-04-01', '--end-date', '2026-04-17', '--previous-wave', str(previous),
                       '--previous-array-job', '5160141'])
        repair = submit('repair', ['--job-name=prism-rain-repair-202101-20260417-six-month-workers',
                        '--array=0-63%6'], script, ['run', '--wave', str(wave)], prep)
        audit = submit('audit', ['--job-name=prism-rain-2021-2026-boundary-audit',
                       '--cpus-per-task=16', '--tmp=40000', '--time=02:00:00'], script,
                       ['summarize', '--wave', str(wave), '--array-job', repair], repair)
        pubargs = ['--wave-dir', str(wave), '--array-job', repair, '--workers', '4']
        canary = submit('publication_canary', ['--job-name=prism-rain-2021-four-day-publish-canary',
                        '--time=02:00:00'], 'slurm/publish_prism_repair_wave.sh',
                        [*pubargs, '--start', '2021-01-01', '--end', '2021-01-04'], audit)
        publication = submit('publication', ['--job-name=prism-rain-publish-202101-20260417-four-day-workers',
                              '--array=0-63%8', '--time=06:00:00'],
                              'slurm/publish_prism_repair_wave.sh', [*pubargs, '--array-month'], canary)
        for year in range(2020, 2027):
            dependency = publication
            submit(f'summaries_{year}', [f'--job-name=rain-only-summaries-and-four-domain-subsets-{year}'],
                   'slurm/run_rain_summary_year.sh',
                   ['--year', str(year), '--start', '2020-12-01', '--end', '2026-04-16',
                    '--workspace', str(wave/'summary-production')], dependency)
        plan['status'] = 'submitted'
        atomic_json(manifest, plan)
        print(json.dumps(plan, indent=2))


if __name__ == '__main__':
    main()
