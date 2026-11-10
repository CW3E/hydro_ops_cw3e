import json
from copy import deepcopy

import pytest

from hydro_ops.download.cdec_swe import FIELDS
from hydro_ops.observations.station_store import StationTable, read_station, write_station
from hydro_ops.observations.station_updates import acquire_window, merge_window


def swe_table():
    row = dict.fromkeys(FIELDS, '')
    row.update(station_id='AAA', sensor_number='3', duration_code='M',
               timestamp_pst='2020-02-15T08:00:00-08:00', swe_mm='0', value_status='numeric')
    return StationTable({
        'provider': 'cdec', 'station_id': 'AAA', 'product': 'snow_water_equivalent',
        'sensor': '3', 'resolution': 'monthly', 'units_si': 'mm',
        'timezone': 'fixed PST', 'label_kind': 'fixed_pst_timestamp',
        'interval_semantics': 'native samples, not monthly averages',
        'mapping': {'series': 'sensor_number', 'date': 'timestamp_pst', 'value': 'swe_mm',
                    'status': 'value_status'}}, FIELDS.copy(), [row])


def test_native_swe_multiple_samples_not_collapsed(tmp_path):
    old = swe_table()
    second = old.rows[0].copy()
    second.update(timestamp_pst='2020-02-29T09:00:00-08:00', swe_mm='25.4')
    incoming = deepcopy(old)
    incoming.rows.append(second)
    result = merge_window(old, incoming, start='2020-02-01', end='2020-02-29')
    path = tmp_path/'swe.nc'
    write_station(path, result, lock_root=tmp_path/'locks')
    assert read_station(path) == result
    assert len(result.rows) == 2
    assert result.normalized()[0]['value_si'] == 0
    incoming.rows = [second]
    with pytest.raises(ValueError, match='omitted'):
        merge_window(old, incoming, start='2020-02-01', end='2020-02-29')
    incoming.metadata['label_kind'] = 'period'
    with pytest.raises(ValueError):
        incoming.validate()


@pytest.mark.parametrize('stamp', ['2020-02-01T00:00:00Z', '2020-02-01T00:00:00',
                                  '2020-02-01T00:00:00-07:00'])
def test_swe_requires_fixed_pst(stamp):
    table = swe_table()
    table.rows[0]['timestamp_pst'] = stamp
    with pytest.raises(ValueError, match='fixed-PST'):
        table.validate()


def test_live_swe_parser_padding_and_raw_receipt(tmp_path, monkeypatch):
    import requests
    payload = [{'stationId': 'AAA', 'SENSOR_NUM': 3, 'durCode': 'M', 'units': 'INCHES',
                'date': '2020-02-15 08:00', 'obsDate': None, 'value': 2, 'dataFlag': ''}]
    class Response:
        content = json.dumps(payload).encode()
        url = 'https://example.test/cdec'
        def raise_for_status(self):
            pass
        def json(self):
            return payload
    def get(self, url, *, params, timeout):
        assert params['SensorNums'] == 3
        assert params['Start'] == '2020-01-31'
        assert params['End'] == '2020-03-02'
        return Response()
    monkeypatch.setattr(requests.Session, 'get', get)
    raw = tmp_path/'observations/work/raw'
    incoming, receipt = acquire_window(tmp_path, raw, swe_table(), start='2020-02-01', end='2020-02-29')
    assert incoming.rows[0]['swe_mm'] == '50.8'
    assert receipt['status'] == 'acquired_not_published'
    assert (raw/'response.json.gz').exists()
    with pytest.raises(ValueError, match='complete calendar months'):
        acquire_window(tmp_path, raw, swe_table(), start='2020-02-02', end='2020-02-29')


def test_swe_archive_requires_current_receipt(tmp_path):
    import gzip

    from hydro_ops.download.cdec_swe import csv_bytes, identity
    from hydro_ops.observations.station_swe import swe_archive_table
    base = tmp_path/'observations/processed/cdec/swe/sensor_3/monthly/AAA'
    base.mkdir(parents=True)
    history = base/'history.csv.gz'
    history.write_bytes(gzip.compress(csv_bytes(swe_table().rows)))
    inventory = tmp_path/'inventory.json'
    inventory.write_text(json.dumps({'status': 'passed', 'stations': [
        {'station_id': 'AAA', 'sensors': [{'sensor_number': 3, 'duration_code': 'M', 'units': 'INCHES'}]}]}))
    receipt = tmp_path/'receipt.json'
    receipt.write_text(json.dumps({'status': 'passed', 'station_id': 'AAA', 'sensor_number': 3,
        'duration_code': 'M', 'output_identities': {str(history.relative_to(tmp_path)): identity(history)}}))
    (base/'cursor.json').write_text(json.dumps({'last_receipt': 'receipt.json'}))
    table = swe_archive_table(tmp_path, 'AAA', 3, 'monthly', inventory=inventory)
    assert table.rows == swe_table().rows
    history.write_bytes(gzip.compress(csv_bytes(swe_table().rows))+b'changed')
    with pytest.raises(ValueError, match='accepted identity'):
        swe_archive_table(tmp_path, 'AAA', 3, 'monthly', inventory=inventory)


@pytest.mark.parametrize('resolution,code', [('hourly', 'H'), ('event', 'E')])
def test_native_chunks_require_each_current_identity(tmp_path, resolution, code):
    import gzip

    from hydro_ops.download.cdec_swe import csv_bytes, identity
    from hydro_ops.observations.station_swe import swe_archive_table
    base = tmp_path/'observations/processed/cdec/swe/sensor_3'/resolution/'AAA'
    base.mkdir(parents=True)
    receipts = tmp_path/'observations/status/cdec/swe/chunks/AAA/3'/code
    receipts.mkdir(parents=True)
    inventory = tmp_path/'inventory.json'
    inventory.write_text(json.dumps({'status': 'passed', 'stations': [
        {'station_id': 'AAA', 'sensors': [{'sensor_number': 3, 'duration_code': code, 'units': 'INCHES'}]}]}))
    expected = []
    for year in (2019, 2020):
        rows = []
        for minute in ('00', '15'):
            row = swe_table().rows[0].copy()
            row.update(duration_code=code, timestamp_pst=f'{year}-02-15T08:{minute}:00-08:00')
            rows.append(row)
        expected.extend(rows)
        path = base/f'{year}.csv.gz'
        path.write_bytes(gzip.compress(csv_bytes(rows)))
        (receipts/f'{year}.json').write_text(json.dumps({'status': 'passed', 'station_id': 'AAA',
            'sensor_number': 3, 'duration_code': code,
            'output_identities': {str(path.relative_to(tmp_path)): identity(path)}}))
    # Deliberately point cursor to the older year's completion.
    (base/'cursor.json').write_text(json.dumps({'last_receipt': str((receipts/'2019.json').relative_to(tmp_path))}))
    table = swe_archive_table(tmp_path, 'AAA', 3, resolution, inventory=inventory)
    assert table.rows == expected
    assert len(table.metadata['accepted_chunk_receipts']) == 2
    (receipts/'2020.json').unlink()
    with pytest.raises(ValueError, match='accepted identity'):
        swe_archive_table(tmp_path, 'AAA', 3, resolution, inventory=inventory)
