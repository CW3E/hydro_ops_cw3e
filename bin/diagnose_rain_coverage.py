"""Read-only cell/hour audit of failed March/April 2026 Retro repair candidates."""
import os
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from materialize_calendar_forcing import locate_hours
from netCDF4 import Dataset

from hydro_ops.forcing.repair_transaction import atomic_json


def failures(values, active, keep):
    finite = np.isfinite(values)
    return {'missing_active': active & ~finite,
            'outside_envelope': ~keep & finite,
            'negative': finite & (values < 0)}


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    index = int(os.environ['SLURM_ARRAY_TASK_ID'])
    start, count, candidate = (
        (date(2026, 3, 1), 31, '5174006_62'),
        (date(2026, 4, 1), 17, '5174006_63'),
    )[index]
    source = root/'forcing/work/prism-rain-pipeline'/candidate
    original = root/'forcing/work/prism-repair-wave7-202101-20260417/originals'
    output = root/'forcing/work/rain-coverage-diagnosis'/f'{os.environ["SLURM_ARRAY_JOB_ID"]}_{index}'
    output.mkdir(parents=True, exist_ok=False)
    with Dataset(root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc') as ds:
        active = np.asarray(ds['active'][:], bool)
        keep = np.asarray(ds['keep'][:], bool)
    union = {name: np.zeros(active.shape, bool) for name in failures(np.zeros(active.shape), active, keep)}
    report = {'status': 'running', 'production_modified': False, 'candidate': str(source),
              'originals': str(original), 'days': []}
    atomic_json(output/'report.json', report)
    try:
        for offset in range(count):
            day = start+timedelta(days=offset)
            paths, indices = locate_hours(source/'windows', day)
            row = {'day': str(day), 'hours': []}
            with ExitStack() as stack:
                windows = {p: stack.enter_context(Dataset(p)) for p in set(paths)}
                old = stack.enter_context(Dataset(original/day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')))
                for hour, (path, record) in enumerate(zip(paths, indices, strict=True)):
                    values = np.ma.asarray(windows[path]['RAINRATE'][record], dtype='f8').filled(np.nan)
                    if values.shape != active.shape:
                        raise ValueError('Unexpected candidate shape')
                    bad = failures(values, active, keep)
                    entry = {'hour': hour, 'counts': {k: int(v.sum()) for k, v in bad.items()}}
                    if any(entry['counts'].values()):
                        before = np.ma.asarray(old['RAINRATE'][hour], dtype='f8').filled(np.nan)
                        old_bad = failures(before, active, keep)
                        entry['original_counts'] = {k: int(v.sum()) for k, v in old_bad.items()}
                        entry['inherited_counts'] = {k: int((v & old_bad[k]).sum()) for k, v in bad.items()}
                        entry['introduced_counts'] = {k: int((v & ~old_bad[k]).sum()) for k, v in bad.items()}
                        entry['samples'] = {}
                        for kind, cells in bad.items():
                            union[kind] |= cells
                            where = np.flatnonzero(cells)
                            entry['samples'][kind] = []
                            for flat in where[:8]:
                                y, x = np.unravel_index(flat, active.shape)
                                entry['samples'][kind].append({
                                    'y': int(y), 'x': int(x),
                                    'lat': float(old['lat'][y, x]), 'lon': float(old['lon'][y, x]),
                                    'active': bool(active[y, x]), 'keep': bool(keep[y, x]),
                                    'original_rate': float(before[y, x]) if np.isfinite(before[y, x]) else None,
                                    'candidate_rate': float(values[y, x]) if np.isfinite(values[y, x]) else None})
                    row['hours'].append(entry)
            report['days'].append(row)
            atomic_json(output/'report.json', report)
            print(day, {k: sum(h['counts'][k] for h in row['hours']) for k in union}, flush=True)
        report['unique_cells'] = {k: int(v.sum()) for k, v in union.items()}
        report['cell_hours'] = {k: sum(h['counts'][k] for d in report['days'] for h in d['hours']) for k in union}
        report['affected_days'] = [d['day'] for d in report['days'] if any(any(h['counts'].values()) for h in d['hours'])]
        np.savez_compressed(output/'failure_masks.npz', **union)
        report['status'] = 'diagnosis_complete'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(output/'report.json', report)


if __name__ == '__main__':
    main()
