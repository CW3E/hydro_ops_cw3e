"""Lossless gzip repackaging of an accepted two-layer station publication."""
import gzip
import json
import shutil
from pathlib import Path

from .globus_transfer import atomic_json, digest


def inventory(folder):
    return [{'path': str(p.relative_to(folder)), 'bytes': p.stat().st_size, 'sha256': digest(p)}
            for p in sorted(folder.rglob('*')) if p.is_file() and p != folder/'acceptance.json']


def package(root, source, output):
    root, source, output = Path(root).resolve(), Path(source).resolve(), Path(output).resolve()
    source.relative_to(root/'web/publications/observations')
    output.relative_to(root/'web/publications/observations')
    acceptance = json.loads((source/'acceptance.json').read_text())
    if acceptance['status'] != 'passed':
        raise ValueError('Accepted source required')
    frozen = digest(source/'acceptance.json')
    output.mkdir(parents=True, exist_ok=False)
    original_bytes = compressed_bytes = count = 0
    for item in acceptance['files']:
        p = (source/item['path']).resolve()
        p.relative_to(source)
        if digest(p) != item['sha256'] or p.stat().st_size != item['bytes']:
            raise ValueError('Source acceptance mismatch')
        if p.suffix == '.csv':
            # Only data assets are compressed; evidence CSVs remain inspectable.
            is_asset = '/data/' in '/'+item['path']
        else:
            is_asset = False
        target = output/(item['path'] + ('.gz' if is_asset else ''))
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_asset:
            raw = p.read_bytes()
            packed = gzip.compress(raw, compresslevel=6, mtime=0)
            target.write_bytes(packed)
            if gzip.decompress(target.read_bytes()) != raw:
                raise ValueError('Lossless verification failed')
            count += 1
            original_bytes += len(raw)
            compressed_bytes += len(packed)
        else:
            shutil.copyfile(p, target)
    index = json.loads((output/'catalog.json').read_text())
    index['releaseId'] = output.name
    for layer in index['layers']:
        folder = output/layer['measurementMethod']
        doc = json.loads((folder/'collection.json').read_text())
        doc['releaseId'] = output.name
        doc['revision'] = f'{output.name}:{layer["measurementMethod"]}'
        doc['geometry']['datasetVersion'] = doc['revision']
        doc['processingVersion'] += '-gzip6'
        layer['revision'] = doc['revision']
        for asset in doc['assets']:
            asset.get('coverage', {}).pop('reportedMonths', None)
            if asset['fileCompression'] != 'none' or not asset['href'].endswith('.csv'):
                raise ValueError('Expected plain CSV source')
            asset['href'] += '.gz'
            target = folder/asset['href']
            asset.update(fileCompression='gzip', bytes=target.stat().st_size, sha256=digest(target))
        atomic_json(folder/'collection.json', doc)
        refs = json.loads((folder/'reference-records.json').read_text())
        for case in refs['realRecords'].values():
            case['asset'] += '.gz'
        atomic_json(folder/'reference-records.json', refs)
        readme = folder/'README.txt'
        readme.write_text(readme.read_text().replace(source.name, output.name) +
            '\nCSV data assets are deterministic gzip (level 6, mtime 0). Decompress before CSV parsing.\n'
            'Asset sizes and SHA-256 refer to compressed bytes; JSON/GeoJSON remain uncompressed.\n')
        local = json.loads((folder/'acceptance.json').read_text())
        local['files'] = inventory(folder)
        local['repackaging'] = 'Every decompressed CSV byte equals accepted source CSV'
        atomic_json(folder/'acceptance.json', local)
    atomic_json(output/'catalog.json', index)
    readme = output/'README.txt'
    readme.write_text(readme.read_text().replace(source.name, output.name) +
        '\nBoth layers use .csv.gz assets. Decompress before parsing; metadata stays uncompressed.\n')
    if digest(source/'acceptance.json') != frozen:
        raise ValueError('Source acceptance changed')
    result = {**acceptance, 'layers': index['layers'], 'files': inventory(output),
              'repackaging': {'source': str(source.relative_to(root)), 'source_acceptance_sha256': frozen,
                             'csv_files': count, 'original_csv_bytes': original_bytes,
                             'compressed_csv_bytes': compressed_bytes, 'all_decompressed_bytes_identical': True}}
    atomic_json(output/'acceptance.json', result)
    return result['repackaging']
