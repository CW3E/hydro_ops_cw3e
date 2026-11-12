import numpy as np
import pytest
from PIL import Image

from hydro_ops.forcing.web_rasters import legends, mercator, render, save_indexed_png


def test_legends():
    rules = legends()
    assert len(rules) == 6
    for rule in rules.values():
        assert len(rule['colors']) == len(rule['thresholds'])+1
        assert all(b['includeLower'] and not b['includeUpper'] for b in rule['bins'])
    assert rules['temperature']['thresholds'] == list(range(-15, 41, 5))
    assert rules['precipitation']['colors'][0].endswith('00')
    assert rules['soil_water_percentile']['bins'][5]['lower'] == 30
    assert rules['precipitation_percentile']['bins'][5]['upper'] == 65


def test_render_boundaries(tmp_path):
    x, y = np.meshgrid(np.arange(6), np.arange(2))
    path = tmp_path/'sample.png'
    render(path, np.array([[np.nan, 2.49, 2.5, 5, 1000]]), x, y, [0, 0, 5, 1], [500, 100], legends()['precipitation'])
    pixels = np.asarray(Image.open(path))
    assert list(pixels[50, [50, 150, 250, 350, 450], 3]) == [0, 0, 255, 255, 255]
    assert tuple(pixels[50, 250, :3]) == (0, 255, 255)
    assert tuple(pixels[50, 350, :3]) == (0, 224, 128)
    a, b = mercator(np.array([0]), np.array([0]))
    np.testing.assert_allclose([a[0], b[0]], [0, 0], atol=1e-8)


def test_rank_transparency_and_north_up(tmp_path):
    x, y = np.meshgrid(np.arange(5), np.arange(3))
    path = tmp_path/'ranks.png'
    values = np.array([[29.9, 30, 69.9, 70], [np.nan, -1, 101, np.nan]])
    render(path, values, x, y, [0, 0, 4, 2], [400, 200], legends()['soil_water_percentile'])
    pixels = np.asarray(Image.open(path))
    assert list(pixels[150, [50, 150, 250, 350], 3]) == [255, 0, 0, 255]
    assert not pixels[50, :, 3].any()


def test_indexed_exact_alpha(tmp_path):
    rgba = np.array([[[255, 255, 255, 0], [0, 0, 0, 0], [0, 0, 0, 255],
                      [12, 34, 56, 128], [12, 34, 56, 255]]], dtype='u1')
    source, target = tmp_path/'source.png', tmp_path/'indexed.png'
    Image.fromarray(rgba).save(source)
    result = save_indexed_png(source, target)
    assert result['indexBits'] == 4 and result['colors'] == 5
    with Image.open(target) as image:
        assert image.mode == 'P'
        np.testing.assert_array_equal(np.array(image.convert('RGBA')), rgba)


def test_indexed_rejects_lossy_conversion(tmp_path):
    rgba = np.zeros((1, 257, 4), dtype='u1')
    rgba[0, :256, 0] = np.arange(256)
    rgba[0, 256, 1] = 1
    source, target = tmp_path/'source.png', tmp_path/'indexed.png'
    Image.fromarray(rgba).save(source)
    with pytest.raises(ValueError, match='<=256'):
        save_indexed_png(source, target)
    assert not target.exists()


@pytest.mark.parametrize('colors, bits', [(1, 1), (2, 1), (4, 2), (16, 4), (256, 8)])
def test_indexed_palette_sizes(tmp_path, colors, bits):
    rgba = np.zeros((1, colors, 4), dtype='u1')
    rgba[0, :, 0] = np.arange(colors)
    source, target = tmp_path/'source.png', tmp_path/'indexed.png'
    Image.fromarray(rgba).save(source)
    assert save_indexed_png(source, target)['indexBits'] == bits
