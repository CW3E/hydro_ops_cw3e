#!/usr/bin/env python3
"""Isolated full-CONUS October 1986 precipitation-only summary benchmark."""
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from backfill_forcing_summaries import audited
from netCDF4 import Dataset

from hydro_ops.forcing.model_interval import load_forcing_reducers
from hydro_ops.forcing.rain_repair_writer import fingerprint
from hydro_ops.forcing.rain_summary_repair import repair_summary
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.forcing.temporal_summary import input_path, summarize, summary_path


def compare(a, b):
    with Dataset(a) as x, Dataset(b) as y:
        if set(x.variables) != set(y.variables):
            raise ValueError('Reference variable sets differ')
        for name in x.variables:
            v, w = x[name], y[name]
            if v.dimensions != w.dimensions or v.shape != w.shape:
                raise ValueError(f'Reference shape mismatch: {name}')
            if 'y' in v.dimensions:
                axis = v.dimensions.index('y')
                slices = []
                for j in range(0, v.shape[axis], 120):
                    sl = [slice(None)]*v.ndim
                    sl[axis] = slice(j, j+120)
                    slices.append(tuple(sl))
            else:
                slices = [Ellipsis]
            for sl in slices:
                left, right = np.ma.asarray(v[sl]), np.ma.asarray(w[sl])
                np.testing.assert_array_equal(np.ma.getmaskarray(left), np.ma.getmaskarray(right))
                np.testing.assert_array_equal(left.compressed(), right.compressed())


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    output = root/'forcing/work/rain-summary-benchmark'/f'job-{job}'
    output.mkdir(parents=True, exist_ok=False)
    parent = root/'forcing/outputs/conus/retro'
    wave = root/'forcing/work/prism-repair-wave3-198602-199012'
    start, stop = date(1986, 10, 1), date(1986, 11, 1)
    reducers, names, units = load_forcing_reducers(root/'config/forcing_daily_reducers.toml')
    spec = {'reducers': reducers, 'output_names': names, 'output_units': units}
    report = {'status': 'running', 'production_modified': False, 'daily': [],
              'scope': '1986-10 CONUS; full-grid daily and monthly reference comparisons',
              'allocated_cpus': int(os.environ['SLURM_CPUS_PER_TASK']), 'workers': 1}
    atomic_json(output/'acceptance.json', report)

    def pin(source, destination):
        before = fingerprint(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, destination)
        if fingerprint(destination) != before or fingerprint(source) != before:
            raise ValueError('Source changed while pinning')
        return destination

    try:
        # Pin first; the ongoing full-summary jobs may later replace live files.
        for i in range(32):
            day = start+timedelta(days=i)
            source = input_path(parent/'hourly', day)
            audited(source)
            tx = json.loads((wave/'production'/f'{day}.plan.json').read_text())
            original = input_path(wave/'originals', day)
            if list(fingerprint(original)) != tx['old_identity']:
                raise ValueError('Original rain-only proof does not match preserved input')
            pin(source, output/'hourly'/day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1'))
            if i < 31:
                pin(summary_path(parent/'daily', day), summary_path(output/'old-daily', day))
        old_month = pin(summary_path(parent/'monthly', start, monthly=True),
                        summary_path(output/'old-monthly', start, monthly=True))
        for i in range(31):
            day = start+timedelta(days=i)
            tick = time.monotonic()
            candidate = summary_path(output/'daily', day)
            repair_summary(summary_path(output/'old-daily', day), candidate,
                           [input_path(output/'hourly', d) for d in (day, day+timedelta(days=1))],
                           [input_path(wave/'originals', d) for d in (day, day+timedelta(days=1))],
                           day, day+timedelta(days=1), spec)
            row = {'day': str(day), 'rain_only_seconds': time.monotonic()-tick}
            # Full all-variable oracle for both ends, without touching live products.
            if i in (0, 30):
                tick = time.monotonic()
                reference = summary_path(output/'reference-daily', day)
                summarize(output/'hourly', reference, day, day+timedelta(days=1),
                          reducers, names, units, domain='conus', stream='retro')
                row['full_regeneration_seconds'] = time.monotonic()-tick
                compare(candidate, reference)
                row['all_variables_reference_equal'] = True
            report['daily'].append(row)
            atomic_json(output/'acceptance.json', report)
        tick = time.monotonic()
        candidate = summary_path(output/'monthly', start, monthly=True)
        days = [start+timedelta(days=i) for i in range(31)]
        repair_summary(old_month, candidate,
                       [summary_path(output/'daily', d) for d in days],
                       [summary_path(output/'old-daily', d) for d in days],
                       start, stop, spec, monthly=True)
        report['monthly_rain_only_seconds'] = time.monotonic()-tick
        tick = time.monotonic()
        reference = summary_path(output/'reference-monthly', start, monthly=True)
        summarize(output/'daily', reference, start, stop, reducers, names, units,
                  from_daily=True, domain='conus', stream='retro')
        report['monthly_full_seconds'] = time.monotonic()-tick
        compare(candidate, reference)
        report.update(status='passed', monthly_all_variables_reference_equal=True,
                      nonrain_preserved_all_31_days=True)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(output/'acceptance.json', report)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
