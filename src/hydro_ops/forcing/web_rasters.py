"""Discrete, transparent Web Mercator publication images (no scientific regridding)."""
import numpy as np
from matplotlib import colormaps
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import BoundaryNorm, ListedColormap, to_hex
from matplotlib.figure import Figure
from PIL import Image


def save_indexed_png(source, destination):
    """Exact RGBA dictionary encoding, never quantization or dithering.

    Caller owns publication/atomic replacement. Reject >256 colors rather than
    silently losing data. Transparent RGB and partial alpha are preserved too.
    """
    with Image.open(source) as image:
        rgba = np.array(image.convert('RGBA'))
    packed = np.ascontiguousarray(rgba).view('u4').reshape(-1)
    colors, inverse = np.unique(packed, return_inverse=True)
    if len(colors) > 256:
        raise ValueError(f'Exact indexed PNG needs <=256 RGBA colors, found {len(colors)}')
    palette = colors.view('u1').reshape(-1, 4)
    indexed = Image.fromarray(inverse.astype('u1').reshape(rgba.shape[:2])).convert('P')
    indexed.putpalette(palette[:, :3].ravel().tolist())
    bits = next(b for b in (1, 2, 4, 8) if len(colors) <= 2**b)
    indexed.save(destination, format='PNG', bits=bits, compress_level=9,
                 transparency=palette[:, 3].tobytes())
    with Image.open(destination) as check:
        if check.mode != 'P':
            raise ValueError('Expected indexed PNG')
        np.testing.assert_array_equal(np.asarray(check.convert('RGBA')), rgba)
    return {'colors': len(colors), 'indexBits': bits, 'decodedRgbaIdentical': True}


def legends():
    specs = {
        'soil_water_percentile': ([2, 5, 10, 20, 30, 70, 80, 90, 95, 98],
            '730000 e60000 e69800 fed37f fefe00 ffffff aaf596 4ce600 38a800 145a00 002673', '%', 5),
        'swe_percentile': ([1, 5, 10, 20, 30, 70, 80, 90, 95, 99],
            'b40000 ff2e2e ff5d5d ff8b8b ffb9b9 ffe85d d7d7ff b9b9ff 8b8bff 5d5dff 2e2eb4', '%', 5),
        'precipitation_percentile': ([1, 5, 10, 20, 35, 65, 80, 90, 95, 99],
            '7f3b08 ad5506 d77911 f4a84b fdd198 ffffff e9e9f1 cac9e2 a39ac6 7764a4 502382', '%', 5),
        'temperature_percentile': ([1, 5, 10, 20, 35, 65, 80, 90, 95, 99],
            '3a4cc0 5673e0 7497f5 94b5fe b4cdfa ffffff e7d6cc f5c1a8 f5a182 ea7b60 d34d40', '%', 5),
        'precipitation': ([2.5, 5, 7.5, 10, 15, 20, 30, 40, 50, 70, 100, 150, 200, 250, 300, 400, 500, 750],
            '50d0d0 00ffff 00e080 00c000 80e000 ffff00 ffa000 ff0000 ff2080 f040ff 8020ff 4040ff 202080 202020 808080 e0e0e0 eed4bc daa678 663300', 'mm', 0),
    }
    result = {}
    for name, (thresholds, colors, units, hidden) in specs.items():
        rgba = ['#'+c+('00' if i == hidden else 'ff') for i, c in enumerate(colors.split())]
        result[name] = make_legend(thresholds, rgba, units)
    result['temperature'] = make_legend(list(range(-15, 41, 5)),
        [to_hex(c, keep_alpha=True) for c in colormaps['rainbow'](np.linspace(0, 1, 13))], 'degC')
    return result


def make_legend(thresholds, colors, units):
    assert len(colors) == len(thresholds)+1
    return {'thresholdUnit': units, 'type': 'discrete', 'thresholds': thresholds,
            'colors': colors, 'bins': [
                {'lower': ([None]+thresholds)[i], 'upper': (thresholds+[None])[i],
                 'includeLower': True, 'includeUpper': False, 'color': color}
                for i, color in enumerate(colors)],
            'missing': {'color': '#00000000', 'meaning': 'No valid estimate'},
            'transparentBins': 'Valid values intentionally not highlighted; not missing',
            'resampling': 'Projected native cell footprints, flat colors, no antialiasing',
            'displayConversion': {'scale': 1.8, 'offset': 32, 'unit': 'degF'} if units == 'degC'
                else {'scale': 1/25.4, 'offset': 0, 'unit': 'in', 'decimals': 2} if units == 'mm' else None}


def mercator(lon, lat):
    if np.any(np.abs(lat) >= 85.051129):
        raise ValueError('Outside Web Mercator latitude range')
    return 6378137*np.deg2rad(lon), 6378137*np.log(np.tan(np.pi/4+np.deg2rad(lat)/2))


def corners(bounds):
    """SW, SE, NE, NW quadrilaterals from the forcing grid."""
    b = np.asarray(bounds)
    np.testing.assert_allclose(b[:-1, :, 3], b[1:, :, 0], atol=1e-5)
    np.testing.assert_allclose(b[:, :-1, 1], b[:, 1:, 0], atol=1e-5)
    out = np.empty((b.shape[0]+1, b.shape[1]+1))
    out[:-1, :-1] = b[:, :, 0]
    out[:-1, -1] = b[:, -1, 1]
    out[-1, :-1] = b[-1, :, 3]
    out[-1, -1] = b[-1, -1, 2]
    return out


def render(path, values, x, y, extent, size, legend):
    """Rasterize physical cell footprints directly into a fixed transparent canvas."""
    a = np.ma.masked_invalid(values)
    if legend['thresholdUnit'] == '%':
        a = np.ma.masked_where((a < 0) | (a > 100), a)
    if legend['thresholdUnit'] == 'mm':
        a = np.ma.masked_where(a < 0, a)
    # Color indices use lower-inclusive boundaries, including the final tail.
    indices = np.ma.array(np.searchsorted(legend['thresholds'], a.filled(np.nan), side='right'), mask=a.mask)
    cmap = ListedColormap(legend['colors']).with_extremes(bad=(0, 0, 0, 0))
    fig = Figure(figsize=(size[0]/100, size[1]/100), dpi=100, facecolor=(0, 0, 0, 0))
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1], frameon=False)
    ax.set_axis_off()
    ax.pcolormesh(x, y, indices, cmap=cmap, norm=BoundaryNorm(np.arange(cmap.N+1)-.5, cmap.N),
                  shading='flat', antialiased=False, edgecolors='none', snap=False)
    ax.set_xlim(extent[0], extent[2]); ax.set_ylim(extent[1], extent[3])
    fig.savefig(path, transparent=True, dpi=100, pad_inches=0)
    fig.clear()
    with Image.open(path) as im:
        assert im.mode == 'RGBA' and im.size == tuple(size)
        pixels = np.asarray(im)
        assert set(np.unique(pixels[..., 3])).issubset({0, 255})
        visible = pixels[pixels[..., 3] > 0]
        actual = set(map(tuple, np.unique(visible, axis=0)))
        allowed = {tuple(bytes.fromhex(c[1:])) for c in legend['colors']}
        if not actual <= allowed:
            raise ValueError('Rendered image contains colors outside its legend')
        return {'visiblePixels': int(np.count_nonzero(pixels[..., 3])), 'width': im.width, 'height': im.height}
