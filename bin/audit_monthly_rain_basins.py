#!/usr/bin/env python3
"""Bounded basin-scale loss screen of 1979–1980 stored precipitation; no repair."""
import argparse
import json
import os
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from netCDF4 import Dataset
from pyproj import Transformer
from shapely.ops import transform

from hydro_ops.forcing.prism_volume_audit import region_totals


def atomic_json(path, record):
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as handle:
        temporary=Path(handle.name)
        json.dump(record,handle,indent=2,allow_nan=False)
        handle.write('\n')
    os.replace(temporary,path)


def loss_metrics(target, stored, weights):
    """Keep deficits separate from surpluses so cancellation cannot hide loss."""
    result = region_totals(target, stored, weights)
    use = weights>0
    area = weights[use].sum()
    deficit = float(np.sum(np.maximum(target[use]-stored[use],0)*weights[use])/area)
    surplus = float(np.sum(np.maximum(stored[use]-target[use],0)*weights[use])/area)
    result.update(deficit_mm=deficit,surplus_mm=surplus,
                  deficit_percent=100*deficit/result['prism_mean_mm'] if result['prism_mean_mm']>0 else None)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-array',required=True,type=int)
    args=parser.parse_args()
    root=Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    out=root/'forcing/work/monthly-rain-basin-audit'/os.environ['SLURM_JOB_ID']
    out.mkdir(parents=True,exist_ok=False)
    report={'status':'running','production_modified':False,'regions':{}}
    atomic_json(out/'acceptance.json',report)
    try:
        rfc=gpd.read_file(root/'forcing/static/noaa/rfc_boundaries/20240112/rf05mr24.shp').to_crs(4326)
        geometries=[('RFC_'+str(row.SITE_ID),row.geometry) for row in rfc.itertuples()]
        basins=gpd.read_file(root/'forcing/work/prism-basin-audit/5092473/forecast_basins.gpkg').to_crs(4326).dissolve(by='Basin').reset_index()
        geometries += [('CNRFC_basin_'+str(row.Basin),row.geometry) for row in basins.itertuples()]
        for domain,layer in [('cnrfc','cnrfc_hu18_union'),('ucrb','ucrb_reporting'),
                             ('yampa','yampa_reporting'),('b120','b120_union')]:
            frame=gpd.read_file(root/f'nwm/static/domains/{domain}/boundaries/{domain}_boundaries.gpkg',layer=layer).to_crs(4326)
            geometries.append((domain+'_reporting',frame.geometry.union_all()))
        for domain,layer,field in [('ucrb','wbd_hu4_context','huc4'),('yampa','wbd_hu8_selected','huc8')]:
            frame=gpd.read_file(root/f'nwm/static/domains/{domain}/boundaries/{domain}_boundaries.gpkg',layer=layer).to_crs(4326)
            field=next(c for c in frame.columns if c.lower()==field)
            geometries += [(f'{domain}_{row[field]}',row.geometry) for _,row in frame.iterrows()]
        weights=root/'forcing/static/remapping/nwm_conus_1km/nwm_to_prism_conservative_masked.nc'
        with Dataset(weights) as ds:
            lon=(np.rad2deg(np.asarray(ds['dst_grid_corner_lon'][:]))+180)%360-180
            lat=np.rad2deg(np.asarray(ds['dst_grid_corner_lat'][:]))
            full_area=np.asarray(ds['dst_grid_area'][:])
        labels=[];targets=[];stored=[];area=None;active=None
        for year in (1979,1980):
            for month in range(1,13):
                label=f'{year}{month:02}'
                directory=root/f'forcing/work/monthly-rain-repair/{args.audit_array}/{label}'
                accepted=json.loads((directory/'acceptance.json').read_text())
                if accepted['status']!='passed':
                    raise ValueError(f'Month not audited: {label}')
                with Dataset(directory/'spatial_diagnostics.nc') as ds:
                    targets.append(np.ma.asarray(ds['target_mm'][:]).filled(np.nan))
                    stored.append(np.ma.asarray(ds['stored_mm'][:]).filled(np.nan))
                    current=np.asarray(ds['covered_area'][:])
                    if area is not None:
                        np.testing.assert_array_equal(area,current)
                    area=current
                    active=np.asarray(ds['active_link_fraction'][:])
                labels.append(label)
        targets,stored=np.stack(targets),np.stack(stored)
        common=np.all(np.isfinite(targets)&np.isfinite(stored),axis=0)&(area>0)
        projection=Transformer.from_crs(4326,5070,always_xy=True)
        xlo,xhi,ylo,yhi=lon.min(axis=1),lon.max(axis=1),lat.min(axis=1),lat.max(axis=1)
        for name,geometry in geometries:
            geometry=shapely.make_valid(geometry)
            x0,y0,x1,y1=geometry.bounds
            ix=np.flatnonzero((xhi>=x0)&(xlo<=x1)&(yhi>=y0)&(ylo<=y1))
            if not ix.size:
                continue
            x,y=projection.transform(lon[ix],lat[ix])
            pixels=shapely.polygons(np.stack((x,y),axis=-1))
            fractions=np.clip(shapely.area(shapely.intersection(pixels,transform(projection.transform,geometry)))/shapely.area(pixels),0,1)
            w=np.where(common[ix],area[ix]*fractions,0)
            if not w.sum():
                report['regions'][name]={'status':'no_common_support'}
                continue
            rows=[{'month':label,**loss_metrics(t[ix],s[ix],w)}
                  for label,t,s in zip(labels,targets,stored,strict=True)]
            annual={str(year):loss_metrics(targets[i:i+12,ix].sum(axis=0),
                      stored[i:i+12,ix].sum(axis=0),w) for year,i in [(1979,0),(1980,12)]}
            report['regions'][name]={'status':'diagnostic_only','months':rows,'years':annual,
                'supported_prism_cells':int(np.count_nonzero(w)),
                'covered_fraction_of_in_grid_area':float(w.sum()/np.sum(full_area[ix]*fractions))}
        points=[]
        for label,t,s in zip(labels,targets,stored,strict=True):
            eligible=np.flatnonzero(np.isfinite(t)&np.isfinite(s)&(area>0))
            worst=eligible[np.argsort(abs(s[eligible]-t[eligible]))[-10:][::-1]]
            for i in worst:
                x,y=float(lon[i].mean()),float(lat[i].mean())
                point=shapely.Point(x,y)
                regions=[str(row.SITE_ID) for row in rfc.itertuples() if row.geometry.covers(point)]
                points.append({'type':'Feature','geometry':{'type':'Point','coordinates':[x,y]},
                    'properties':{'month':label,'prism_cell':int(i),'target_mm':float(t[i]),
                                  'stored_mm':float(s[i]),'bias_mm':float(s[i]-t[i]),
                                  'active_link_fraction':float(active[i]),'rfc':regions}})
        atomic_json(out/'largest_discrepancies.geojson',{'type':'FeatureCollection','features':points})
        ranked=[]
        for name,r in report['regions'].items():
            for m in r.get('months',[]):
                if m['prism_mean_mm']>=1:
                    ranked.append({'region':name,**m,'covered_fraction':r['covered_fraction_of_in_grid_area']})
        report.update(status='diagnostics_complete',common_support_cells=int(common.sum()),
                      worst_deficit_months=sorted(ranked,key=lambda r:r['deficit_percent'],reverse=True)[:30],
                      worst_net_loss_months=sorted(ranked,key=lambda r:r['bias_percent'])[:30],
                      scope='fractional basin overlap on PRISM grid; stored NWM monthly rain conservatively mapped back',
                      limitations='Common supported PRISM area only; RFCs are broad regions, not outlet basins. Annual deficits net temporal cancellation within cells; monthly deficits are also retained. Not a nationwide small-watershed audit.',
                      scientific_acceptance='requires review; no production replacement or automatic rejection')
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        atomic_json(out/'acceptance.json',report)


if __name__=='__main__':
    main()
