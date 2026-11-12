"""Copy a pilot publication to lossless indexed PNGs, verifying every pixel."""
import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from hydro_ops.forcing.web_rasters import save_indexed_png


def write(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def decode_seconds(path, repeats=5):
    start = time.perf_counter()
    for _ in range(repeats):
        with Image.open(path) as image:
            image.convert('RGBA').load()
    return (time.perf_counter()-start)/repeats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    acceptance = json.loads((source/'acceptance.json').read_text())
    if acceptance['status'] != 'passed':
        raise ValueError('Source publication has not passed')
    catalog = json.loads((source/'catalog.json').read_text())
    output.mkdir(parents=True, exist_ok=False)
    records = []
    write(output/'legends.json', json.loads((source/'legends.json').read_text()))
    for family_ref in catalog['families']:
        rel = Path(family_ref['href'])
        family = json.loads((source/rel).read_text())
        folder = output/rel.parent
        folder.mkdir()
        index = json.loads((source/rel.parent/family['availability']).read_text())
        for asset in index['assets']:
            relative = rel.parent/asset['href']
            original, target = source/relative, output/relative
            original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            if original_hash != asset['sha256']:
                raise ValueError(f'Source image changed: {relative}')
            target.parent.mkdir(parents=True, exist_ok=True)
            start = time.perf_counter()
            verified = save_indexed_png(original, target)
            elapsed = time.perf_counter()-start
            if hashlib.sha256(original.read_bytes()).hexdigest() != original_hash:
                raise ValueError('Source changed during conversion')
            records.append({'image': str(relative), 'rgbaBytes': original.stat().st_size,
                'indexedBytes': target.stat().st_size, 'encodeAndVerifySeconds': elapsed,
                'rgbaDecodeSeconds': decode_seconds(original),
                'indexedDecodeSeconds': decode_seconds(target), **verified})
            asset.update(sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                         pngColorType='indexed', paletteIndexBits=verified['indexBits'])
        family['pngStorage'] = 'Indexed PNG with per-palette-entry transparency; decoded RGBA unchanged'
        family['publication'] = 'Immutable indexed-encoding pilot; only listed assets exist'
        write(folder/family['availability'], index)
        write(output/rel, family)
    before = sum(r['rgbaBytes'] for r in records)
    after = sum(r['indexedBytes'] for r in records)
    report = {'status': 'passed', 'sourcePublication': str(source), 'images': len(records),
        'rgbaBytes': before, 'indexedBytes': after, 'savedPercent': 100*(1-after/before),
        'encodeAndVerifySeconds': sum(r['encodeAndVerifySeconds'] for r in records),
        'decodeBenchmark': 'Mean of five local Pillow decode-to-RGBA reads; NOT browser/network latency',
        'browserValidated': False, 'records': records}
    write(output/'encoding-benchmark.json', report)
    write(output/'acceptance.json', {'status': 'passed', 'scope': 'Exact decoded RGBA equality for every image; inherited geometry and scientific definitions unchanged',
        'sourceAcceptance': acceptance, 'browserValidated': False, 'benchmark': 'encoding-benchmark.json'})
    catalog['publishedAt'] = datetime.now(UTC).isoformat()
    write(output/'catalog.json', catalog)
    print(json.dumps({k: v for k, v in report.items() if k != 'records'}, indent=2), flush=True)


if __name__ == '__main__':
    main()
