#!/usr/bin/env python3
"""Isolated CNRFC cold-prelude/warm ingestion test for full-schema PRISM trial."""

import json
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from prepare_cnrfc_hu18_test import digest, identity
from test_cnrfc_hu18_model import integration

from hydro_ops.nwm_masks import load_masks
from hydro_ops.wrf_hydro.production import check_active_state, write_json


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    result = root / 'nwm/outputs/cnrfc/retro/tests/prism_ingestion' / f'job-{job}'
    result.mkdir(parents=True, exist_ok=False)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}/prism-ingestion")
    scratch.mkdir(parents=True, exist_ok=False)
    domain = root / 'nwm/static/domains/cnrfc'
    _, masks, _, _ = load_masks(domain / 'parameters/domain_masks.nc')
    trial = root / 'forcing/work/prism-full-schema-trial/5081298/2016-04-15'
    acceptance = json.loads((trial / 'acceptance.json').read_text())
    if acceptance['status'] != 'passed':
        raise ValueError('Full-schema acceptance gate failed')
    accepted = {Path(f['path']).name: f for f in acceptance['files']}
    start = datetime.fromisoformat('2016-04-15')
    forcing = scratch / 'forcing'
    started = time.monotonic()
    report = {'status': 'running', 'production_modified': False, 'scientific_validation': False,
              'job_id': job, 'mpi_ranks': int(os.environ['SLURM_NTASKS']),
              'description': 'One-day cold prelude followed by one-day option-1 warm trial',
              'forcing_snapshots': [], 'trial_acceptance': str(trial / 'acceptance.json')}
    executable = root / 'external/wrf_hydro_nwm_public-v5.4.0/build-intel/Run/wrf_hydro_NoahMP.exe'
    executable_hash = digest(executable)
    try:
        for offset in (-1, 0, 1):
            day = start + timedelta(days=offset)
            suffix = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            source = (root / 'forcing/outputs/cnrfc/retro/hourly' if offset < 0 else trial / 'hourly') / suffix
            before = identity(source)
            source_hash = digest(source)
            if offset >= 0 and accepted[source.name]['sha256'] != source_hash:
                raise ValueError('Trial file changed since acceptance')
            target = forcing / suffix
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if digest(target) != source_hash or identity(source) != before:
                raise ValueError('Forcing snapshot mismatch')
            check_active_state(target, masks['model_mask'],
                               ('T2D', 'Q2D', 'PSFC', 'U2D', 'V2D', 'RAINRATE', 'SWDOWN', 'LWDOWN'))
            # Existing integration helper consumes this receipt filename. This
            # is explicitly an ingestion audit, NOT a claim of new subsetting.
            write_json(target.with_name(target.name+'.subset.json'), {
                'status': 'passed', 'missing_active_values': 0,
                'output_identity': identity(target), 'kind': 'trial_ingestion_snapshot',
                'source': source, 'sha256': source_hash,
            })
            report['forcing_snapshots'].append({'source': str(source), 'sha256': source_hash,
                                                'date': str(day), 'trial': offset >= 0})
        # Confirm this actually tests changed rain rather than accidentally pointing at old files.
        with Dataset(forcing / start.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')) as new, Dataset(
            root / 'forcing/outputs/cnrfc/retro/hourly' / start.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        ) as old:
            changed = 0
            for h in range(24):
                changed += int(np.ma.count(np.ma.masked_equal(new['RAINRATE'][h]-old['RAINRATE'][h], 0)))
            if not changed:
                raise ValueError('No changed rainfall samples in ingestion trial')
            report['changed_rainfall_samples'] = changed
        write_json(result / 'acceptance.json', report)
        report['prelude'] = integration(root, domain, forcing, start-timedelta(days=1), start,
                                         result / 'prelude', scratch / 'prelude')
        initial = result / 'prelude/restarts' / start.strftime('%Y/%m')
        report['trial'] = integration(root, domain, forcing, start, start+timedelta(days=1),
                                       result / 'trial', scratch / 'trial', initial=initial)
        if digest(executable) != executable_hash:
            raise ValueError('Executable changed during experiment')
        report.update(status='passed', executable_sha256=executable_hash)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['elapsed_seconds'] = time.monotonic()-started
        write_json(result / 'acceptance.json', report)


if __name__ == '__main__':
    main()
