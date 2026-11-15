"""Incremental, receipt-gated CDEC web snapshots; never modifies station archives."""
import copy
import csv
import gzip
import io
import json
import os
import re
import shutil
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

from hydro_ops.observations.station_store import read_station
from hydro_ops.observations.swe_policy import load_policy

from . import publication
from .globus_transfer import atomic_json, child_path, digest, lock, transfer_client
from .station_fnf import export_rows as fnf_rows
from .station_fnf import flag_dictionary, gap_references
from .station_gzip import inventory
from .station_pilot import export_rows as swe_rows

VERSION = 'station-incremental-v1'
PRODUCTS = {'cdec-fnf': 'full_natural_flow', 'cdec-swe': 'snow_water_equivalent'}


def read(path):
    return json.loads(Path(path).read_text())


def configuration(root, selection):
    if selection not in PRODUCTS:
        raise ValueError('Only CDEC FNF/SWE publication supported')
    settings = read(root/'config/web_stations.json')
    spec = settings['products'][selection]
    config = read(root/'config/web_globus_observations.json')
    suffix = 'cdec/fnf' if selection == 'cdec-fnf' else 'cdec/swe'
    config.update(destination_root=child_path(config['destination_root'], suffix),
                  public_base_url=urljoin(config['public_base_url'], suffix+'/'),
                  production_enabled=spec['mode'] == 'publish')
    return spec, config


def station_inventory(folder):
    accepted = read(folder/'acceptance.json')
    if accepted['status'] != 'passed':
        raise ValueError('Accepted station release required')
    files = []
    for item in accepted['files']:
        name = publication.relative(item['path'])
        path = (folder/name).resolve()
        path.relative_to(folder.resolve())
        if digest(path) != item['sha256'] or path.stat().st_size != item['bytes']:
            raise ValueError('Station release inventory mismatch')
        files.append(dict(item, role='catalog' if name == 'catalog.json' else
                          'payload' if name.endswith('.csv.gz') else 'metadata'))
    files.append({'path': 'acceptance.json', 'sha256': digest(folder/'acceptance.json'),
                  'bytes': (folder/'acceptance.json').stat().st_size, 'role': 'metadata'})
    result = {'schema': 'hydro_ops_publication_inventory_v1', 'files': files,
              'acceptance_sha256': digest(folder/'acceptance.json')}
    publication.validate_inventory(result)
    return result


def source_name(selection, asset):
    return f'cdec/{asset["featureId"]}/{PRODUCTS[selection]}/{asset["sensor"]}/{asset["resolution"]}.nc'


def pins_from_release(root, folder, selection):
    pins = {}
    for layer in read(folder/'catalog.json')['layers']:
        doc = read(folder/layer['href'])
        sources = {s['path']: s['sha256'] for s in doc['provenance']['stationSources']}
        for asset in doc['assets']:
            name = source_name(selection, asset)
            pins[name] = {'sha256': sources['observations/processed/stations/v1/'+name], 'stale': False}
    return pins


def bootstrap(root, selection):
    """Adopt an already HTTPS-verified manual deployment; no remote writes."""
    spec, config = configuration(root, selection)
    folder = (root/spec['bootstrap_release']).resolve()
    folder.relative_to(root/'web/publications/observations')
    inv = station_inventory(folder)
    verified = read(root/spec['bootstrap_https_report'])
    if verified['status'] != 'passed':
        raise ValueError('Successful bootstrap HTTPS receipt required')
    checks = {r['url']: r for r in verified['files']}
    transfer_plan = read((root/spec['bootstrap_https_report']).parent/'plan.json')
    planned = {r['url']: r for r in transfer_plan['files']}
    for row in inv['files']:
        url = urljoin(config['public_base_url'], row['path'])
        item, original = checks[url], planned[url]
        passed = item.get('status') == 'passed' or (item.get('http_status') == 200 and item.get('sha256_matches') is True)
        if not passed or original['sha256'] != row['sha256'] or original['bytes'] != row['bytes']:
            raise ValueError('Bootstrap receipt does not match destination/release')
    control = root/'web/status/deployments'/publication.destination_key(config)
    control.mkdir(parents=True, exist_ok=True)
    # Called under the station product lock; destination owner protects other writers.
    if (control/'owner.json').exists() or (control/'accepted.json').exists():
        raise ValueError('Destination already owned; cannot bootstrap')
    plan_path = publication.prepare(root, folder, inv, config)
    plan = read(plan_path)
    with lock(control/'writer.lock'):
        if (control/'owner.json').exists() or (control/'accepted.json').exists():
            raise ValueError('Destination changed during bootstrap')
        atomic_json(control/'accepted.json', {'id': plan['id'], 'inventory': inv,
                    'plan': str(plan_path.relative_to(root)), 'bootstrap_https_report': spec['bootstrap_https_report']})
        state = read(plan_path.with_name('state.json'))
        atomic_json(plan_path.with_name('state.json'), dict(state, phase='published'))
    return {'release': str(folder.relative_to(root)), 'pins': pins_from_release(root, folder, selection),
            'deployment': str(plan_path.relative_to(root)), 'version': VERSION}


