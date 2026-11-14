"""Create a complete immutable publication with Mexico hidden in SM maps only."""
import argparse
import copy
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import requests
from matplotlib.path import Path as PolygonPath
from PIL import Image

from hydro_ops.forcing.web_rasters import save_indexed_png

URL = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')


def country_mask(geometry, extent, width, height):
    """Pixel-center containment, preserving polygon holes and separate islands."""
    west, south, east, north = extent
    xx = west+(np.arange(width)+.5)*(east-west)/width
    yy = north-(np.arange(height)+.5)*(north-south)/height
    lon = np.rad2deg(xx/6378137)
    lat = np.rad2deg(2*np.arctan(np.exp(yy/6378137))-np.pi/2)
    x, y = np.meshgrid(lon, lat)
    points = np.column_stack([x.ravel(), y.ravel()])
    polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
    mask = np.zeros(len(points), bool)
    for rings in polygons:
        ring = np.asarray(rings[0])[:, :2]
        candidates = ((points[:, 0] >= ring[:, 0].min()) & (points[:, 0] <= ring[:, 0].max()) &
                      (points[:, 1] >= ring[:, 1].min()) & (points[:, 1] <= ring[:, 1].max()))
        indexes = np.flatnonzero(candidates)
        inside = PolygonPath(ring).contains_points(points[indexes])
        for hole in rings[1:]:
            inside &= ~PolygonPath(np.asarray(hole)[:, :2]).contains_points(points[indexes])
        mask[indexes] |= inside
    return mask.reshape(height, width)


def mask_image(source, target, mask):
    with Image.open(source) as image:
        original = np.array(image.convert('RGBA'))
    if original.shape[:2] != mask.shape:
        raise ValueError('Image geometry mismatch')
    modified = original.copy()
    modified[mask] = 0
    raw = target.with_suffix('.rgba.png')
    Image.fromarray(modified).save(raw)
    encoding = save_indexed_png(raw, target)
    raw.unlink()
    with Image.open(target) as image:
        actual = np.array(image.convert('RGBA'))
    np.testing.assert_array_equal(actual[~mask], original[~mask])
    assert not actual[mask].any()
    visible = actual[..., 3] > 0
    return encoding, {'total': int(mask.size), 'visible': int(visible.sum()),
        'transparent': int((~visible).sum()), 'displayExcluded': int(mask.sum()),
        'previouslyTransparentOutsideDisplayExclusion': int((~mask & ~visible).sum()),
        'newlyHiddenVisible': int((mask & (original[..., 3] > 0)).sum())}


