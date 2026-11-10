#!/usr/bin/env python3
"""Submit the 2001–2010 hourly repair and gated rain-only summary campaign."""
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
    wave = root/'forcing/work/prism-repair-wave5-200101-201012'
    previous = root/'forcing/work/prism-repair-wave4-199101-200012'
    gate = json.loads((root/'forcing/work/rain-summary-publication-test/job-5118047/acceptance.json').read_text())
    if not (gate.get('status') == 'passed' and gate.get('idempotent_retry_verified')
            and gate.get('all_variables_reference_equal') and gate.get('published_files') == 160):
        raise ValueError('Rain-only summary publication integration gate failed')
    old = json.loads((previous/'plan.json').read_text())
    if (old.get('status') != 'prepared' or len(old.get('months', [])) != 120
            or old['months'][-1]['start'] != '2000-12-01'):
        raise ValueError('Preceding wave preparation missing/incompatible')
    # Its acceptance is still pending: preparation is gated by afterok:5118063
    # and prism_repair_wave.py rechecks the passed report at execution time.
    plan = {'status': 'planned', 'wave': str(wave), 'jobs': [],
            'start': '2001-01-01', 'end': '2010-12-31', 'months': 120, 'days': 3652,
            'repair_concurrency': 6, 'repair_cpus_per_task': 64,
            'publication_concurrency': 8, 'publication_cpus_per_task': 16,
            'publication_day_workers': 4, 'summary_cpus_per_year': 16,
            'summary_start': '2000-12-01', 'summary_end': '2010-12-30',
            'deferred_daily': ['2010-12-31'], 'deferred_monthly': ['2010-12'],
            'old_campaigns_modified': False}
    if not args.submit:
        print(json.dumps(plan, indent=2))
        return
    directory = root/'forcing/work/repair-submissions'
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory/'2001-2010.json'
    with (directory/'2001-2010.lock').open('a') as lock:
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
        prep = submit('prepare', ['--job-name=prism-rain-2001-2010-freeze-originals',
                      '--cpus-per-task=16', '--tmp=40000', '--time=00:30:00'], script,
                      ['prepare', '--wave', str(wave), '--start-month', '2001-01-01',
                       '--end-month', '2010-12-01', '--previous-wave', str(previous),
                       '--previous-array-job', '5118062'], '5118063')
        repair = submit('repair', ['--job-name=prism-rain-repair-200101-201012-six-month-workers',
                        '--array=0-119%6'], script, ['run', '--wave', str(wave)], prep)
        audit = submit('audit', ['--job-name=prism-rain-2001-2010-boundary-audit',
                       '--cpus-per-task=16', '--tmp=40000', '--time=02:00:00'], script,
                       ['summarize', '--wave', str(wave), '--array-job', repair], repair)
        pubargs = ['--wave-dir', str(wave), '--array-job', repair, '--workers', '4']
        canary = submit('publication_canary', ['--job-name=prism-rain-2001-four-day-publish-canary',
                        '--time=02:00:00'], 'slurm/publish_prism_repair_wave.sh',
                        [*pubargs, '--start', '2001-01-01', '--end', '2001-01-04'], audit+':5118065')
        publication = submit('publication', ['--job-name=prism-rain-publish-200101-201012-four-day-workers',
                              '--array=0-119%8', '--time=06:00:00'],
                              'slurm/publish_prism_repair_wave.sh', [*pubargs, '--array-month'], canary)
        for year in range(2000, 2011):
            dependency = publication + (':5118076' if year == 2000 else '')
            submit(f'summaries_{year}', [f'--job-name=rain-only-summaries-and-four-domain-subsets-{year}'],
                   'slurm/run_rain_summary_year.sh',
                   ['--year', str(year), '--start', '2000-12-01', '--end', '2010-12-30',
                    '--workspace', str(wave/'summary-production')], dependency)
        plan['status'] = 'submitted'
        atomic_json(manifest, plan)
        print(json.dumps(plan, indent=2))


if __name__ == '__main__':
    main()
