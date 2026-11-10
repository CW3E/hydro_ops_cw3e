"""Compact, internal PRISM-window storage; not model-ready forcing."""
import numpy as np
from netCDF4 import Dataset, date2num, num2date


def create_rain_window(paths, destination, day, *, source_time_indices,
                       time_variable_overrides, global_attributes, **unused):
    if set(time_variable_overrides) != {'RAINRATE'}:
        raise ValueError('Compact windows support precipitation only')
    rain = np.asarray(time_variable_overrides['RAINRATE'])
    if rain.ndim != 3 or rain.shape[0] != 24:
        raise ValueError('Expected 24 rain fields')
    with Dataset(paths[0]) as source, Dataset(destination, 'w') as out:
        for name, size in zip(('time', 'y', 'x'), rain.shape, strict=True):
            out.createDimension(name, size)
        t = out.createVariable('time', 'f8', ('time',))
        t.units = source['time'].units
        t.calendar = getattr(source['time'], 'calendar', 'standard')
        src = source['RAINRATE']
        v = out.createVariable('RAINRATE', src.dtype, ('time', 'y', 'x'),
                               fill_value=getattr(src, '_FillValue', -9999.),
                               zlib=True, complevel=2,
                               chunksizes=(1, min(128, rain.shape[1]), min(128, rain.shape[2])))
        v.setncatts({k: src.getncattr(k) for k in src.ncattrs()
                    if k not in ('_FillValue', 'vmin', 'vmax', 'coordinates', 'grid_mapping')})
        for i, (path, index) in enumerate(zip(paths, source_time_indices, strict=True)):
            with Dataset(path) as ds:
                stamp = num2date(ds['time'][index], ds['time'].units,
                                 calendar=getattr(ds['time'], 'calendar', 'standard'))
            t[i] = date2num(stamp, t.units, calendar=t.calendar)
        v[:] = np.ma.masked_invalid(rain)
        out.setncatts(global_attributes)
        out.product_type = 'internal_precipitation_window_not_model_ready'
        out.archive_period = str(day)
