"""Compare day-to-day structure of inferred old-versus-current PRISM targets.

Read-only diagnostic. A static pattern supports but cannot prove a static-input
explanation. Variation rules out a single fixed additive correction as the sole
cause; it does not by itself identify provider revisions.
"""
import argparse
import json
import os
import subprocess
from itertools import combinations
from pathlib import Path

import numpy as np
import xarray as xr
from netCDF4 import Dataset

from hydro_ops.forcing.normalize import open_normalized_forcing
from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json


def describe(values):
    v=np.asarray(values,dtype='f8')
    if not v.size or not np.isfinite(v).all():
        raise ValueError('Expected finite nonempty statistics domain')
    return {'mean':float(v.mean()),'rms':float(np.sqrt(np.mean(v*v))),
            'p50_abs':float(np.percentile(np.abs(v),50)),
            'p99_abs':float(np.percentile(np.abs(v),99)),'max_abs':float(np.abs(v).max())}


def correlation(a,b):
    a,b=np.asarray(a,dtype='f8'),np.asarray(b,dtype='f8')
    a,b=a-a.mean(),b-b.mean()
    denominator=np.sqrt(np.dot(a,a)*np.dot(b,b))
    return float(np.dot(a,b)/denominator) if denominator>0 else None


def compare(errors,days):
    if errors.ndim != 2 or len(days)!=errors.shape[0] or len(set(days))!=len(days) or len(days)<2:
        raise ValueError('Distinct dates and aligned error vectors required')
    if not errors.shape[1] or not np.isfinite(errors).all():
        raise ValueError('Finite common cells required')
    mean=errors.mean(axis=0)
    spread=errors.max(axis=0)-errors.min(axis=0)
    varying=errors-mean
    energy=float(np.sum(errors*errors))
    pairs={f'{days[i]}__{days[j]}':{'correlation':correlation(errors[i],errors[j]),
                                  'difference_k':describe(errors[j]-errors[i])}
           for i,j in combinations(range(len(days)),2)}
    return {'common_cells':errors.shape[1],'pairs':pairs,
            'per_day':{day:describe(e) for day,e in zip(days,errors,strict=True)},
            'mean_error_k':describe(mean),'day_to_day_spread_k':describe(spread),
            'fixed_spatial_fraction_of_squared_error':float(len(days)*np.sum(mean*mean)/energy) if energy else None,
            'varying_component_rms_k':float(np.sqrt(np.mean(varying*varying))),
            'cells_spread_le_0_001k':int((spread<=.001).sum()),
            'cells_spread_gt_0_1k':int((spread>.1).sum())}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports',nargs='+',type=Path,required=True)
    args=parser.parse_args()
    root=Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    job=os.environ['SLURM_JOB_ID']
    work=root/'forcing/work/temperature-target-comparison'/job
    work.mkdir(parents=True,exist_ok=False)
    scratch=Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/target-comparison')
    scratch.mkdir(parents=True,exist_ok=False)
    report={'status':'running','production_modified':False,'publication_released':False,
            'interpretation':'Pattern comparison alone cannot identify the historical PRISM release.'}
    try:
        maps,days,pins=[],[],{}
        for path in args.reports:
            path=path.resolve()
            if not path.is_relative_to(root/'forcing/work/temperature-refresh-diagnostics'):
                raise ValueError('Expected independent diagnostic report')
            r=json.loads(path.read_text())
            if r.get('diagnostic_version')!=2 or r['status']!='passed':
                raise ValueError('Accepted version-2 report required')
            map_path=path.parent/'spatial-diagnostics.nc'
            if digest(map_path)!=r['maps_sha256']:
                raise ValueError('Spatial diagnostics changed')
            for p,pin in r['input_pins'].items():
                if list(fingerprint(Path(p)))!=pin:
                    raise ValueError('Original diagnostic inputs changed')
            pins.update(r['input_pins'])
            pins.update({str(p):list(fingerprint(p)) for p in (path,map_path)})
            with Dataset(map_path) as ds:
                maps.append(np.ma.asarray(ds['original_midpoint_error'][:],dtype='f8').filled(np.nan))
            days.append(r['day'])
        common=np.logical_and.reduce([np.isfinite(m) for m in maps])
        errors=np.stack([m[common] for m in maps])
        report.update(compare(errors,days))
        layout=OperationalLayout.project_defaults(root)
        source=root/'forcing/static/prism/prism_an_4km_elevation.nc'
        weights=root/'forcing/static/remapping/nwm_conus_1km/prism_bilinear.nc'
        for p in (source,weights,layout.target_elevation,layout.target_grid):
            pins[str(p)]=list(fingerprint(p))
        template=next(Path(p) for p in pins if '/daily/tmin/' in p and p.endswith('.nc'))
        # Static terrain lacks the coordinate metadata CDO expects. Use the same
        # normalized PRISM coordinates as target construction, not its raw file.
        with open_normalized_forcing(template,'prism_tmin') as grid, xr.open_dataset(source) as terrain:
            dims=grid['daily_minimum_temperature'].squeeze(drop=True).dims
            elevation=np.asarray(terrain['elevation'].squeeze(),dtype='f8')
            if elevation.shape!=grid['daily_minimum_temperature'].squeeze(drop=True).shape:
                raise ValueError('Terrain/template grid mismatch')
            xr.Dataset({'elevation':(dims,elevation)},coords={d:grid.coords[d] for d in dims}).to_netcdf(
                scratch/'source-elevation.nc')
        try:
            subprocess.run(['cdo','-O','-b','F64',f'remap,{layout.target_grid},{weights}',
                            str(scratch/'source-elevation.nc'),str(scratch/'source-elevation-on-target.nc')],
                           check=True,capture_output=True,text=True)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(f'Elevation remapping failed: {error.stderr}') from error
        with Dataset(scratch/'source-elevation-on-target.nc') as ds:
            zsource=np.ma.asarray(ds['elevation'][:],dtype='f8').filled(np.nan).squeeze()
        with Dataset(layout.target_elevation) as ds:
            ztarget=np.ma.asarray(ds['elevation'][:],dtype='f8').filled(np.nan).squeeze()
        correction=-.0065*(ztarget-zsource)
        terrain_valid=np.isfinite(correction[common])
        corr=correction[common][terrain_valid]
        if not corr.size:
            raise ValueError('No finite terrain comparison cells')
        report['lapse_hypothesis']={}
        for day,e in zip(days,errors,strict=True):
            v=e[terrain_valid]
            report['lapse_hypothesis'][day]={
                'cells':corr.size,'correlation_error_with_lapse_correction':correlation(v,corr),
                'residual_if_old_omitted_lapse_k':describe(v+corr),
                'residual_if_old_doubled_lapse_k':describe(v-corr)}
        derived={}
        for name,v in [('mean_error_k',errors.mean(axis=0)),('day_to_day_std_k',errors.std(axis=0)),
                       ('day_to_day_spread_k',np.ptp(errors,axis=0))]:
            field=np.full(common.shape,np.nan,dtype='f4');field[common]=v
            derived[name]=(('y','x'),field)
        derived['current_lapse_correction_k']=(('y','x'),np.where(common,correction,np.nan).astype('f4'))
        xr.Dataset(derived,attrs={'days':','.join(days),'production_modified':'false'}).to_netcdf(
            work/'target-patterns.nc',encoding={k:{'zlib':True,'complevel':2} for k in derived})
        if any(list(fingerprint(Path(p)))!=pin for p,pin in pins.items()):
            raise ValueError('Diagnostic input changed')
        report.update(status='diagnosed',input_pins=pins,days=days,
                      patterns_sha256=digest(work/'target-patterns.nc'))
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        atomic_json(work/'report.json',report)
        print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
