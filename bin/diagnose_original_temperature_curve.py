"""Read-only affine-signature test against retained and rebuilt baselines."""
import argparse
import json
import os
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from diagnose_temperature_refresh import CASES, operational_active, records
from netCDF4 import Dataset

from hydro_ops.forcing.rain_repair_writer import fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json


def fit_curve(x,y):
    """Fit y=a*x+b at each cell, centered to avoid Kelvin cancellation."""
    complete = np.isfinite(x).all(axis=0)&np.isfinite(y).all(axis=0)
    xm,ym = x.mean(axis=0),y.mean(axis=0)
    dx,dy = x-xm,y-ym
    denominator = np.sum(dx*dx,axis=0)
    slope = np.divide(np.sum(dx*dy,axis=0),denominator,
                      out=np.full(xm.shape,np.nan),where=denominator>1e-10)
    residual = np.max(np.abs(dy-slope*dx),axis=0)
    return {'complete':complete,'slope':slope,'residual':residual,
            'maximum_difference':np.max(np.abs(y-x),axis=0),
            'midpoint_shift':(y.min(axis=0)+y.max(axis=0)-x.min(axis=0)-x.max(axis=0))/2,
            'baseline_range':x.max(axis=0)-x.min(axis=0)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign',type=Path,required=True)
    parser.add_argument('--case',type=int,choices=range(len(CASES)),required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    campaign = args.campaign.resolve()
    if not campaign.is_relative_to(root/'forcing/work/temperature-refresh'):
        raise ValueError('Dedicated campaign required')
    day = date.fromisoformat(CASES[args.case])
    work = root/'forcing/work/original-temperature-diagnostics'/os.environ['SLURM_JOB_ID']
    work.mkdir(parents=True,exist_ok=False)
    report = {'status':'running','day':str(day),'production_modified':False,
              'note':'An affine signature is evidence of adjustment, not proof of a historical PRISM version.'}
    try:
        roots = {'retained':root/'forcing/outputs/conus/baseline/hourly',
                 'rebuilt':campaign/'baseline','original':campaign/'originals/nrt',
                 'candidate':campaign/'candidates/nrt'}
        paths = {k:[base/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                    for d in (day-timedelta(days=1),day)] for k,base in roots.items()}
        mask_path = root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc'
        pins = {str(p):list(fingerprint(p)) for group in paths.values() for p in group}
        pins[str(mask_path)] = list(fingerprint(mask_path))
        sequence = {k:records(v,day) for k,v in paths.items()}
        active = operational_active(mask_path)
        pairs = [('original','retained'),('original','rebuilt'),('candidate','rebuilt'),('rebuilt','retained')]
        totals = {f'{a}_vs_{b}':{'cells':0,'missing_cells':0,'equal_within_0_001k':0,
                                 'affine_cells':0,'flat_baseline_cells':0,'positive_slope_cells':0}
                  for a,b in pairs}
        samples = {k:{v:[] for v in ('residual','maximum_difference','midpoint_shift','slope')} for k in totals}
        with ExitStack() as stack:
            files = {p:stack.enter_context(Dataset(p)) for p in {p for group in paths.values() for p in group}}
            for start in range(0,active.shape[0],64):
                selected = active[start:start+64]
                if not selected.any():
                    continue
                fields = {k:np.stack([np.ma.asarray(files[p]['T2D'][i,start:start+64,:],dtype='f8').filled(np.nan)
                                      for p,i in s])[:,selected] for k,s in sequence.items()}
                for a,b in pairs:
                    key = f'{a}_vs_{b}'
                    fitted = fit_curve(fields[b],fields[a])
                    finite = fitted['complete']
                    usable = finite & np.isfinite(fitted['residual'])
                    t = totals[key]
                    t['cells'] += int(finite.sum())
                    t['missing_cells'] += int((~finite).sum())
                    t['equal_within_0_001k'] += int((finite & (fitted['maximum_difference']<=.001)).sum())
                    t['affine_cells'] += int((usable & (fitted['residual']<=.001)).sum())
                    t['flat_baseline_cells'] += int((finite & (fitted['baseline_range']<.5)).sum())
                    t['positive_slope_cells'] += int((usable & (fitted['slope']>0)).sum())
                    for name in samples[key]:
                        v=fitted[name]
                        samples[key][name].append(v[finite & np.isfinite(v)].astype('f4'))
        for key,t in totals.items():
            for name,parts in samples[key].items():
                v=np.concatenate(parts)
                t[name] = {'p50':float(np.percentile(v,50)),'p99_abs':float(np.percentile(np.abs(v),99)),
                           'max_abs':float(np.abs(v).max())} if v.size else None
        if any(list(fingerprint(Path(p)))!=pin for p,pin in pins.items()):
            raise ValueError('Input changed')
        report.update(status='diagnosed',input_pins=pins,comparisons=totals)
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        atomic_json(work/'report.json',report)
        print(json.dumps(report),flush=True)


if __name__ == '__main__':
    main()
