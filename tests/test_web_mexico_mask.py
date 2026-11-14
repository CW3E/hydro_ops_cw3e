from pathlib import Path

import numpy as np
from PIL import Image
from test_nwm_forcing_coordinator import load_script


def test_country_mask_holes_and_pixel_orientation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('mask_web_publication_mexico.py')
    geometry = {'type': 'Polygon', 'coordinates': [
        [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]],
        [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]]}
    edge = 6378137*np.deg2rad(4)
    mask = module.country_mask(geometry, [0, 0, edge, edge], 4, 4)
    assert mask.sum() == 12
    assert not mask[1:3, 1:3].any()
    far = module.country_mask(geometry, [10*edge, 0, 11*edge, edge], 4, 4)
    assert not far.any()


def test_only_selected_pixels_change(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = load_script('mask_web_publication_mexico.py')
    rgba = np.array([[[255, 0, 0, 255], [0, 0, 0, 0]],
                     [[0, 255, 0, 255], [0, 0, 255, 255]]], dtype='u1')
    source, target = tmp_path/'source.png', tmp_path/'target.png'
    Image.fromarray(rgba).save(source)
    _, counts = module.mask_image(source, target, np.array([[True, True], [False, False]]))
    assert counts['visible'] == 2 and counts['newlyHiddenVisible'] == 1
    assert counts['displayExcluded'] == 2
    with Image.open(target) as im:
        result = np.asarray(im.convert('RGBA'))
    assert not result[0].any()
    np.testing.assert_array_equal(result[1], rgba[1])
