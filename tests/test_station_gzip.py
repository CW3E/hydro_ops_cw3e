import gzip
import json

from hydro_ops.web.globus_transfer import atomic_json
from hydro_ops.web.station_gzip import inventory, package


def test_lossless_package(tmp_path):
    source = tmp_path/'web/publications/observations/source'
    folder = source/'snow_pillow'
    data = folder/'data/ABC/history.csv'
    data.parent.mkdir(parents=True)
    raw = b'time,value\r\n2024-01-01,0\r\n2024-01-02,\r\n'
    data.write_bytes(raw)
    atomic_json(folder/'collection.json', {'geometry': {}, 'processingVersion': 'test',
        'assets': [{'href': 'data/ABC/history.csv', 'fileCompression': 'none',
                    'coverage': {'reportedMonths': ['2024-01'], 'rows': 2, 'samplingPolicy': 'native'}}]})
    atomic_json(folder/'reference-records.json', {'realRecords': {'zero': {'asset': 'data/ABC/history.csv'}}})
    (folder/'README.txt').write_text('source')
    atomic_json(folder/'acceptance.json', {'status': 'passed', 'files': inventory(folder)})
    atomic_json(source/'catalog.json', {'layers': [{'measurementMethod': 'snow_pillow'}]})
    (source/'README.txt').write_text('source')
    atomic_json(source/'acceptance.json', {'status': 'passed', 'files': inventory(source)})
    output = source.with_name('compressed')
    report = package(tmp_path, source, output)
    assert report['csv_files'] == 1
    target = output/'snow_pillow/data/ABC/history.csv.gz'
    assert gzip.decompress(target.read_bytes()) == raw
    assert target.read_bytes() == gzip.compress(raw, compresslevel=6, mtime=0)
    doc = json.loads((output/'snow_pillow/collection.json').read_text())
    assert doc['assets'][0]['fileCompression'] == 'gzip'
    assert doc['assets'][0]['bytes'] == target.stat().st_size
    assert 'reportedMonths' not in doc['assets'][0]['coverage']
    assert doc['assets'][0]['coverage'] == {'rows': 2, 'samplingPolicy': 'native'}
    assert doc['revision'] == 'compressed:snow_pillow'
    assert not target.with_suffix('').exists()
    assert data.read_bytes() == raw
