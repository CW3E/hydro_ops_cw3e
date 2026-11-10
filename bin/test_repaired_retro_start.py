"""Isolated corrected-spinup restart screening and 1979 output/continuation gate."""
# Model dates are naive UTC.
# ruff: noqa: DTZ001
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from run_cnrfc_hu18_pilot import check_parameters, digest
from test_cnrfc_hu18_model import integration

from hydro_ops.nwm_masks import load_masks
from hydro_ops.wrf_hydro.production import initialization, restart_names, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain', choices=('cnrfc', 'ucrb', 'b120', 'yampa'))
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    domain = root/'nwm/static/domains'/args.domain
    output = root/'nwm/outputs'/args.domain/'tests'/f'repaired-retro-start-{job}'
    output.mkdir(parents=True, exist_ok=False)
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}/repaired-retro-start")
    donor = root/'nwm/restarts'/args.domain/'retro/repaired_rain_spinup_v1/1986/01'
    report = {'status': 'running', 'domain': args.domain, 'job_id': job,
              'production_modified': False, 'scientific_equilibration_verified': False}
    try:
        check_parameters(domain)
        accepted = json.loads((root/'nwm/runs'/args.domain/
                              'retro/repaired_rain_spinup_v1/198512/accepted.json').read_text())
        if accepted['status'] != 'passed' or accepted['end'] != '1986-01-01 00:00:00':
            raise ValueError('Final corrected spinup acceptance missing')
        sources = [donor/n for n in restart_names(datetime(1986, 1, 1))]
        if [digest(p) for p in sources] != accepted['restart_sha256']:
            raise ValueError('Spinup checkpoint checksum changed')
        _, masks, _, _ = load_masks(domain/'parameters/domain_masks.nc')
        active = masks['model_mask']
        screening = {}
        with Dataset(sources[0]) as ds:
            for name, low, high in [('SMC', 0, 1), ('SH2O', 0, 1),
                                    ('SOIL_T', 150, 350), ('SNEQV', 0, None)]:
                var = ds[name]
                dims = list(var.dimensions)
                values = np.moveaxis(np.ma.asarray(var[:]),
                                     (dims.index('south_north'), dims.index('west_east')),
                                     (-2, -1))[..., active]
                if np.ma.getmaskarray(values).any() or not np.isfinite(values).all():
                    raise ValueError(f'Missing active restart state: {name}')
                if (values < low-1e-6).any() or (high is not None and (values > high+1e-6).any()):
                    raise ValueError(f'Active restart state outside broad bounds: {name}')
                screening[name] = {'min': float(values.min()), 'max': float(values.max())}
        with Dataset(sources[1]) as ds:
            for name in ('hlink', 'qlink1', 'qlink2', 'z_gwsubbas'):
                values = np.ma.asarray(ds[name][:])
                if np.ma.getmaskarray(values).any() or not np.isfinite(values).all():
                    raise ValueError(f'Missing routing restart state: {name}')
                screening[name] = {'min': float(values.min()), 'max': float(values.max())}
        report['restart_screening'] = screening
        initial = output/'initialization'
        initialization(root, initial, donor=donor)
        start, resume, end = datetime(1979, 1, 2), datetime(1979, 1, 3), datetime(1979, 1, 4)
        forcing = root/'forcing/outputs'/args.domain/'retro/hourly'
        report['continuous'] = integration(root, domain, forcing, start, end,
                                           output/'continuous', scratch/'continuous', initial)
        report['resumed'] = integration(root, domain, forcing, resume, end,
                                        output/'resumed', scratch/'resumed',
                                        output/'continuous/restarts/1979/01')
        maxima = {}
        for filename, names in zip(restart_names(end),
                [('SMC', 'SH2O', 'SOIL_T', 'SNEQV'), ('hlink', 'qlink1', 'qlink2', 'z_gwsubbas')], strict=True):
            with Dataset(output/'continuous/restarts/1979/01'/filename) as a, \
                    Dataset(output/'resumed/restarts/1979/01'/filename) as b:
                for name in names:
                    x, y = np.ma.asarray(a[name][:]), np.ma.asarray(b[name][:])
                    np.testing.assert_array_equal(np.ma.getmaskarray(x), np.ma.getmaskarray(y))
                    np.testing.assert_allclose(x.compressed(), y.compressed(), rtol=1e-5, atol=1e-6)
                    maxima[name] = float(np.ma.max(np.ma.abs(x.astype('f8')-y.astype('f8'))))
        if [digest(p) for p in sources] != accepted['restart_sha256']:
            raise ValueError('Donor changed during test')
        report.update(status='passed', restart_equivalence='passed', max_abs_differences=maxima,
                      initial_restart_sha256={p.name: digest(p) for p in initial.glob('*DOMAIN1')})
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        write_json(output/'acceptance.json', report)


if __name__ == '__main__':
    main()