def copy_release(source, target):
    # Immutable CSVs can share inodes; metadata must have independent copies.
    def copier(src, dst):
        if str(src).endswith('.csv.gz'):
            os.link(src, dst)
        else:
            shutil.copyfile(src, dst)
    shutil.copytree(source, target, copy_function=copier)


def render(root, selection, report_path, accepted, output, *, refresh_metadata=False):
    report = read(report_path)
    if report.get('selection') != selection or report.get('representative') or report.get('limit'):
        raise ValueError('Exact full collection cycle report required')
    if report.get('status') not in ('passed', 'passed_with_warnings', 'partial'):
        raise ValueError('No completed per-station acquisition verdict')
    base = root/'observations/processed/stations/v1'
    entries = {e['path']: e for e in read(base/'catalog.json')['entries']
               if e['provider'] == 'cdec' and e['product'] == PRODUCTS[selection]}
    results = {s['path']: s for s in report.get('stations', [])}
    previous = root/accepted['release']
    station_inventory(previous)
    pins, changed, stale, export_errors = copy.deepcopy(accepted['pins']), [], [], {}
    allowed = None
    if selection == 'cdec-swe':
        allowed, _ = load_policy(root)
        expected = {f'cdec/{s}/snow_water_equivalent/{n}/{r}.nc' for s, n, r in allowed}
    else:
        ids = read(root/'config/cdec_fnf_stations.json')['station_ids']
        expected = {f'cdec/{s}/full_natural_flow/{n}/{r}.nc' for s in ids
                    for n, r in (('8', 'daily'), ('65', 'monthly')) if (s, r) != ('TNL', 'daily')}
    if expected != set(pins):
        raise ValueError('Publication membership changed; explicit onboarding/retirement review required')
    for name, old in pins.items():
        verdict, entry = results.get(name, {}), entries.get(name)
        safe = (entry is not None and verdict.get('status') in ('published', 'unchanged')
                and verdict.get('sha256') == entry['sha256'] and not verdict.get('omitted_keys_retained'))
        old['stale'] = not safe
        if not safe:
            stale.append(name)
        elif old['sha256'] != entry['sha256'] or accepted.get('version') != VERSION:
            changed.append(name)
    if not changed and pins == accepted['pins'] and not refresh_metadata:
        return {'status': 'unchanged', 'stale': stale, 'changed': [], 'pins': pins}
    copy_release(previous, output)
    for p in output.rglob('README.txt'):
        text = p.read_text().replace(previous.name, output.name)
        text = text.replace('Routine publication is not enabled.', 'Updated incrementally after station acquisition.')
        p.write_text(text)
    index = read(output/'catalog.json')
    index.update(releaseId=output.name, schema='hydro-ops-observation-index-v1')
    index.pop('pilot', None)
    for layer in index['layers']:
        folder = output/Path(layer['href']).parent
        doc = read(folder/'collection.json')
        features = read(folder/'features.geojson')
        by_id = {f['id']: f for f in features['features']}
        sources = {s['path']: s for s in doc['provenance']['stationSources']}
        references = read(folder/'reference-records.json')
        for asset in doc['assets']:
            name = source_name(selection, asset)
            asset['updateStatus'] = 'stale-retained' if name in stale else 'current'
            if name not in changed:
                continue
            saved_asset = copy.deepcopy(asset)
            saved_feature = copy.deepcopy(by_id[asset['featureId']])
            saved_sources, saved_references = copy.deepcopy(sources), copy.deepcopy(references)
            try:
                entry = entries[name]
                source = (base/name).resolve()
                source.relative_to(base)
                if digest(source) != entry['sha256']:
                    raise ValueError('Source revised before export')
                table = read_station(source)
                rows = (fnf_rows(table, asset['featureId'], asset['resolution']) if selection == 'cdec-fnf'
                        else swe_rows(table, asset['featureId'], asset['sensor'], None,
                                      'D' if asset['resolution'] == 'daily' else 'M'))
                if not rows or len(rows) != entry['rows']:
                    raise ValueError('Record completeness mismatch')
                fields = list(rows[0])
                buffer = io.StringIO(newline='')
                writer = csv.DictWriter(buffer, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
                raw = buffer.getvalue().encode()
                payload = gzip.compress(raw, compresslevel=6, mtime=0)
                if list(csv.DictReader(io.StringIO(gzip.decompress(payload).decode(), newline=''))) != rows:
                    raise ValueError('Lossless export verification failed')
                path = folder/publication.relative(asset['href'])
                # Replace, never truncate a reused hardlink.
                temporary = path.with_name(path.name+'.new')
                temporary.write_bytes(payload)
                temporary.replace(path)
                asset.update(sha256=digest(path), bytes=path.stat().st_size)
                asset['coverage'].update(rows=len(rows), firstTimestamp=rows[0]['timestamp_pst'],
                    lastTimestamp=rows[-1]['timestamp_pst'],
                    displayStatusCounts=dict(Counter(r['display_status'] for r in rows)),
                    providerFlagCounts=dict(Counter(r['data_flag'] for r in rows)))
                # Compact gap counts; reference cases remain explicit for FNF.
                gap_rows = [dict(r, period_label=r['timestamp_pst'][:10 if asset['resolution'] == 'daily' else 7]) for r in rows]
                gaps = gap_references(gap_rows, asset['resolution'], asset['href'])
                asset['coverage'].pop('absentProviderDates', None)
                asset['coverage']['absentPeriodPolicy'] = 'Preserve absent reporting periods; never fill values'
                asset['coverage']['gapCount'] = len(gaps)
                if 'partition' in asset:
                    asset['partition'] = {'kind': 'full-station-history', 'meaning': 'All available provider reporting labels'}
                meta = table.metadata['auxiliary']['station.json']
                lon, lat = meta['longitude'], meta['latitude']
                if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                    raise ValueError('Invalid station coordinates')
                feature = by_id[asset['featureId']]
                feature['geometry']['coordinates'] = [lon, lat]
                feature['properties'].update(name=meta['station_name'], basin=meta['river_basin'],
                    elevation_raw=meta['elevation_raw'], metadataUrl=meta['source_url'], historical_notes=meta['historical_notes'])
                if 'elevation' in feature['properties']:
                    elevation = re.fullmatch(r'\s*([+-]?[\d.]+)\s*ft\s*', meta['elevation_raw'])
                    feature['properties']['elevation'] = float(elevation[1]) if elevation else None
                sources[str(source.relative_to(root))] = {'path': str(source.relative_to(root)),
                    'sha256': entry['sha256'], 'downloadable': False,
                    'bootstrapReceipt': table.metadata.get('archive_receipt', {}),
                    'bootstrapReceiptScope': 'Historical initial ingestion, not current CSV coverage',
                    'updateCycle': str(report_path.relative_to(root)), 'updateCycleSha256': digest(report_path),
                    'publishedSnapshot': {'rows': len(rows), 'firstTimestamp': rows[0]['timestamp_pst'],
                                          'lastTimestamp': rows[-1]['timestamp_pst']}}
                task_index = results[name].get('task_index')
                if task_index is not None:
                    receipt_path = report_path.parent/str(int(task_index))/'publication.json'
                    if receipt_path.exists():
                        receipt = read(receipt_path)
                        matched = receipt.get('result', {}).get('entry', {})
                        if receipt.get('status') == 'passed' and matched.get('sha256') == entry['sha256'] and matched.get('path') == name:
                            sources[str(source.relative_to(root))]['subsequentUpdate'] = {
                                'receiptPath': str(receipt_path.relative_to(root)), 'receiptSha256': digest(receipt_path),
                                'receipt': receipt,
                                'scope': 'Matched refresh receipt, not complete per-record correction history'}
                refs = references.setdefault('realRecords', {})
                refs = {k: v for k, v in refs.items() if v['asset'] != asset['href']}
                for row in rows:
                    value = row['fnf_display_si' if selection == 'cdec-fnf' else 'swe_display_mm']
                    tag = f'{asset["featureId"]}:{row["display_status"]}'
                    refs.setdefault(tag, {'asset': asset['href'], 'record': row,
                        'expectedPlot': {'plotCoordinate': row['timestamp_pst'],
                                         'valueSI': float(value) if value else None, 'excluded': not bool(value)}})
                references['realRecords'] = refs
                if selection == 'cdec-fnf':
                    references['absentPeriodCases'] = [g for g in references.get('absentPeriodCases', [])
                        if g['asset'] != asset['href']] + gaps
                if digest(source) != entry['sha256']:
                    raise ValueError('Source revised during export')
                pins[name] = {'sha256': entry['sha256'], 'stale': False}
            except Exception as error:  # noqa: BLE001 -- retain accepted station on isolated export failure
                asset.clear()
                asset.update(saved_asset, updateStatus='stale-retained')
                by_id[asset['featureId']].clear()
                by_id[asset['featureId']].update(saved_feature)
                sources, references = saved_sources, saved_references
                path = folder/publication.relative(asset['href'])
                path.with_name(path.name+'.new').unlink(missing_ok=True)
                temporary = path.with_name(path.name+'.restore')
                shutil.copyfile(previous/path.relative_to(output), temporary)
                temporary.replace(path)
                pins[name] = dict(accepted['pins'][name], stale=True)
                stale.append(name)
                export_errors[name] = str(error)
        doc['provenance']['stationSources'] = list(sources.values())
        doc.update(releaseId=output.name, revision=f'{output.name}:{folder.name}',
                   createdAt=datetime.now(UTC).isoformat(), processingVersion=VERSION)
        doc.pop('pilot', None)
        doc['geometry']['datasetVersion'] = doc['revision']
        doc['publication'].update(updatePolicy='Incremental after daily station acquisition; failed stations retain accepted data',
                                  staleSeries=[a['seriesId'] for a in doc['assets'] if a['updateStatus'] == 'stale-retained'])
        if selection == 'cdec-fnf':
            doc['quality']['providerFlagDictionary'] = flag_dictionary(doc['assets'])
        atomic_json(folder/'collection.json', doc)
        atomic_json(folder/'features.geojson', features)
        atomic_json(folder/'reference-records.json', references)
        layer.update(revision=doc['revision'], records=sum(a['coverage']['rows'] for a in doc['assets']))
        atomic_json(folder/'acceptance.json', {'status': 'passed', 'files': inventory(folder),
                    'stations': len(doc['assets']), 'assets': len(doc['assets']), 'records': layer['records'],
                    'scope': 'Changed assets revalidated; unchanged assets reused from accepted publication'})
    atomic_json(output/'catalog.json', index)
    changed = [name for name in changed if name not in export_errors]
    current = {e['path']: e for e in read(base/'catalog.json')['entries']}
    if any(current.get(name, {}).get('sha256') != pins[name]['sha256'] or digest(base/name) != pins[name]['sha256']
           for name in changed):
        raise ValueError('Sources revised before snapshot acceptance')
    atomic_json(output/'acceptance.json', {'status': 'passed', 'layers': index['layers'],
        'publication_catalog_sha256': digest(output/'catalog.json'), 'files': inventory(output),
        'scope': 'Incremental export fidelity; stale series retained explicitly; remote acceptance separate'})
    return {'status': 'prepared', 'changed': changed, 'stale': stale, 'export_errors': export_errors, 'pins': pins,
            'release': str(output.relative_to(root)), 'version': VERSION}


def run(root, selection, report_path, *, publish=False, timeout=1800, retry_failed=False, refresh_metadata=False):
    root, report_path = Path(root).resolve(), Path(report_path).resolve()
    report_path.relative_to(root/'observations/work/station-cycles')
    spec, config = configuration(root, selection)
    if publish and (not spec['enabled'] or spec['mode'] != 'publish'):
        raise ValueError('Live station publication disabled')
    control = root/'web/status/stations'/selection
    control.mkdir(parents=True, exist_ok=True)
    with lock(control/'cycle.lock'):
        accepted_path, pending_path = control/'accepted.json', control/'pending.json'
        if not accepted_path.exists():
            atomic_json(accepted_path, bootstrap(root, selection))
        accepted = read(accepted_path)
        if pending_path.exists():
            pending = read(pending_path)
            if pending['config'] != config:
                raise ValueError('Pending publication configuration changed')
        else:
            stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
            folder = root/'web/publications/observations'/f'{selection}-{stamp}'
            pending = render(root, selection, report_path, accepted, folder, refresh_metadata=refresh_metadata)
            pending.update(report=str(report_path.relative_to(root)), config=config)
            if pending['status'] == 'unchanged':
                atomic_json(control/'latest.json', pending)
                return pending
            plan = publication.prepare(root, folder, station_inventory(folder), config, incremental=True)
            pending['deployment'] = str(plan.relative_to(root))
            atomic_json(pending_path, pending)
        if not publish:
            atomic_json(control/'latest.json', dict(pending, status='prepared_not_published'))
            return dict(pending, status='prepared_not_published')
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            current_spec, current_config = configuration(root, selection)
            if not current_spec['enabled'] or current_config != config:
                raise ValueError('Publication disabled or configuration changed')
            with transfer_client(config) as client:
                state = publication.advance(root, root/pending['deployment'], client, enabled=True,
                                            retry_failed=retry_failed)
            retry_failed = False
            if state['phase'] == 'published':
                atomic_json(accepted_path, pending)
                pending_path.unlink()
                result = dict(pending, status='published')
                atomic_json(control/'latest.json', result)
                return result
            if state['phase'] == 'blocked':
                raise ValueError('Failed transfer retained; retry with --retry-failed')
            time.sleep(5)
        raise TimeoutError('Publication pending; rerun to resume without acquisition or rebuilding')


def commission(root, selection, report_path, station, run_id):
    """No-write commissioning: force one local re-export without altering archive or live state."""
    root, report_path = Path(root).resolve(), Path(report_path).resolve()
    report_path.relative_to(root/'observations/work/station-cycles')
    if not re.fullmatch('[A-Z0-9]{3}', station) or not str(run_id).isdecimal():
        raise ValueError('Station ID and numeric job ID required')
    accepted = read(root/'web/status/stations'/selection/'accepted.json')
    original = copy.deepcopy(accepted)
    candidates = [n for n in accepted['pins'] if n.split('/')[1] == station and n.endswith('/daily.nc')]
    if len(candidates) != 1:
        raise ValueError('One accepted daily station required')
    name = candidates[0]
    accepted['pins'][name]['sha256'] = 'commissioning-force-export'
    output = root/'web/publications/observations'/f'{selection}-commission-{run_id}'
    started = time.monotonic()
    result = render(root, selection, report_path, accepted, output)
    elapsed = time.monotonic()-started
    if result.get('changed') != [name] or result.get('stale') or result.get('export_errors'):
        raise ValueError('Unexpected commissioning selection/result')
    inv = station_inventory(output)
    old_inv = station_inventory(root/original['release'])
    old_assets = {r['path']: r['sha256'] for r in old_inv['files'] if r['role'] == 'payload'}
    new_assets = {r['path']: r['sha256'] for r in inv['files'] if r['role'] == 'payload'}
    if new_assets != old_assets:
        raise ValueError('Forced export changed data; inspect before live adoption')
    work = root/'web/work/observations/station-commission'/str(run_id)
    work.mkdir(parents=True, exist_ok=True)
    report = {'status': 'passed', 'selection': selection, 'forced_station': name,
              'elapsed_seconds': elapsed, 'all_csv_assets_identical': len(new_assets),
              'release': str(output.relative_to(root)), 'remoteWrites': False,
              'sourceArchiveWrites': False, 'liveAcceptedStateChanged': False}
    atomic_json(work/'acceptance.json', report)
    return report
