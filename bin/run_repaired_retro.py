"""Yearly corrected-forcing reruns, initialized only from the new domain spinups."""
# ruff: noqa: DTZ001
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from run_cnrfc_hu18_pilot import check_parameters, digest
from run_cnrfc_production import run

from hydro_ops.wrf_hydro.production import restart_names

CAMPAIGN = 'repaired_rain_retro_v1'
GATES = {'cnrfc': '5150178', 'ucrb': '5150179', 'b120': '5150180', 'yampa': '5150181'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain', choices=tuple(GATES))
    parser.add_argument('year', type=int, choices=range(1979, 2001))
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    cleanup = json.loads((root/'nwm/status'/CAMPAIGN/'cleanup.json').read_text())
    if cleanup.get('status') != 'passed':
        raise ValueError('Old-result cleanup not accepted')
    check_parameters(root/'nwm/static/domains'/args.domain)
    gate = root/'nwm/outputs'/args.domain/'tests'/f'repaired-retro-start-{GATES[args.domain]}'/'acceptance.json'
    record = json.loads(gate.read_text())
    if record.get('status') != 'passed' or record.get('restart_equivalence') != 'passed':
        raise ValueError('Startup tests not accepted')
    if args.year == 1979:
        initial = gate.parent/'initialization'
        if {n: digest(initial/n) for n in restart_names(datetime(1979, 1, 2))} != record['initial_restart_sha256']:
            raise ValueError('Tested initialization changed')
    else:
        previous = json.loads((root/'nwm/runs'/args.domain/'retro'/CAMPAIGN/f'{args.year-1}-passed.json').read_text())
        if not previous.get('passed') or previous.get('end') != str(datetime(args.year, 1, 1)):
            raise ValueError('Previous year not accepted')
        initial = root/'nwm/restarts'/args.domain/'retro'/str(args.year)/'01'
    run(root, args.year, campaign=CAMPAIGN, archive_gate=gate,
        initial_restart_dir=initial, domain=args.domain)


if __name__ == '__main__':
    main()
