"""Read-only independent target/affine/calendar checks for frozen candidates.

Unlike the production target builder, interpolate temperature and elevation
separately in double precision, then apply the lapse correction on the target.
Never publish or release held jobs. This tests present consistency, not the
unavailable historical PRISM values or the provenance of every old mismatch.
"""
import argparse
import json
import os
import shutil
import subprocess
from contextlib import ExitStack
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import xarray as xr
from audit_prism_temperature_midpoint import statistics
from netCDF4 import Dataset, num2date

from hydro_ops.forcing.normalize import open_normalized_forcing
from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json

CASES = ['2026-05-03','2026-05-10','2026-05-15']


def records(paths, day):
    start = datetime.combine(day-timedelta(days=1),time(12))
    wanted = [start+timedelta(hours=h) for h in range(24)]
    found = {}
    for path in paths:
        with Dataset(path) as ds:
            t = ds['time']
            for i,stamp in enumerate(num2date(t[:],t.units,calendar=getattr(t,'calendar','standard'),
                                              only_use_cftime_datetimes=False)):
                if stamp in wanted:
                    if stamp in found:
                        raise ValueError('Duplicate timestamp')
                    found[stamp] = (path,i)
    if set(found) != set(wanted):
        raise ValueError('Missing constraint-window timestamp')
    return [found[t] for t in wanted]


def values(ds,index):
    return np.ma.asarray(ds['T2D'][index],dtype='f8').filled(np.nan).squeeze()


def operational_active(path):
    """Use the production-required active cells, not the broader grid footprint."""
    with Dataset(path) as ds:
        active = np.asarray(ds['active'][:],bool)
        keep = np.asarray(ds['keep'][:],bool)
    if active.shape != keep.shape or np.any(active & ~keep) or not active.any():
        raise ValueError('Invalid operational active/envelope masks')
    return active


def maximum_error(actual, expected, mask):
    """Reject missing required values rather than letting NaN hide an error."""
    a = np.ma.asarray(actual,dtype='f8').filled(np.nan)
    b = np.ma.asarray(expected,dtype='f8').filled(np.nan)
    if a.shape != b.shape or a.shape != mask.shape or not mask.any():
        raise ValueError('Invalid/empty comparison domain')
    if not (np.isfinite(a[mask]).all() and np.isfinite(b[mask]).all()):
        raise ValueError('Nonfinite values in required comparison cells')
    result = float(np.max(np.abs(a[mask]-b[mask])))
    if not np.isfinite(result):
        raise ValueError('Nonfinite maximum error')
    return result


def extrema(sequence):
    with ExitStack() as stack:
        files = {p:stack.enter_context(Dataset(p)) for p in {p for p,_ in sequence}}
        first = values(files[sequence[0][0]],sequence[0][1])
        low,high = np.full(first.shape,np.inf),np.full(first.shape,-np.inf)
        count = np.zeros(first.shape,dtype='u1')
        for p,i in sequence:
            v = values(files[p],i)
            finite = np.isfinite(v)
            low = np.minimum(low,np.where(finite,v,np.inf))
            high = np.maximum(high,np.where(finite,v,-np.inf))
            count += finite
        return np.where(count==24,low,np.nan),np.where(count==24,high,np.nan)


