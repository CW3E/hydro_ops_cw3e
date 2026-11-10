#!/usr/bin/env python3
"""Output-free, monthly-checkpoint subdomain spin-up on repaired 1981–1985 forcing."""
# Model helper clocks use timezone-naive UTC.
# ruff: noqa: DTZ001
import argparse
import fcntl
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from prepare_cnrfc_hu18_test import digest, identity
from run_cnrfc_hu18_pilot import check_parameters

from hydro_ops.nwm_masks import load_masks
from hydro_ops.wrf_hydro.production import (
    check_active_state,
    check_forcing,
    copy_atomic,
    dates,
    months,
    namelist,
    restart_check,
    restart_names,
    write_json,
)

CAMPAIGN = 'repaired_rain_spinup_v1'
GATES = {'cnrfc': '5106713', 'b120': '5106714', 'ucrb': '5106715', 'yampa': '5106716'}
TABLES = ('namelist.hrldas', 'hydro.namelist', 'CHANPARM.TBL', 'GENPARM.TBL',
          'HYDRO.TBL', 'MPTABLE.TBL', 'SOILPARM.TBL')
LAND_STATES = ('SMC', 'SH2O', 'SOIL_T', 'SNEQV')


def settings(begin, stop, forcing, inputs):
    hours = int((stop - begin).total_seconds() / 3600)
    land = {
        'INDIR': repr(str(forcing)), 'OUTDIR': "'./'", 'START_YEAR': begin.year,
        'START_MONTH': begin.month, 'START_DAY': begin.day, 'START_HOUR': 0, 'START_MIN': 0,
        'KHOUR': hours, 'PCP_PARTITION_OPTION': 1,
        'RESTART_FILENAME_REQUESTED': repr(str(inputs[0])) if inputs else "' '",
        'RESTART_FREQUENCY_HOURS': hours, 'OUTPUT_TIMESTEP': 3600, 'SPLIT_OUTPUT_COUNT': 1,
    }
    hydro = {
        'RESTART_FILE': repr(str(inputs[1])) if inputs else "' '",
        'rst_dt': hours * 60, 'rst_typ': 1 if inputs else 0,
        'RSTRT_SWC': 0 if inputs else 1, 'GW_RESTART': 1 if inputs else 0,
        't0OutputFlag': 0, 'CHRTOUT_DOMAIN': 0, 'CHRTOUT_HOURLY': 0, 'CHRTOUT_DAILY': 0,
        'LDASOUT_HOURLY': 0, 'LDASOUT_DAILY': 0, 'RTOUT_DOMAIN': 0, 'LSMOUT_DOMAIN': 0,
        'CHANOBS_DOMAIN': 0, 'CHRTOUT_GRID': 0, 'output_gw': 0, 'outlake': 0,
        'frxst_pts_out': 0, 'DTRT_TER': 600, 'DTRT_CH': 600, 'lake_option': 0,
        'diversions_file': "''", 'io_form_outputs': 3, 'out_dt': 60, 'SPLIT_OUTPUT_COUNT': 1,
    }
    return land, hydro


def accepted(marker, begin, stop, signature):
    record = json.loads(marker.read_text())
    if (record.get('status') != 'passed' or record.get('start') != str(begin)
            or record.get('end') != str(stop) or record.get('signature') != signature):
        raise ValueError(f'Incompatible checkpoint acceptance: {marker}')
    paths = [Path(p) for p in record['restarts']]
    if [digest(p) for p in paths] != record['restart_sha256']:
        raise ValueError(f'Checkpoint changed: {marker}')
    restart_check(paths, stop)
    return paths


def snapshot_forcing(forcing, snapshot, begin, stop):
    """Pin atomic-published files, with current subset receipts, on the same filesystem."""
    records = {}
    for day in dates(begin, stop + timedelta(days=1)):
        relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        source, target = forcing / relative, snapshot / relative
        before = identity(source)
        receipt = json.loads(source.with_name(source.name + '.subset.json').read_text())
        if (receipt.get('status') != 'passed' or receipt.get('missing_active_values') != 0
                or receipt.get('output_identity') != before):
            raise ValueError(f'Missing/stale forcing acceptance: {source}')
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, target)
        if identity(target) != before or identity(source) != before:
            raise ValueError(f'Forcing changed while snapshotting: {source}')
        records[relative] = before
    check_forcing(snapshot, begin, stop)
    return records


