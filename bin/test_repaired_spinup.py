#!/usr/bin/env python3
"""Isolated two-day cold start and option-1 restart check on repaired forcing."""
# Model clock is naive UTC.
# ruff: noqa: DTZ001
import argparse
import os
from datetime import datetime
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from run_cnrfc_hu18_pilot import check_parameters
from test_cnrfc_hu18_model import integration

from hydro_ops.wrf_hydro.production import restart_names, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain', choices=('cnrfc','b120','ucrb','yampa'))
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    domain = root/'nwm/static/domains'/args.domain
    check_parameters(domain)
    job = os.environ['SLURM_JOB_ID']
    output = root/f'nwm/outputs/{args.domain}/tests/repaired-spinup-{job}'
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{job}/repaired-spinup")
    forcing = root/f'forcing/outputs/{args.domain}/retro/hourly'
    start, resume, end = datetime(1981,1,1), datetime(1981,1,2), datetime(1981,1,3)
    report = {'status':'running', 'domain':args.domain, 'production_modified':False,
              'scientific_validation':False}
    try:
        report['continuous'] = integration(root, domain, forcing, start, end,
                                           output/'continuous', scratch/'continuous')
        initial = output/'continuous/restarts/1981/01'
        report['resumed'] = integration(root, domain, forcing, resume, end,
                                       output/'resumed', scratch/'resumed', initial=initial)
        maxima = {}
        for filename, names in zip(restart_names(end), [
                ('SMC','SH2O','SOIL_T','SNEQV'), ('hlink','qlink1','qlink2','z_gwsubbas')], strict=True):
            with Dataset(output/'continuous/restarts/1981/01'/filename) as a, \
                    Dataset(output/'resumed/restarts/1981/01'/filename) as b:
                for name in names:
                    x,y = np.ma.asarray(a[name][:]),np.ma.asarray(b[name][:])
                    np.testing.assert_array_equal(np.ma.getmaskarray(x),np.ma.getmaskarray(y))
                    np.testing.assert_allclose(x.compressed(), y.compressed(),rtol=1e-5,atol=1e-6)
                    maxima[name]=float(np.ma.max(np.ma.abs(x.astype('f8')-y.astype('f8'))))
        report.update(status='passed', restart_equivalence='passed', max_abs_differences=maxima)
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        write_json(output/'acceptance.json',report)


if __name__ == '__main__':
    main()
