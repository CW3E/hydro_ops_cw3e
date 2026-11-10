import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from io import BytesIO
from zipfile import ZipFile

import numpy as np
import pytest
import xarray as xr
from test_prism import settings

from hydro_ops.download import prism
from hydro_ops.download.source_history import replacement_evidence


def grid(path, values, *, variable='tmin', history='one', units='degC'):
    data = xr.Dataset({variable: (('lat', 'lon'), np.asarray(values, dtype=float)),
                       'crs': np.int32(0)},
                      coords={'lat': [1., 2.], 'lon': [3., 4.]}, attrs={'history': history})
    data[variable].attrs['units'] = units
    data.to_netcdf(path)


def test_metadata_vs_value_changes(tmp_path):
    old, new = tmp_path/'old.nc', tmp_path/'new.nc'
    grid(old, [[1, 2], [np.nan, 4]])
    grid(new, [[1, 2], [np.nan, 4]], history='new release')
    evidence = replacement_evidence(old, new, 'tmin')
    assert evidence['old']['file_sha256'] != evidence['new']['file_sha256']
    assert evidence['comparison']['values_changed'] is False
    grid(new, [[2, np.nan], [3, 4]])
    comparison = replacement_evidence(old, new, 'tmin')['comparison']
    assert comparison['changed_finite_cells'] == 1
    assert comparison['newly_finite_cells'] == 1
    assert comparison['newly_missing_cells'] == 1
    assert comparison['max_abs_change'] == 1
    grid(new, [[2, 2], [3, 4]], units='K')
    assert replacement_evidence(old, new, 'tmin')['comparison']['kind'] == 'grid_or_units_changed'
    assert replacement_evidence(tmp_path/'absent.nc', new, 'tmin')['comparison']['kind'] == 'initial_download'


def test_unreadable_old_grid_does_not_block_valid_replacement(tmp_path):
    import hashlib
    old, new = tmp_path/'old.nc', tmp_path/'new.nc'
    old.write_bytes(b'truncated old download')
    grid(new, [[1, 2], [3, 4]])
    evidence = replacement_evidence(old, new, 'tmin')
    assert evidence['old']['file_sha256'] == hashlib.sha256(old.read_bytes()).hexdigest()
    assert evidence['comparison']['kind'] == 'old_grid_unreadable'
    assert evidence['comparison']['values_changed'] is None
    assert evidence['old']['inspection_error']


def test_missing_old_variable_is_recorded_but_invalid_new_grid_fails(tmp_path):
    old, new = tmp_path/'old.nc', tmp_path/'new.nc'
    grid(old, [[1, 2], [3, 4]], variable='other')
    grid(new, [[1, 2], [3, 4]])
    assert replacement_evidence(old, new, 'tmin')['comparison']['kind'] == 'old_grid_unreadable'
    new.write_bytes(b'not a valid replacement')
    with pytest.raises((OSError, ValueError)):
        replacement_evidence(old, new, 'tmin')


@pytest.mark.parametrize('interrupt', [False, True])
@pytest.mark.parametrize('corrupt_old', [False, True])
def test_download_history_and_interruption(tmp_path, monkeypatch, interrupt, corrupt_old):
    downloader = prism.PrismDownloader(settings(tmp_path))
    release = prism.PrismRelease(date(2026, 5, 3), date(2026, 10, 22), 7, 'https://example/grid', 'tmin')
    destination, metadata = downloader.paths(release.data_date, release.element)
    destination.parent.mkdir(parents=True)
    grid(destination, [[1, 2], [3, 4]])
    if corrupt_old:
        destination.write_bytes(b'truncated old download')
    metadata.write_text(json.dumps({'release_date': '2026-09-01'}))
    raw = tmp_path/'raw.nc'
    grid(raw, [[2, 2], [3, 4]], variable='Band1')
    payload = BytesIO()
    with ZipFile(payload, 'w') as archive:
        archive.write(raw, destination.name)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, **kwargs):
            yield payload.getvalue()

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    @contextmanager
    def session():
        yield Session()

    monkeypatch.setattr(downloader, '_session', session)
    monkeypatch.setattr(prism, 'temporary_work_root', lambda *a: tmp_path)
    real_write = prism.write_json

    def write(path, value):
        if interrupt and path == metadata:
            raise OSError('simulated metadata failure')
        real_write(path, value)

    monkeypatch.setattr(prism, 'write_json', write)
    if interrupt:
        with pytest.raises(OSError, match='simulated'):
            downloader.download_one(release)
    else:
        assert downloader.download_one(release) == 'downloaded'
        assert downloader.download_one(release) == 'skipped'
    history = json.loads(destination.with_suffix('.history.json').read_text())
    assert len(history['events']) == 1
    event = history['events'][0]
    assert event['status'] == ('prepared' if interrupt else 'published')
    assert event['old_release']['release_date'] == '2026-09-01'
    assert event['new_release']['release_date'] == '2026-10-22'
    assert event['old']['file_sha256'] != event['new']['file_sha256']
    if corrupt_old:
        assert event['comparison'] == {'kind': 'old_grid_unreadable', 'values_changed': None}
    if not interrupt:
        newer = replace(release, release_date=date(2026, 10, 23), grid_count=8)
        assert downloader.download_one(newer) == 'downloaded'
        history = json.loads(destination.with_suffix('.history.json').read_text())
        assert len(history['events']) == 2
        assert history['events'][0] == event
        assert history['events'][1]['comparison']['values_changed'] is False
