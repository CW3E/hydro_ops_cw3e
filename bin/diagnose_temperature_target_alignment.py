"""Read-only date/grid alignment checks against inferred original targets."""
import json
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from compare_temperature_target_midpoints import describe
from diagnose_temperature_refresh import independent_target
from netCDF4 import Dataset

from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json


def shifted(field, dy, dx):
    """Shift without allowing cyclic wraparound at domain edges."""
    out = np.roll(field, (dy, dx), axis=(0, 1)).copy()
    if dy > 0:
        out[:dy] = np.nan
    elif dy < 0:
        out[dy:] = np.nan
    if dx > 0:
        out[:, :dx] = np.nan
    elif dx < 0:
        out[:, dx:] = np.nan
    return out


def comparison(old, target, reference):
    mask = np.isfinite(old) & np.isfinite(target) & np.isfinite(reference)
    return {'cells': int(mask.sum()), 'alternative_error_k': describe((old-target)[mask]),
            'same_cell_current_error_k': describe((old-reference)[mask])}


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    job = os.environ['SLURM_JOB_ID']
    work = root/'forcing/work/temperature-target-alignment'/job
    work.mkdir(parents=True, exist_ok=False)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/target-alignment')
    scratch.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'production_modified': False, 'publication_released': False,
              'cases': {}, 'input_pins': {}}
    pins = report['input_pins']
    try:
        layout = OperationalLayout.project_defaults(root)
        for prior_job in ('5228764', '5228765', '5228762'):
            prior = root/'forcing/work/temperature-refresh-diagnostics'/prior_job
            evidence = json.loads((prior/'report.json').read_text())
            if evidence['status'] != 'passed' or evidence.get('diagnostic_version') != 2:
                raise ValueError('Accepted version-2 diagnostic required')
            maps = prior/'spatial-diagnostics.nc'
            if digest(maps) != evidence['maps_sha256']:
                raise ValueError('Prior maps changed')
            pins.update(evidence['input_pins'])
            for path, pin in pins.items():
                if list(fingerprint(Path(path))) != pin:
                    raise ValueError(f'Input changed: {path}')
            pins[str(maps)] = list(fingerprint(maps))
            with Dataset(maps) as ds:
                target = np.ma.asarray(ds['independent_prism_midpoint'][:], dtype='f8').filled(np.nan)
                old = target + np.ma.asarray(ds['original_midpoint_error'][:], dtype='f8').filled(np.nan)
            day = date.fromisoformat(evidence['day'])
            case = {'current': comparison(old, target, target), 'grid_shifts': {}, 'adjacent_dates': {}}
            report['cases'][str(day)] = case
            for dy, dx in [(y, x) for y in (-1, 0, 1) for x in (-1, 0, 1) if y or x] + [(4, 0), (-4, 0), (0, 4), (0, -4)]:
                case['grid_shifts'][f'{dy},{dx}'] = comparison(old, shifted(target, dy, dx), target)
            for delta in (-1, 1):
                other = day + timedelta(days=delta)
                sources = {v: root/'forcing/inputs/oregon_state/prism/an/4km/daily'/v/other.strftime('%Y/%m')/
                           f'prism_{v}_us_25m_{other:%Y%m%d}.nc' for v in ('tmin', 'tmax')}
                for p in sources.values():
                    pins[str(p)] = list(fingerprint(p))
                staging = scratch/f'{day}_{delta}'
                staging.mkdir()
                low, high = independent_target(root, layout, sources, staging)
                case['adjacent_dates'][str(other)] = comparison(old, (low+high)/2, target)
            case['release_records'] = {}
            for p in evidence['input_pins']:
                path = Path(p)
                if '/daily/tmin/' in p or '/daily/tmax/' in p:
                    release = path.with_suffix('.release.json')
                    case['release_records'][str(release)] = json.loads(release.read_text())
            atomic_json(work/'report.json', report)
        for p, pin in pins.items():
            if list(fingerprint(Path(p))) != pin:
                raise ValueError(f'Input changed: {p}')
        report['status'] = 'diagnosed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(work/'report.json', report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
