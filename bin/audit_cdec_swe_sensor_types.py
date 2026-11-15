"""Historical exploratory evidence audit, not the operational SWE classifier.

Current eligibility comes from observations.swe_policy and the CDEC-supplied
station lists. This retained diagnostic must not override that policy.
"""
import argparse
import csv
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import requests

from hydro_ops.web.globus_transfer import atomic_json

CURRENT = 'https://data.cnra.ca.gov/dataset/797db683-e9f1-4c4b-93f9-ee8f065313e7/resource/49baa289-3cc3-439a-af7a-727486dbf303/download/snow-station-metadata.csv'
HISTORICAL = 'https://cdec.water.ca.gov/misc/SnowSensors.html'
# Human-reviewed direct references to equipment at the station. A comparison
# with "other pillows" (e.g. MUM) does not establish this station's hardware.
DIRECT = {'BCH': '02/14/2023', 'BFL': '01/30/2014', 'FRW': '12/16/2025',
          'GKS': '02/03/2011', 'HUL': '11/28/2016', 'MHP': '04/05/2021'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=False)
    metadata_bytes = args.metadata.read_bytes()
    metadata = json.loads(metadata_bytes)
    catalog_path = root / 'observations/processed/stations/v1/catalog.json'
    catalog_bytes = catalog_path.read_bytes()
    entries = [r for r in json.loads(catalog_bytes)['entries'] if r['provider'] == 'cdec'
               and r['product'] == 'snow_water_equivalent' and r['resolution'] == 'daily']
    stations = {r['station_id']: r for r in metadata['stations']}
    retrieved = datetime.now(UTC).isoformat()
    sources = []
    payloads = {}
    for name, url in [('snow-station-metadata.csv', CURRENT), ('SnowSensors.html', HISTORICAL)]:
        response = requests.get(url, timeout=45)
        response.raise_for_status()
        payloads[name] = response.content
        (args.output / name).write_bytes(response.content)
        sources.append({'url': url, 'file': name, 'retrieved_utc': retrieved,
                        'sha256': hashlib.sha256(response.content).hexdigest()})
    current = {r['STATION_ID'] for r in csv.DictReader(io.StringIO(
        payloads['snow-station-metadata.csv'].decode('utf-8-sig')))}
    historical = set(re.findall(r'station_id=([A-Z0-9]{3})', payloads['SnowSensors.html'].decode()))
    if not current or not historical:
        raise ValueError('Provider metadata structure changed')
    records = []
    for sid in sorted({r['station_id'] for r in entries}):
        station = stations[sid]
        evidence = [n for n in station['historical_notes'] if sid in DIRECT
                    and n['date_raw'] == DIRECT[sid] and 'pillow' in n['text'].lower()]
        record = {'station_id': sid, 'station_name': station['station_name'],
            'sensor_codes': sorted({r['sensor'] for r in entries if r['station_id'] == sid}),
            'current_snow_sensor_list': sid in current,
            'historical_2019_snow_sensor_list': sid in historical,
            'measurement_method': 'snow_pillow' if evidence else 'unconfirmed',
            'method_evidence': evidence, 'station_metadata_url': station['source_url'],
            'caveat': 'Network listing does not prove hardware type or unchanged equipment throughout history.'}
        records.append(record)
    report = {'status': 'passed', 'scope': 'Evidence inventory, not universal instrument certification',
        'retrieved_utc': retrieved, 'sources': sources,
        'catalog_sha256': hashlib.sha256(catalog_bytes).hexdigest(),
        'station_metadata_sha256': hashlib.sha256(metadata_bytes).hexdigest(),
        'station_metadata_path': str(args.metadata), 'stations': records,
        'counts': {'daily_series': len(entries), 'daily_stations': len(records),
            'current_network_matches': sum(r['current_snow_sensor_list'] for r in records),
            'historical_network_matches': sum(r['historical_2019_snow_sensor_list'] for r in records),
            'direct_pillow_evidence': sum(r['measurement_method'] == 'snow_pillow' for r in records)}}
    atomic_json(args.output / 'report.json', report)
    print(json.dumps(report['counts']))


if __name__ == '__main__':
    main()