def run(root, domain, year):
    if domain not in GATES or not 1981 <= year <= 1985:
        raise ValueError('Only approved four-domain 1981–1985 spin-up is authorized')
    gate = root / f'nwm/outputs/{domain}/tests/repaired-spinup-{GATES[domain]}/acceptance.json'
    evidence = json.loads(gate.read_text())
    if (evidence.get('domain') != domain or evidence.get('status') != 'passed'
            or evidence.get('restart_equivalence') != 'passed'):
        raise ValueError('Cold/warm model gate failed')
    domain_root = root / 'nwm/static/domains' / domain
    check_parameters(domain_root)
    executable = root / 'external/wrf_hydro_nwm_public-v5.4.0/build-intel/Run/wrf_hydro_NoahMP.exe'
    template = root / 'nwm/runs/nwm_subset_mid_atlantic/run_prism_native_daily'
    signature = {'runner': digest(Path(__file__)), 'executable': digest(executable),
                 'parameters': digest(domain_root / 'parameter_acceptance.json'),
                 'templates': {name: digest(template / name) for name in TABLES},
                 'gate': digest(gate)}
    runs = root / 'nwm/runs' / domain / 'retro' / CAMPAIGN
    restarts = root / 'nwm/restarts' / domain / 'retro' / CAMPAIGN
    runs.mkdir(parents=True, exist_ok=True)
    with (runs / 'production.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        first, end = datetime(year, 1, 1), datetime(year + 1, 1, 1)
        inputs = None
        if year > 1981:
            inputs = accepted(runs / f'{year - 1}12/accepted.json',
                              datetime(year - 1, 12, 1), first, signature)
            previous = json.loads((runs / f'{year - 1}-passed.json').read_text())
            if previous.get('status') != 'passed' or previous.get('signature') != signature:
                raise ValueError('Previous year not accepted')
        _, masks, _, _ = load_masks(domain_root / 'parameters/domain_masks.nc')
        active = masks['model_mask']
        job = os.environ['SLURM_JOB_ID']
        scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}")
        forcing = root / 'forcing/outputs' / domain / 'retro/hourly'
        for begin, stop in months(first, end):
            logs = runs / begin.strftime('%Y%m')
            marker = logs / 'accepted.json'
            if marker.exists():
                inputs = accepted(marker, begin, stop, signature)
                continue
            logs.mkdir(parents=True, exist_ok=True)
            attempt = logs / f'job-{job}'
            attempt.mkdir(exist_ok=False)
            snapshot = attempt / 'forcing-snapshot'
            work = scratch / f'{domain}-spinup-{begin:%Y%m}'
            report = {'status': 'running', 'domain': domain, 'start': str(begin), 'end': str(stop),
                      'job_id': job, 'mpi_ranks': int(os.environ['SLURM_NTASKS']),
                      'signature': signature, 'initial_restarts': inputs, 'history_files': 0,
                      'scientific_equilibration_verified': False}
            started = time.monotonic()
            write_json(attempt / 'status.json', report)
            try:
                report['forcing_identities'] = snapshot_forcing(forcing, snapshot, begin, stop)
                if inputs:
                    restart_check(inputs, begin)
                    check_active_state(inputs[0], active, LAND_STATES)
                work.mkdir(parents=True, exist_ok=False)
                shutil.copytree(domain_root / 'parameters', work / 'DOMAIN')
                for name in TABLES:
                    shutil.copy2(template / name, work / name)
                shutil.copy2(executable, work / 'wrf_hydro.exe')
                if digest(work / 'wrf_hydro.exe') != signature['executable']:
                    raise ValueError('Model executable changed')
                land, hydro = settings(begin, stop, snapshot, inputs)
                namelist(work / 'namelist.hrldas', land)
                namelist(work / 'hydro.namelist', hydro)
                for name in ('namelist.hrldas', 'hydro.namelist'):
                    shutil.copy2(work / name, attempt / name)
                model_started = time.monotonic()
                with (attempt / 'model.log').open('w') as log:
                    subprocess.run(['mpiexec', '-n', str(report['mpi_ranks']), './wrf_hydro.exe'],
                                   cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True)
                report['model_seconds'] = time.monotonic() - model_started
                if 'The model finished successfully' not in (attempt / 'model.log').read_text():
                    raise ValueError('Model completion sentinel missing')
                history = [p.name for p in work.iterdir() if any(
                    token in p.name for token in ('LDASOUT', 'CHRTOUT', 'RTOUT', 'LAKEOUT',
                                                  'CHANOBS', 'GWOUT', 'LSMOUT'))]
                if history:
                    raise ValueError(f'Unexpected spin-up history files: {history[:10]}')
                if any(identity(snapshot / p) != v for p, v in report['forcing_identities'].items()):
                    raise ValueError('Pinned forcing changed during simulation')
                terminal = [work / name for name in restart_names(stop)]
                restart_check(terminal, stop)
                check_active_state(terminal[0], active, LAND_STATES)
                inputs = [restarts / stop.strftime('%Y/%m') / p.name for p in terminal]
                for source, target in zip(terminal, inputs, strict=True):
                    # Interrupted pair publication is recoverable, but never overwrite different data.
                    if target.exists() and digest(target) != digest(source):
                        raise ValueError(f'Unaccepted different checkpoint exists: {target}')
                    if not target.exists():
                        copy_atomic(source, target)
                restart_check(inputs, stop)
                report.update(status='passed', restarts=inputs,
                              restart_sha256=[digest(p) for p in inputs],
                              elapsed_seconds=time.monotonic() - started)
                write_json(marker, report)
                # Only this accepted attempt's snapshots and scratch are removed.
                shutil.rmtree(snapshot)
                shutil.rmtree(work)
                print(json.dumps(report, default=str), flush=True)
            except BaseException as error:
                report.update(status='failed', error=str(error))
                raise
            finally:
                write_json(attempt / 'status.json', report)
        write_json(runs / f'{year}-passed.json', {
            'status': 'passed', 'start': str(first), 'end': str(end), 'signature': signature,
            'restarts': inputs, 'job_id': job, 'history_files': 0})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain', choices=tuple(GATES))
    parser.add_argument('year', type=int, choices=range(1981, 1986))
    args = parser.parse_args()
    run(Path(os.environ['HYDRO_OPS_PROJECT_ROOT']), args.domain, args.year)
