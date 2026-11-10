#!/usr/bin/env python3
"""Audit 1979–1980 stored rain against monthly PRISM and propose bounded totals.

Read-only production access; proposed monthly depths are not model forcing.
No baseline archive or temperature recalculation is required.
"""
import argparse
import calendar
import os
from datetime import date
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.forcing.precipitation_reconciliation import ConservativeOperator
from hydro_ops.forcing.prism_safeguards import (
    audit_direct_depth,
    direct_conservative_depth,
    fit_daily_depth,
    weak_support_targets,
)
from hydro_ops.forcing.rain_repair_writer import fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--save-spatial-diagnostics', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    index = int(os.environ['SLURM_ARRAY_TASK_ID'])
    if not 0 <= index < 24:
        raise ValueError('Only 1979–1980 months authorized')
    year, month = 1979+index//12, index%12+1
    output = root / 'forcing/work/monthly-rain-repair' / os.environ['SLURM_ARRAY_JOB_ID'] / f'{year}{month:02}'
    output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'year': year, 'month': month,
              'production_modified': False, 'hourly_repair_verified': False}
    atomic_json(output/'acceptance.json', report)
    try:
        weights = root/'forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc'
        operator = ConservativeOperator.from_cdo(weights)
        with Dataset(weights) as ds:
            fraction = np.asarray(ds['dst_grid_frac'][:])
            area = np.asarray(ds['dst_grid_area'][:])*fraction
        with Dataset(root/'nwm/static/operational/nwm.v3.1.6/domain/wrfinput_CONUS_NLDAS2.nc') as ds:
            active = np.asarray(ds['XLAND'][:]).squeeze() == 1
        shape = active.shape
        total = np.zeros(shape, dtype='f8')
        complete = np.ones(shape, dtype=bool)
        records = 0
        identities = {}
        ndays = calendar.monthrange(year, month)[1]
        for day_number in range(1, ndays+1):
            day = date(year, month, day_number)
            path = root/'forcing/outputs/conus/retro/hourly'/day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            identities[str(path)] = list(fingerprint(path))
            with Dataset(path) as ds:
                if str(getattr(ds, 'prism_constraint_frequency', '')) != 'monthly':
                    raise ValueError(f'Not monthly constrained: {path}')
                stamps = num2date(ds['time'][:], ds['time'].units)
                hours = range(13,24) if day == date(1979,1,1) else range(24)
                if [(s.year,s.month,s.day,s.hour,s.minute,s.second) for s in stamps] != [
                        (year,month,day_number,h,0,0) for h in hours]:
                    raise ValueError(f'Time coverage mismatch: {day}')
                for i in range(len(stamps)):
                    values = np.ma.asarray(ds['RAINRATE'][i], dtype='f8').filled(np.nan)*3600
                    valid = np.isfinite(values) & (values >= 0)
                    if not valid[active].all():
                        raise ValueError(f'Missing/negative active rain: {day} record {i}')
                    total += np.where(valid, values, 0)
                    complete &= valid
                    records += 1
        coverage = records/(24*ndays)
        prism = root/f'forcing/inputs/oregon_state/prism/an/4km/monthly/ppt/{year}/prism_ppt_us_25m_{year}{month:02}.nc'
        with Dataset(prism) as ds:
            target = np.ma.asarray(ds['ppt'][:], dtype='f8').filled(np.nan).reshape(-1)*coverage
        missing = np.bincount(operator.target_index[~complete.reshape(-1)[operator.source_index]],
                              minlength=operator.target_size)>0
        eligible = np.isfinite(target) & (fraction>0) & ~missing
        weak = eligible & weak_support_targets(operator, fraction, active, threshold=.1)
        hard = eligible & ~weak
        target = np.where(hard, target, np.nan)
        old = operator.apply(np.where(complete, total, np.nan))
        tolerance = .01+.001*target
        old_bad = hard & (~np.isfinite(old) | (abs(old-target)>tolerance))
        if args.save_spatial_diagnostics:
            active_weight = np.bincount(operator.target_index,
                weights=operator.weight*active.reshape(-1)[operator.source_index],
                minlength=operator.target_size)
            full_weight = np.bincount(operator.target_index,weights=operator.weight,
                                      minlength=operator.target_size)
            with Dataset(output/'spatial_diagnostics.nc','w') as ds:
                ds.createDimension('cell',operator.target_size)
                for name, values in (
                        ('target_mm',target), ('stored_mm',old), ('covered_area',area),
                        ('active_link_fraction',np.divide(active_weight,full_weight,
                            out=np.zeros_like(active_weight),where=full_weight>0))):
                    v=ds.createVariable(name,'f8',('cell',),zlib=True)
                    v[:]=np.ma.masked_invalid(values)
                ds.scope='monthly stored rainfall on PRISM grid; common supported area only'
        depth, solver = fit_daily_depth(target, operator, max_iterations=200)
        estimate = operator.apply(depth)
        fallback = bool(np.any(hard & (~np.isfinite(estimate) | (abs(estimate-target)>tolerance))))
        direct_audit = None
        if fallback:
            depth, _ = direct_conservative_depth(target, operator, area)
            direct_audit = audit_direct_depth(depth, target, operator, area)
            if not direct_audit['accepted']:
                raise ValueError('Proposed monthly conservative depth failed volume audit')
        with Dataset(output/'proposed_monthly_depth.nc', 'w') as ds:
            ds.createDimension('y', shape[0]); ds.createDimension('x', shape[1])
            v = ds.createVariable('proposed_monthly_depth', 'f8', ('y','x'), zlib=True)
            v[:] = np.ma.masked_invalid(depth.reshape(shape)); v.units='mm'
            ds.prism_precipitation_coverage_fraction=coverage
            ds.product='proposal only; no hourly timing or publication acceptance'
        if any(list(fingerprint(Path(p))) != ident for p,ident in identities.items()):
            raise ValueError('Source changed during audit')
        report.update(status='passed', records=records, coverage_fraction=coverage,
                      constrained_cells=int(hard.sum()), old_cells_outside_tolerance=int(old_bad.sum()),
                      old_max_abs_error_mm=float(np.nanmax(abs(old[hard]-target[hard]))),
                      old_shared_area_volume_relative_bias=float(np.sum((old[hard]-target[hard])*area[hard])/
                                                               max(np.sum(target[hard]*area[hard]),1e-30)),
                      proposal_fallback=fallback, solver=solver, direct_audit=direct_audit,
                      source_identities=identities)
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(output/'acceptance.json', report)


if __name__ == '__main__':
    main()
