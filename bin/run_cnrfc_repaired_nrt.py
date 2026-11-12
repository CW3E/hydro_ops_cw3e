"""Gate CNRFC NRT continuation on the corrected 2026 Retro terminal state."""
import argparse
import fcntl
import os
from pathlib import Path

from netCDF4 import Dataset, num2date
from refresh_thermal_model_2026 import (
    CAMPAIGN,
    PUBLICATION,
    audit_lineage,
    execute,
    interval,
    load,
)
from run_cnrfc_hu18_pilot import check_parameters, digest
from run_repaired_retro import GATES

from hydro_ops.wrf_hydro.nrt_recovery import file_identity
from hydro_ops.wrf_hydro.production import restart_check, restart_names, write_json

STATE = 'nwm/status/cnrfc_repaired_nrt_20260417_20261111_v1'
PREVIOUS = ('nwm/runs/cnrfc/retro/repaired_rain_retro_v1/'
            '20260101-20260417-passed.json')


def validate_retro(record):
    start, _ = interval('nrt', 'cnrfc')
    if (record.get('passed') is not True or record.get('stream') != 'retro'
            or record.get('start') != '2026-01-01 00:00:00'
            or record.get('end') != str(start)):
        raise ValueError('Corrected CNRFC Retro terminal acceptance is required')


def prepare(root, state):
    state.mkdir(parents=True, exist_ok=True)
    with (state/'prepare.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (state/'plan.json').exists():
            raise ValueError('Existing plan; inspect before retrying preparation')
        start, end = interval('nrt', 'cnrfc')
        previous = load(root/PREVIOUS)
        validate_retro(previous)
        initial = root/'nwm/restarts/cnrfc/retro/2026/04'
        pair = [initial/n for n in restart_names(start)]
        if previous.get('restarts') != [str(p) for p in pair]:
            raise ValueError('Unexpected terminal restart pair')
        restart_check(pair, start)
        check_parameters(root/'nwm/static/domains/cnrfc')
        publication = load(root/PUBLICATION)
        if publication.get('status') != 'passed' or publication.get('job') != '5232861':
            raise ValueError('Thermal forcing publication must pass')
        gate = root/f'nwm/outputs/cnrfc/tests/repaired-retro-start-{GATES["cnrfc"]}/acceptance.json'
        old = load(gate)
        if old.get('status') != 'passed' or old.get('restart_equivalence') != 'passed':
            raise ValueError('Corrected-model startup gate failed')
        lineage = audit_lineage(root, 'cnrfc', 'nrt', start, end)
        terminal = root/'forcing/outputs/cnrfc/nrt/hourly'/end.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        with Dataset(terminal) as ds:
            t = ds['time']
            stamps = num2date(t[:], t.units, calendar=getattr(t, 'calendar', 'standard'),
                             only_use_cftime_datetimes=False)
            if end not in stamps:
                raise ValueError('NRT terminal midnight forcing is missing')
        mask = root/'nwm/static/domains/cnrfc/masks/cnrfc_masks.nc'
        pins = {str(p): file_identity(p) for p in pair}
        plan = {'authorized': True, 'campaign': CAMPAIGN, 'publication': PUBLICATION,
                'publication_sha256': digest(root/PUBLICATION),
                'predecessor': PREVIOUS, 'predecessor_sha256': digest(root/PREVIOUS),
                'start': str(start), 'end': str(end), 'cron_changed': False,
                'domains': {'cnrfc': {'ranks': 64, 'initial_pins': pins,
                    'mask': str(mask), 'mask_sha256': digest(mask),
                    'startup_gate': str(gate), 'startup_gate_sha256': digest(gate)}}}
        write_json(state/'preflight-lineage.json', {'status': 'passed', 'identities': lineage})
        write_json(state/'plan.json', plan)
        write_json(state/'cnrfc/retro/acceptance.json', {
            'status': 'complete', 'domain': 'cnrfc', 'stream': 'retro', 'end': str(start),
            'plan_sha256': digest(state/'plan.json'), 'terminal_restart_pins': pins,
            'inherited_acceptance': str(root/PREVIOUS), 'model_job': '5202844'})
        write_json(state/'prepare-acceptance.json', {
            'status': 'passed', 'job': os.environ['SLURM_JOB_ID'],
            'start': str(start), 'end': str(end), 'forcing_files': len(lineage)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('prepare', 'run'), required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    state = root/STATE
    if args.stage == 'prepare':
        prepare(root, state)
    else:
        plan = load(state/'plan.json')
        if (load(state/'prepare-acceptance.json').get('status') != 'passed'
                or digest(root/PREVIOUS) != plan['predecessor_sha256']):
            raise ValueError('Preflight or Retro predecessor changed')
        validate_retro(load(root/PREVIOUS))
        execute(root, state, 'cnrfc', 'nrt')


if __name__ == '__main__':
    main()
