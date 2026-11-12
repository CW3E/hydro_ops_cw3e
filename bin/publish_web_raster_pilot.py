"""Build a standalone, versioned pilot package; never modify scientific archives."""
import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import netCDF4
import numpy as np

from hydro_ops.forcing.web_rasters import corners, legends, mercator, render


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def identity(path):
    s = path.stat()
    return [s.st_ino, s.st_size, s.st_mtime_ns]


def main():
    root = Path(__file__).resolve().parents[1]
    output = root/'web/publications'/f'pilot-{os.environ["SLURM_JOB_ID"]}'
    output.mkdir(parents=True, exist_ok=False)
    palettes = legends()
    write(output/'legends.json', palettes)
    families = []
    pins = {}
    for domain, land_job, forcing_job in [('cnrfc', '5260671', '5263647'), ('ucrb', '5260679', '5263648')]:
        folder = output/domain
        (folder/'images').mkdir(parents=True)
        grid_path = root/f'forcing/outputs/{domain}/retro/daily/2023/04/20230401.LDASIN_DOMAIN1.daily'
        with netCDF4.Dataset(grid_path) as ds:
            lat, lon = ds['lat'][:], ds['lon'][:]
            xc, yc = mercator(corners(ds['lon_bnds'][:]), corners(ds['lat_bnds'][:]))
            mask = np.asarray(ds['subset_forcing_mask'][:], bool)
        pins[grid_path] = identity(grid_path)
        # Full current subset grid footprint, not legacy placement or date-dependent valid values.
        extent = [float(xc.min()), float(yc.min()), float(xc.max()), float(yc.max())]
        size = [int(np.ceil((extent[2]-extent[0])/1500)), int(np.ceil((extent[3]-extent[1])/1500))]
        west, east = np.rad2deg(np.array(extent)[[0, 2]]/6378137)
        south, north = np.rad2deg(2*np.arctan(np.exp(np.array(extent)[[1, 3]]/6378137))-np.pi/2)
        placement = {'crs': 'OGC:CRS84', 'cornerOrder': ['top-left', 'top-right', 'bottom-right', 'bottom-left'],
                     'corners': [[west, north], [east, north], [east, south], [west, south]],
                     'pixelReference': 'outer-edges', 'rowOrder': 'north-to-south'}
        assets = []
        for key in ['20150401', '20230401', '20250930']:
            day = date.fromisoformat(f'{key[:4]}-{key[4:6]}-{key[6:]}')
            month = key[:6]
            daily = root/f'forcing/outputs/{domain}/retro/daily/{key[:4]}/{key[4:6]}/{key}.LDASIN_DOMAIN1.daily'
            land = root/f'nwm/analysis/{domain}/retro/climatology/wy1980-2025/maps-{land_job}/{key}.land_percentiles.nc'
            monthly = root/f'forcing/analysis/{domain}/retro/monthly-climatology/wy1980-2025/job_{forcing_job}/maps-5263662/{month}.forcing_percentiles.nc'
            month_report = json.loads((monthly.parent/f'{month}.json').read_text())
            if month_report['status'] != 'passed':
                raise ValueError('Monthly rank preview not accepted')
            land_report = root/f'nwm/status/{domain}/climatology/maps-{land_job}/{key}.json'
            accepted_land = json.loads(land_report.read_text())
            if accepted_land['status'] != 'passed':
                raise ValueError('Daily rank preview not accepted')
            expected_hash = next(a['sha256'] for a in accepted_land['artifacts'] if Path(a['path']).name == land.name)
            if hashlib.sha256(land.read_bytes()).hexdigest() != expected_hash:
                raise ValueError('Daily ranks changed after acceptance')
            for path, fields, product, resolution in [
                (daily, [('RAINRATE', 'precipitation'), ('T2D', 'temperature')], 'forcing', 'daily'),
                (land, [('SOIL_WATER_PERCENTILE', 'soil_water_percentile'), ('SNEQV_PERCENTILE', 'swe_percentile')], 'nwm', 'daily'),
                (monthly, [('RAINRATE_PERCENTILE', 'precipitation_percentile'), ('T2D_PERCENTILE', 'temperature_percentile')], 'forcing', 'monthly'),
            ]:
                pins[path] = identity(path)
                start = day if resolution == 'daily' else day.replace(day=1)
                end = start+timedelta(days=1) if resolution == 'daily' else (start.replace(day=28)+timedelta(days=4)).replace(day=1)
                with netCDF4.Dataset(path) as ds:
                    np.testing.assert_allclose(ds['lat'][:], lat, atol=1e-5)
                    np.testing.assert_allclose(ds['lon'][:], lon, atol=1e-5)
                    for field, variable in fields:
                        values = np.ma.asarray(ds[field][:], dtype=float).filled(np.nan).squeeze()
                        conversion = 'none'
                        if variable == 'precipitation':
                            tv = ds['time']
                            bounds = netCDF4.num2date(ds['time_bounds'][0], tv.units, getattr(tv, 'calendar', 'standard'))
                            seconds = (bounds[1]-bounds[0]).total_seconds()
                            if seconds != 86400 or str(bounds[0])[:10] != str(start):
                                raise ValueError('Daily interval mismatch')
                            if ds[field].units not in ('kg m-2 s-1', 'kg/m^2/s'):
                                raise ValueError('Unexpected precipitation units')
                            values *= seconds
                            conversion = 'mean RAINRATE multiplied by time_bounds duration (seconds) -> mm'
                        if variable == 'temperature':
                            if ds[field].units != 'K':
                                raise ValueError('Unexpected temperature units')
                            values -= 273.15
                            conversion = 'K minus 273.15 -> degC'
                        values[~mask] = np.nan
                        label = key if resolution == 'daily' else month
                        name = f'{domain}_{product}_retro_{resolution}_{variable}_{label}.png'
                        target = folder/'images'/name
                        validation = render(target, values, xc, yc, extent, size, palettes[variable])
                        assets.append({'href': 'images/'+name, 'mediaType': 'image/png', 'encoding': 'precolored-rgba',
                            'variable': variable, 'product': product, 'stream': 'retro', 'resolution': resolution,
                            'date': str(start), 'validInterval': {'start': str(start)+'T00:00:00Z',
                            'end': str(end)+'T00:00:00Z', 'closure': '[start,end)', 'label': 'start'},
                            'source': str(path.relative_to(root)), 'field': field, 'conversion': conversion,
                            'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), **validation})
        family = {'convention': 'hydro-ops-raster-pilot', 'conventionVersion': '0.1',
            'id': f'hydro_ops_{domain}_v1', 'type': 'raster-family', 'domain': domain,
            'status': 'pilot; browser adapter and scientific visual review pending',
            'producer': 'CW3E Hydro Ops', 'reuseTerms': 'Confirm public reuse terms before server deployment',
            'pixelProjection': 'EPSG:3857', 'projectedExtent': extent, 'width': size[0], 'height': size[1],
            'placement': placement, 'gridSource': str(grid_path.relative_to(root)),
            'gridSha256': hashlib.sha256(np.asarray([xc, yc], dtype='<f8').tobytes()).hexdigest(),
            'legends': '../legends.json', 'availability': 'availability.json',
            'dimensions': {'variable': list(palettes), 'product': ['forcing', 'nwm'],
                'stream': ['retro'], 'resolution': ['daily', 'monthly'],
                'availabilityRule': 'Only explicit asset combinations exist, not the Cartesian product'},
            'calendar': 'gregorian', 'timezone': 'UTC',
            'statistics': {'referencePeriod': ['1979-10-01', '2025-09-30'],
                'ranks': 'Non-exceedance percentile ranks, 0-100; table inversion, tied thresholds use midranks, tails clipped to 0.5/99.5',
                'dailyWindowDays': 31, 'sweRankMinimumMm': 1, 'sweMinimumRule': 'values <= 1 mm are missing ranks',
                'monthly': 'Calendar-month climatology; T2D and RAINRATE means',
                'soilWater': 'Layer-depth-weighted column soil water, not unweighted SOIL_M sum'},
            'publication': 'Immutable pilot directory; only listed assets exist; no legacy families reused',
            'paletteSource': 'https://github.com/fallspinach/hydromet-map/blob/9f91e4242470e1b3701833c61b31a30ecf4e247a/src/config/mapConfig.js',
            'paletteChanges': '5 degC rainbow bins; precipitation below 2.5 mm hidden; central percentile bins hidden'}
        write(folder/'availability.json', {'assets': assets})
        write(folder/'family.json', family)
        families.append({'id': family['id'], 'href': domain+'/family.json', 'images': len(assets)})
    if any(identity(p) != pin for p, pin in pins.items()):
        raise ValueError('Inputs changed during rendering; do not publish')
    write(output/'acceptance.json', {'status': 'passed', 'scope': 'PNG dimensions, binary alpha, coordinate alignment, accepted rank previews and unchanged inputs',
        'browserValidated': False, 'scientificVisualReview': 'pending',
        'sourceIdentities': {str(p.relative_to(root)): pin for p, pin in pins.items()}})
    # Catalog is the last publication marker; an incomplete job must never be indexed.
    write(output/'catalog.json', {'families': families, 'publishedAt': datetime.now(UTC).isoformat()})
    print(output, flush=True)


if __name__ == '__main__':
    main()