def independent_target(root,layout,sources,scratch):
    terrain = root/'forcing/static/prism/prism_an_4km_elevation.nc'
    with open_normalized_forcing(sources['tmin'],'prism_tmin') as lo, \
         open_normalized_forcing(sources['tmax'],'prism_tmax') as hi, xr.open_dataset(terrain) as z:
        lower = lo['daily_minimum_temperature'].squeeze(drop=True)
        upper = hi['daily_maximum_temperature'].squeeze(drop=True)
        height = np.asarray(z['elevation'].squeeze(),dtype='f8')
        if height.shape != lower.shape or height.shape != upper.shape:
            raise ValueError('Source-grid shape mismatch')
        data = xr.Dataset(coords={d:lo.coords[d] for d in lower.dims})
        for name,temp in [('low',lower),('high',upper)]:
            v = np.asarray(temp,dtype='f8')
            valid = np.isfinite(v)&np.isfinite(height)
            data[name] = (lower.dims,np.where(valid,v,np.nan))
            data[name+'_z'] = (lower.dims,np.where(valid,height,np.nan))
        data.to_netcdf(scratch/'independent-source.nc')
    weights = root/'forcing/static/remapping/nwm_conus_1km/prism_bilinear.nc'
    subprocess.run([shutil.which('cdo') or 'cdo','-O','-b','F64',
                    f'remap,{layout.target_grid},{weights}',str(scratch/'independent-source.nc'),
                    str(scratch/'independent-remapped.nc')],check=True,capture_output=True,text=True)
    with xr.open_dataset(scratch/'independent-remapped.nc') as ds, xr.open_dataset(layout.target_elevation) as z:
        # Explicit, independently expressed -6.5 K/km lapse correction.
        elevation = np.asarray(z['elevation'].squeeze(),dtype='f8')
        return tuple(np.asarray(ds[n].squeeze())-0.0065*(elevation-np.asarray(ds[n+'_z'].squeeze()))
                     for n in ('low','high'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign',required=True,type=Path)
    parser.add_argument('--case',required=True,type=int,choices=range(len(CASES)))
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    campaign = args.campaign.resolve()
    if not campaign.is_relative_to(root/'forcing/work/temperature-refresh'):
        raise ValueError('Dedicated campaign required')
    day = date.fromisoformat(CASES[args.case])
    job = os.environ['SLURM_JOB_ID']
    work = root/'forcing/work/temperature-refresh-diagnostics'/job
    work.mkdir(parents=True,exist_ok=False)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/independent-temperature')
    scratch.mkdir(parents=True,exist_ok=False)
    report = {'status':'running','day':str(day),'job':job,'production_modified':False,
              'publication_released':False,'historical_cause':'not established by this diagnostic',
              'diagnostic_version':2,'coverage_scope':'static-envelope active variable',
              'hourly_nonfinite_policy':'fail on any required nonfinite value'}
    try:
        plan = json.loads((campaign/'plan.json').read_text())
        layout = OperationalLayout.project_defaults(root)
        sources = {v:Path(plan['prism_sources'][f'{v}/{day}']['path']) for v in ('tmin','tmax')}
        source_pins = {v:source_signature(p) for v,p in sources.items()}
        if any(pin != plan['prism_sources'][f'{v}/{day}'] for v,pin in source_pins.items()):
            raise ValueError('Source superseded')
        paths = {}
        for group,base in [('original',campaign/'originals/nrt'),('candidate',campaign/'candidates/nrt'),
                           ('baseline',campaign/'baseline')]:
            paths[group] = [base/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1') for d in (day-timedelta(days=1),day)]
        paths['window'] = [campaign/'windows'/f'{day}.nc']
        pinned_paths = [p for group in paths.values() for p in group]+list(sources.values())+[
            layout.target_grid,layout.target_elevation,root/'forcing/static/prism/prism_an_4km_elevation.nc',
            root/'forcing/static/remapping/nwm_conus_1km/prism_bilinear.nc',
            root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc']
        pins = {str(p):list(fingerprint(p)) for p in pinned_paths}
        report['input_pins'] = pins
        sequence = {k:records(v,day) for k,v in paths.items()}
        low,high = independent_target(root,layout,sources,scratch)
        target = (low+high)/2
        active = operational_active(root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc')
        mask = active & np.isfinite(target) & (high>=low)
        report['required_active_cells'] = int(active.sum())
        report['prism_constraint_active_cells'] = int(mask.sum())
        report['active_cells_without_prism_target'] = int((active & ~mask).sum())
        bounds = {k:extrema(s) for k,s in sequence.items()}
        report['midpoint'] = {k:statistics((a+b)/2,target,mask) for k,(a,b) in bounds.items()}
        report['incomplete_active_cells'] = {k:int((active & ~(np.isfinite(a)&np.isfinite(b))).sum())
                                             for k,(a,b) in bounds.items()}
        blo,bhi = bounds['baseline']
        midpoint = (blo+bhi)/2
        span = bhi-blo
        scale = np.clip(np.where(span<0.5,1.0,(high-low)/np.where(span<0.5,1.0,span)),0.25,4.0)
        errors = {'candidate_vs_affine_max_k':0.0,'window_vs_affine_max_k':0.0,'candidate_vs_window_max_k':0.0}
        with ExitStack() as stack:
            files = {p:stack.enter_context(Dataset(p)) for p in {p for s in sequence.values() for p,_ in s}}
            for h in range(24):
                fields = {k:values(files[s[h][0]],s[h][1]) for k,s in sequence.items() if k != 'original'}
                expected = target+scale*(fields['baseline']-midpoint)
                for key,a,b in [('candidate_vs_affine_max_k',fields['candidate'],expected),
                                ('window_vs_affine_max_k',fields['window'],expected),
                                ('candidate_vs_window_max_k',fields['candidate'],fields['window'])]:
                    comparison_mask = active if key == 'candidate_vs_window_max_k' else mask
                    errors[key] = max(errors[key],maximum_error(a,b,comparison_mask))
        report.update(errors)
        for p,pin in pins.items():
            if list(fingerprint(Path(p))) != pin:
                raise ValueError('Diagnostic input changed')
        candidate_ok = all(report['midpoint'][k]['max_abs_k'] <= .001 for k in ('candidate','window'))
        report['status'] = 'passed' if (candidate_ok and all(v<=.001 for v in errors.values())
                           and all(report['incomplete_active_cells'][k]==0 for k in ('candidate','window','baseline'))) else 'mismatch'
        fields = {'independent_prism_midpoint':target,'original_midpoint_error':sum(bounds['original'])/2-target,
                  'candidate_midpoint_error':sum(bounds['candidate'])/2-target,
                  'window_midpoint_error':sum(bounds['window'])/2-target}
        xr.Dataset({k:(('y','x'),np.where(mask,v,np.nan).astype('f4')) for k,v in fields.items()}).to_netcdf(
            work/'spatial-diagnostics.nc',encoding={k:{'zlib':True,'complevel':2} for k in fields})
        report['maps_sha256'] = digest(work/'spatial-diagnostics.nc')
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        atomic_json(work/'report.json',report)
        print(json.dumps(report),flush=True)
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