def main(source, output):
    accepted = json.loads((source/'acceptance.json').read_text())
    if accepted['status'] != 'passed':
        raise ValueError('Source must have passed publication acceptance')
    output.mkdir(parents=True, exist_ok=False)
    response = requests.get(URL, timeout=180)
    response.raise_for_status()
    features = response.json()['features']
    mexico = [f for f in features if f['properties'].get('ADM0_A3') == 'MEX']
    if len(mexico) != 1:
        raise ValueError('Expected exactly one Mexico feature')
    provenance = output/'provenance'
    provenance.mkdir()
    write(provenance/'mexico.geojson', {'type': 'FeatureCollection', 'features': mexico})
    policy = {'variable': 'soil_water_percentile', 'country': 'Mexico',
        'operation': 'Display only: web-image pixel centers inside Mexico become transparent black.',
        'boundarySource': URL, 'boundarySourceSha256': hashlib.sha256(response.content).hexdigest(),
        'boundaryGeometry': 'provenance/mexico.geojson', 'boundaryGeometrySha256': sha(provenance/'mexico.geojson'),
        'scientificDataChanged': False, 'boundaryScale': 'Natural Earth 1:10 million; cartographic, not cadastral',
        'qualitySemantics': 'sourceCells and preDisplayImagePixels retain original scientific counts. imagePixels describes the final display: visible + displayExcluded + previouslyTransparentOutsideDisplayExclusion = total. displayExcluded includes any already-transparent pixels inside Mexico; it does not mean missing data.'}
    write(output/'display-policy.json', policy)
    for name in ('legends.json', 'variables.json'):
        shutil.copy2(source/name, output/name)
    definitions = json.loads((output/'variables.json').read_text())
    definitions['variables']['soil_water_percentile']['displayExclusion'] = policy
    write(output/'variables.json', definitions)
    if (source/'validation-fixtures').is_dir():
        shutil.copytree(source/'validation-fixtures', output/'validation-fixtures')
    catalog = json.loads((source/'catalog.json').read_text())
    total, changed, reused, newly_hidden = 0, 0, 0, 0
    for entry in catalog['families']:
        rel = Path(entry['href'])
        domain = rel.parent
        folder = output/domain
        folder.mkdir(parents=True)
        family = json.loads((source/rel).read_text())
        mask = country_mask(mexico[0]['geometry'], family['projectedExtent'], family['width'], family['height'])
        availability = json.loads((source/domain/'availability.json').read_text())
        ids = set()
        for asset in availability['assets']:
            if asset['id'] in ids:
                raise ValueError('Duplicate asset')
            ids.add(asset['id'])
            src, dst = source/domain/asset['href'], folder/asset['href']
            if sha(src) != asset['sha256']:
                raise ValueError(f'Source checksum changed: {src}')
            dst.parent.mkdir(parents=True, exist_ok=True)
            if asset['variable'] == 'soil_water_percentile':
                encoding, counts = mask_image(src, dst, mask)
                asset['preDisplaySha256'] = asset['sha256']
                asset['qualityCounts']['preDisplayImagePixels'] = copy.deepcopy(asset['qualityCounts']['imagePixels'])
                asset['qualityCounts']['imagePixels'] = counts
                asset['qualityCounts']['countSemantics'] = policy['qualitySemantics']
                asset['displayExclusion'] = {'country': 'Mexico', 'policy': '../display-policy.json'}
                asset['visiblePixels'] = counts['visible']
                asset['paletteIndexBits'] = encoding['indexBits']
                asset['preDisplayImageStatus'] = asset['imageStatus']
                if not counts['visible']:
                    asset['imageStatus'] = 'display_excluded' if counts['newlyHiddenVisible'] else asset['imageStatus']
                changed += 1
                newly_hidden += counts['newlyHiddenVisible']
                assert counts['total'] == counts['visible']+counts['displayExcluded']+counts['previouslyTransparentOutsideDisplayExclusion']
            else:
                shutil.copy2(src, dst)
                assert sha(dst) == asset['sha256']
                reused += 1
            asset['sha256'] = sha(dst)
            total += 1
        write(folder/'availability.json', availability)
        for name in ('timeline.json', 'coverage.json'):
            shutil.copy2(source/domain/name, folder/name)
        timeline = json.loads((folder/'timeline.json').read_text())
        assert all(len(r['availableAssets']) == 1 and r['preferredAsset'] in ids for r in timeline['entries'])
        family.update(revision=output.name, publishedAt=datetime.now(UTC).isoformat(), displayPolicy='../display-policy.json')
        write(output/rel, family)
        print(f'{domain}: validated {len(ids)} assets; Mexico pixels={int(mask.sum())}', flush=True)
    write(output/'acceptance.json', {'status': 'passed', 'browserValidated': False,
        'sourcePublication': source.name, 'sourceAcceptanceSha256': sha(source/'acceptance.json'),
        'publisherCodeSha256': sha(Path(__file__)), 'images': total, 'soilMoistureImagesProcessed': changed,
        'otherImagesByteIdentical': reused, 'newlyHiddenVisiblePixels': newly_hidden,
        'scope': 'Every source PNG checksum verified. Non-SM PNGs byte identical; SM pixels outside Mexico exactly identical and all Mexico pixels transparent. Display count partitions and exclusive timelines verified.',
        'families': catalog['families'], 'modelCaveat': accepted.get('modelCaveat')})
    catalog.update(revision=output.name, publishedAt=datetime.now(UTC).isoformat())
    write(output/'catalog.json', catalog)
    (output/'README.txt').write_text('Complete replacement publication. Entry point: catalog.json. Transfer this entire folder.\nOnly soil-water percentile images hide Mexico; other images are byte-identical to the accepted parent.\nSee display-policy.json for boundary provenance and quality-count semantics.\nScientific source-cell counts remain pre-display; masked pixels are not missing estimates.\nShort labels, exclusive Retro/NRT timeline, geometry and legends are unchanged.\nEvaluation-only; browser acceptance and public-release approval remain pending.\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    main(args.source, args.output)
