"""Read-only operational-mask check of archive temperature against current PRISM.

Midpoint is the invariant even when the production diurnal-range scale clips.
Extrema differences are descriptive, not independent acceptance requirements.
"""
import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from audit_prism_temperature_midpoint import statistics
from diagnose_temperature_refresh import extrema, independent_target, operational_active, records

from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.rain_repair_writer import fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json


def evaluate(low, high, target_low, target_high, active):
    if len({a.shape for a in (low, high, target_low, target_high, active)}) != 1:
        raise ValueError('Grid shape mismatch')
    mask = active & np.isfinite(target_low) & np.isfinite(target_high) & (target_high >= target_low)
    if not mask.any():
        raise ValueError('Empty PRISM comparison domain')
    complete = np.isfinite(low) & np.isfinite(high)
    missing = int((active & ~complete).sum())
    midpoint = statistics((low+high)/2, (target_low+target_high)/2, mask)
    delta = ((low+high)/2-(target_low+target_high)/2)[mask & complete]
    midpoint['rms_k'] = float(np.sqrt(np.mean(delta*delta))) if delta.size else None
    matched = missing == 0 and midpoint['cells_above_0_001k'] == 0
    return {'result': 'midpoint_match' if matched else 'mismatch_or_missing',
            'required_active_cells': int(active.sum()), 'incomplete_active_cells': missing,
            'target_cells': int(mask.sum()), 'active_without_valid_prism_target': int((active & ~mask).sum()),
            'midpoint': midpoint, 'minimum_descriptive': statistics(low, target_low, mask),
            'maximum_descriptive': statistics(high, target_high, mask)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', nargs='+', required=True, type=date.fromisoformat)
    parser.add_argument('--stream', choices=('retro', 'nrt'), default='retro')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    job = os.environ['SLURM_JOB_ID']
    work = root/'forcing/work/independent-temperature-audit'/job
    work.mkdir(parents=True, exist_ok=False)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/temperature-audit')
    scratch.mkdir(parents=True, exist_ok=False)
    layout = OperationalLayout.project_defaults(root)
    envelope = root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
    report = {'status': 'running', 'job': job, 'stream': args.stream,
              'production_modified': False, 'days': {},
              'interpretation': 'Current midpoint consistency, not historical cause or full coupled-field acceptance.'}
    pins = {str(p): list(fingerprint(p)) for p in (envelope, layout.target_grid, layout.target_elevation,
            root/'forcing/static/prism/prism_an_4km_elevation.nc',
            root/'forcing/static/remapping/nwm_conus_1km/prism_bilinear.nc')}
    report['input_pins'] = pins
    try:
        active = operational_active(envelope)
        for day in args.days:
            paths = [root/f'forcing/outputs/conus/{args.stream}/hourly'/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                     for d in (day-timedelta(days=1), day)]
            sources = {v: root/'forcing/inputs/oregon_state/prism/an/4km/daily'/v/day.strftime('%Y/%m')/
                       f'prism_{v}_us_25m_{day:%Y%m%d}.nc' for v in ('tmin', 'tmax')}
            signatures = {v: source_signature(p) for v, p in sources.items()}
            pins.update({str(p): list(fingerprint(p)) for p in paths+list(sources.values())})
            staging = scratch/str(day)
            staging.mkdir()
            target_low, target_high = independent_target(root, layout, sources, staging)
            low, high = extrema(records(paths, day))
            result = evaluate(low, high, target_low, target_high, active)
            result['prism_sources'] = signatures
            for v, p in sources.items():
                if signatures[v] != source_signature(p):
                    raise ValueError('PRISM source changed during audit')
            report['days'][str(day)] = result
            atomic_json(work/'report.json', report)
            print(json.dumps({'day': str(day), **result}), flush=True)
        for p, pin in pins.items():
            if list(fingerprint(Path(p))) != pin:
                raise ValueError(f'Input changed: {p}')
        report['status'] = 'diagnosed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(work/'report.json', report)


if __name__ == '__main__':
    main()
