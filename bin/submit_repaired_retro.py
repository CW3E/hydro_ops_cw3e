"""Submit audited old-data cleanup followed by four independent 1979–2000 chains."""
import argparse
import json
import os
import subprocess
from pathlib import Path

from run_repaired_retro import CAMPAIGN, GATES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submit', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    ledger = root/'nwm/status'/CAMPAIGN/'submission.json'
    if ledger.exists():
        raise ValueError('Submission ledger exists; inspect rather than duplicate chains')
    for domain, job in GATES.items():
        record = json.loads((root/'nwm/outputs'/domain/'tests'/
                             f'repaired-retro-start-{job}'/'acceptance.json').read_text())
        if record.get('status') != 'passed' or record.get('restart_equivalence') != 'passed':
            raise ValueError(f'Gate failed: {domain}')
    if not args.submit:
        print('Cleanup + 88 annual jobs, 1979–2000; 64/64/32/32 ranks. Year 2000 also waits for 5133400.')
        return
    env = dict(os.environ)
    env.pop('SBATCH_RESERVATION', None)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open('x') as f:
        f.write('[]\n')
    rows = []

    def submit(command, **details):
        job = subprocess.check_output(['sbatch', '--parsable', *command], cwd=root,
                                      env=env, text=True).strip().split(';')[0]
        rows.append(dict(job_id=job, command=command, **details))
        temporary = ledger.with_suffix('.tmp')
        temporary.write_text(json.dumps(rows, indent=2)+'\n')
        temporary.replace(ledger)
        print(json.dumps(rows[-1]), flush=True)
        return job

    cleanup = submit(['--job-name=wrfh-four-domains-delete-obsolete-results',
                      'slurm/cleanup_old_subdomain_results.sh'], stage='cleanup')
    for domain, ranks in [('cnrfc', 64), ('ucrb', 64), ('b120', 32), ('yampa', 32)]:
        previous = cleanup
        for year in range(1979, 2001):
            dependencies = previous + (':5133400' if year == 2000 else '')
            previous = submit([f'--dependency=afterok:{dependencies}', f'--ntasks={ranks}',
                               f'--job-name=wrfh-{domain}-corrected-rain-retro-{year}-monthly-rst',
                               'slurm/run_repaired_retro.sh', domain, str(year)],
                              domain=domain, year=year, ranks=ranks)


if __name__ == '__main__':
    main()
