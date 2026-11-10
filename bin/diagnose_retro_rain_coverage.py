"""Historical sampled-hour diagnosis of the March/April 2026 writer rejection.

Not a current archive acceptance check. Retained for reproducibility; inspect
its fixed campaign inputs before executing under SLURM. Production is read-only.
"""
import json
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date


def read(var, index):
    return np.ma.asarray(var[index], dtype='f4').filled(np.nan)


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    out = root/'forcing/work/retro-rain-coverage-diagnosis'/os.environ['SLURM_JOB_ID']
    out.mkdir(parents=True, exist_ok=False)
    with Dataset(root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc') as ds:
        active, keep = np.asarray(ds['active'][:], bool), np.asarray(ds['keep'][:], bool)
    report = {'scope': 'Hours 00,12,23 of representative failed and passing days; not full archive audit',
              'production_modified': False, 'hours': []}
    original_root = root/'forcing/work/prism-repair-wave7-202101-20260417/originals'
    for label in ('2026-03-02', '2026-03-03', '2026-03-04', '2026-04-01', '2026-04-02'):
        day = date.fromisoformat(label)
        task = 62 if day.month == 3 else 63
        windows = root/f'forcing/work/prism-rain-pipeline/5174006_{task}/windows'
        original = original_root/day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        with Dataset(original) as old:
            for hour in (0, 12, 23):
                window_day = day if hour < 12 else day+timedelta(days=1)
                index = hour+12 if hour < 12 else hour-12
                with Dataset(windows/window_day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')) as ds:
                    assert str(num2date(ds['time'][index], ds['time'].units)) == f'{day} {hour:02d}:00:00'
                    new = read(ds['RAINRATE'], index)
                previous = read(old['RAINRATE'], hour)
                bad = {'active_missing': active & ~np.isfinite(new),
                       'outside_keep_valid': ~keep & np.isfinite(new),
                       'negative': np.isfinite(new) & (new < 0)}
                row = {'time': f'{day}T{hour:02d}:00:00Z',
                       'original_active_missing': int((active & ~np.isfinite(previous)).sum()),
                       'original_outside_keep_valid': int((~keep & np.isfinite(previous)).sum()),
                       'checks': {}}
                for name, mask in bad.items():
                    flat = np.flatnonzero(mask)
                    samples = []
                    for pos in flat[:8]:
                        y, x = np.unravel_index(pos, new.shape)
                        samples.append({'y': int(y), 'x': int(x), 'lat': float(old['lat'][y,x]),
                                        'lon': float(old['lon'][y,x]),
                                        'old': float(previous[y,x]) if np.isfinite(previous[y,x]) else None,
                                        'new': float(new[y,x]) if np.isfinite(new[y,x]) else None})
                    row['checks'][name] = {'cells': len(flat),
                                          'old_finite_cells': int(np.isfinite(previous[mask]).sum()),
                                          'samples': samples}
                report['hours'].append(row)
                print(row['time'], {k:v['cells'] for k,v in row['checks'].items()}, flush=True)
                (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    report['status'] = 'diagnosis_complete'
    (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
