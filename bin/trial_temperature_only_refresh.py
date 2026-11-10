"""Build isolated thermal-only candidates for one complete PRISM window.

No production publication, queue acknowledgement or model changes. Both halves
of the 12-12 constraint window are staged as 00-23 calendar-day collections.
"""
import argparse
import json
import os
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date

from hydro_ops.forcing import nrt_cycle
from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.prism_temperature import (
    apply_daily_temperature_constraint,
    create_prism_temperature_constraints,
)
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.forcing.temperature_refresh import CHANGED, coupled, unchanged_payloads


def field(ds, name, index):
    return np.ma.asarray(ds[name][index], dtype='f8').filled(np.nan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window', required=True, type=date.fromisoformat)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    job = os.environ['SLURM_JOB_ID']
    work = root/f'forcing/work/temperature-refresh/{job}'
    work.mkdir(parents=True, exist_ok=False)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/temperature-refresh')
    scratch.mkdir(parents=True, exist_ok=False)
    day = args.window
    days = (day-timedelta(days=1), day)
    layout = OperationalLayout.project_defaults(root)
    report = {'status': 'running', 'window': str(day), 'production_modified': False,
              'queue_modified': False, 'changed_variables': list(CHANGED), 'files': []}
    path = work/'acceptance.json'
    try:
        # Snapshot before expensive work; publishers must check these pins again.
        originals = {}
        for stream in ('retro', 'nrt'):
            for d in days:
                source = root/f'forcing/outputs/conus/{stream}/hourly'/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                before = fingerprint(source)
                target = work/'originals'/stream/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(source, target)
                if fingerprint(source) != before or fingerprint(target) != before:
                    raise ValueError('Original changed during snapshot')
                originals[str(source)] = list(before)
        report['production_pins'] = originals
        inputs = {}
        for v in ('tmin', 'tmax'):
            source = root/'forcing/inputs/oregon_state/prism/an/4km/daily'/v/day.strftime(f'%Y/%m/prism_{v}_us_25m_%Y%m%d.nc')
            pin = source_signature(source)
            copy = work/f'{v}.nc'
            shutil.copy2(source, copy)
            if source_signature(source) != pin or digest(source) != digest(copy):
                raise ValueError('PRISM changed during snapshot')
            inputs[v] = pin
        report['prism_sources'] = inputs
        atomic_json(path, report)
        engine = nrt_cycle.RecentNrt(root, scratch, datetime.now(UTC),
                                   baseline_root=work/'baseline', output_root=work/'unused')
        baselines = []
        for d in days:
            selections, _ = nrt_cycle.baseline_plan(d, engine.layout, engine.config)
            if {s.product for s in selections} != {'nldas2'}:
                raise ValueError('Trial requires local NLDAS-2; no external acquisition allowed')
            baseline, receipt = engine.baseline_day(d)
            if receipt['status'] != 'passed' or not receipt['complete_day']:
                raise ValueError('Baseline gate failed')
            baselines.append(baseline)
        target = scratch/'temperature-target.nc'
        create_prism_temperature_constraints(work/'tmin.nc', work/'tmax.nc',
            root/'forcing/static/prism/prism_an_4km_elevation.nc', layout.target_grid,
            layout.target_elevation, root/'forcing/static/remapping/nwm_conus_1km/prism_bilinear.nc',
            target, work_directory=scratch)
        corrected = scratch/'temperature-corrected.nc'
        apply_daily_temperature_constraint([baselines[0]]*12+[baselines[1]]*12,
            target, corrected, baseline_variable='T2D',
            source_time_indices=list(range(12, 24))+list(range(12)))
        with Dataset(root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc') as ds:
            active = np.asarray(ds['active'][:], bool)
            keep = np.asarray(ds['keep'][:], bool)
        with Dataset(corrected) as corrected_ds:
            corrected_times = num2date(corrected_ds['time'][:], corrected_ds['time'].units)
            for stream in ('retro', 'nrt'):
                for half, d in enumerate(days):
                    original = work/'originals'/stream/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                    staged = scratch/f'{stream}-{d}.nc'
                    shutil.copy2(original, staged)
                    indices = range(12, 24) if half == 0 else range(12)
                    with Dataset(original) as old, Dataset(staged, 'r+') as out:
                        times = num2date(old['time'][:], old['time'].units)
                        if [(t.year, t.month, t.day, t.hour) for t in times] != [(d.year,d.month,d.day,h) for h in range(24)]:
                            raise ValueError('Not a complete calendar day')
                        for h in indices:
                            i = h-12 if half == 0 else h+12
                            if times[h] != corrected_times[i]:
                                raise ValueError('Constraint timestamp mismatch')
                            old_t = field(old, 'T2D', h)
                            new_t = field(corrected_ds, 'T2D', i)
                            # Preserve the existing valid-footprint; reject active gaps.
                            new_t = np.where(keep & np.isfinite(old_t), new_t, np.nan)
                            values = coupled(old_t, field(old,'Q2D',h), field(old,'PSFC',h),
                                             field(old,'LWDOWN',h), new_t)
                            for name, value in values.items():
                                if not np.all(np.isfinite(value[active])) or np.any(np.isfinite(value[~keep])):
                                    raise ValueError(f'Coverage failed: {stream}/{d}/{h}/{name}')
                                if name != 'T2D' and np.any(value[np.isfinite(value)] < 0):
                                    raise ValueError(f'Negative coupled field: {name}')
                                out[name][h] = np.ma.masked_invalid(value)
                        out.temperature_refresh_provenance = json.dumps(inputs, sort_keys=True)
                        out.temperature_refresh_window = str(day)
                    # Full readback of changed hours; unchanged hours must match exactly.
                    with Dataset(original) as old, Dataset(staged) as out:
                        for h in range(24):
                            if h in indices:
                                i = h-12 if half == 0 else h+12
                                new_t = np.where(keep & np.isfinite(field(old,'T2D',h)), field(corrected_ds,'T2D',i), np.nan)
                                expected = coupled(field(old,'T2D',h),field(old,'Q2D',h),
                                    field(old,'PSFC',h),field(old,'LWDOWN',h),new_t)
                            else:
                                expected = {n: field(old,n,h) for n in CHANGED}
                            for name, value in expected.items():
                                np.testing.assert_array_equal(field(out,name,h).astype('f4'), value.astype('f4'))
                    chunks = unchanged_payloads(original, staged)
                    dest = work/'candidates'/stream/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(staged, dest)
                    checksum = digest(staged)
                    if digest(dest) != checksum:
                        raise ValueError('Candidate copy mismatch')
                    report['files'].append({'path': str(dest), 'stream': stream, 'day': str(d),
                        'changed_hours': list(indices), 'sha256': checksum,
                        'unchanged_payload_chunks_verified': chunks})
                    atomic_json(path, report)
        if any(list(fingerprint(Path(p))) != pin for p, pin in originals.items()):
            raise ValueError('Production changed during trial')
        if any(source_signature(Path(pin['path'])) != pin for pin in inputs.values()):
            raise ValueError('PRISM changed during trial')
        report['status'] = 'passed_candidate_only'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(path, report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
