"""Checkpoint-aware small-domain continuation and bounded UCRB 2026 retry."""
# ruff: noqa: DTZ001
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from extend_repaired_retro import WAVES, audit_day, audit_year
from run_cnrfc_hu18_pilot import check_parameters
from run_cnrfc_production import forcing_identity, run
from run_repaired_retro import CAMPAIGN, GATES

from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.wrf_hydro.production import restart_check, restart_names

END = datetime(2026, 4, 17)


def continuation(domain):
    first = 2026 if domain == 'ucrb' else 2020
    start = '20260101' if domain == 'ucrb' else '20201231'
    return first, f'{domain}-extension-{start}-20260417'


def previous_marker(year):
    if year == 2020:
        return '20200101-20201231-passed.json'
    if year == 2021:
        return '20201231-20210101-passed.json'
    return f'{year-1}-passed.json'


def interval(year):
    if not 2020 <= year <= 2026:
        raise ValueError('Year outside approved continuation')
    return (datetime(2020, 12, 31) if year == 2020 else datetime(year, 1, 1),
            min(datetime(year+1, 1, 1), END))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--year', type=int, choices=range(2020, 2027))
    parser.add_argument('--domain', choices=('yampa', 'b120', 'ucrb'), default='yampa')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    domain = args.domain
    first_year, label = continuation(domain)
    if args.year is not None and args.year < first_year:
        parser.error('Year outside domain continuation')
    runs = root/f'nwm/runs/{domain}/retro'/CAMPAIGN
    evidence = root/'nwm/status'/CAMPAIGN/label
    if args.preflight:
        for first in ((2021,) if domain == 'ucrb' else (2011, 2021)):
            if json.loads((root/'forcing/work'/WAVES[first]/'acceptance.json').read_text())['status'] != 'passed':
                raise ValueError('Forcing wave not accepted')
        prior = json.loads((runs/previous_marker(first_year)).read_text())
        if not prior.get('passed') or prior.get('end') != str(interval(first_year)[0]):
            raise ValueError('Previous run not accepted')
        restart_check([Path(p) for p in prior['restarts']], interval(first_year)[0])
        for year in range(first_year, 2027):
            if year == 2020:
                pins = {}
                for day in interval(year):
                    pins.update(audit_day(root, domain, day))
                result = {'status': 'passed', 'identities': pins}
            else:
                result = audit_year(root, domain, year, end=END)
            result.update(start=str(interval(year)[0]), end=str(interval(year)[1]),
                          job_id=os.environ['SLURM_JOB_ID'])
            atomic_json(evidence/f'{year}.json', result)
            print(year, 'forcing provenance verified', flush=True)
        atomic_json(evidence/'acceptance.json', {'status': 'passed', 'end': str(END),
                                               'job_id': os.environ['SLURM_JOB_ID']})
        return
    if args.year is None:
        parser.error('--year is required for model execution')
    first, end = interval(args.year)
    gate = json.loads((evidence/'acceptance.json').read_text())
    if gate.get('status') != 'passed' or gate.get('end') != str(END):
        raise ValueError('Forcing preflight not accepted')
    pins = json.loads((evidence/f'{args.year}.json').read_text())
    for path, identity in pins['identities'].items():
        if forcing_identity(Path(path)) != identity:
            raise ValueError(f'Forcing changed: {path}')
    previous = json.loads((runs/previous_marker(args.year)).read_text())
    if not previous.get('passed') or previous.get('end') != str(first):
        raise ValueError('Previous interval not accepted')
    initial = root/f'nwm/restarts/{domain}/retro'/first.strftime('%Y/%m')
    restart_check([initial/name for name in restart_names(first)], first)
    check_parameters(root/f'nwm/static/domains/{domain}')
    test_gate = root/f'nwm/outputs/{domain}/tests/repaired-retro-start-{GATES[domain]}/acceptance.json'
    run(root, args.year, campaign=CAMPAIGN, archive_gate=test_gate, domain=domain,
        start_date=first.strftime('%Y-%m-%d'), end_date=end.strftime('%Y-%m-%d'),
        initial_restart_dir=initial, continuation_end=END)


if __name__ == '__main__':
    main()
