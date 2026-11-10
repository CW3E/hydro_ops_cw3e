import fcntl
import importlib
import json
import os
from contextlib import ExitStack
from datetime import date
from pathlib import Path

import pytest


@pytest.fixture
def publisher(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    module = importlib.import_module('publish_prism_repair_wave')
    monkeypatch.setattr(module, 'digest', lambda path: 'mask-hash')
    return module


def test_nested_subsets_use_updated_parent_and_allow_retry(publisher, tmp_path, monkeypatch):
    relative = '1981/01/19810101.LDASIN_DOMAIN1'
    files = {d: tmp_path / f'forcing/outputs/{d}/retro/hourly' / relative
             for d in ('conus', *publisher.DOMAINS)}
    files['conus'].parent.mkdir(parents=True)
    files['conus'].write_bytes(b'corrected')
    calls = []

    def process(task):
        source, target, _mask, _checksum, _scratch, identity = task
        calls.append((source, target))
        assert identity == list(publisher.fingerprint(source))
        assert source.read_bytes() == b'corrected'
        if target.exists():
            return {'status': 'skipped'}
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())
        return {'status': 'published'}

    monkeypatch.setattr(publisher, 'process', process)
    result = publisher.publish_subsets(tmp_path, relative, tmp_path)
    assert calls == [(files['conus'], files['cnrfc']), (files['cnrfc'], files['b120']),
                     (files['conus'], files['ucrb']), (files['ucrb'], files['yampa'])]
    assert all(r['status'] == 'published' for r in result.values())
    assert all(r['status'] == 'skipped' for r in
               publisher.publish_subsets(tmp_path, relative, tmp_path).values())


def test_failed_parent_stops_children(publisher, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(publisher, 'fingerprint', lambda path: (1, 2, 3))

    def process(task):
        calls.append(task)
        return {'status': 'failed', 'error': 'test parent failure'}

    monkeypatch.setattr(publisher, 'process', process)
    with pytest.raises(RuntimeError, match='cnrfc subset failed'):
        publisher.publish_subsets(tmp_path, '1981/01/19810101.LDASIN_DOMAIN1', tmp_path)
    assert len(calls) == 1


def test_wave2_month_mapping(publisher):
    first = date(1981, 9, 1)
    assert publisher.month_range(first, 52) == (date(1986, 1, 1), date(1986, 1, 31))
    assert publisher.month_range(first, 29) == (date(1984, 2, 1), date(1984, 2, 29))
    assert publisher.month_index(date(1986, 1, 31), first) == 52


def test_parallel_month_locks_exclude_overlap_and_external_writer(publisher, tmp_path):
    for domain in ('conus', *publisher.DOMAINS):
        (tmp_path / f'forcing/outputs/{domain}').mkdir(parents=True)
    with ExitStack() as first, ExitStack() as second:
        publisher.acquire_locks(first, tmp_path, date(1981, 9, 1), date(1981, 9, 30))
        publisher.acquire_locks(second, tmp_path, date(1981, 10, 1), date(1981, 10, 31))
        with ExitStack() as overlap, pytest.raises(BlockingIOError):
            publisher.acquire_locks(overlap, tmp_path, date(1981, 9, 15), date(1981, 10, 1))
        with (tmp_path / 'forcing/outputs/b120/.subset-pilot.lock').open('a') as external, \
                pytest.raises(BlockingIOError):
            fcntl.flock(external, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize('historical_envelope', [True, False])
def test_publish_day_recovers_after_subset_failure(publisher,tmp_path,monkeypatch,historical_envelope):
    from hydro_ops.forcing.rain_repair_writer import digest
    monkeypatch.setattr(publisher,'digest',digest)
    relative='1986/02/19860201.LDASIN_DOMAIN1'
    target=tmp_path/'forcing/outputs/conus/retro/hourly'/relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b'old')
    wave=tmp_path/'forcing/work/testwave'
    backup=wave/'originals'/relative
    backup.parent.mkdir(parents=True)
    os.link(target,backup)
    output=wave/'production'; output.mkdir()
    candidate=tmp_path/'forcing/work/prism-rain-pipeline/123_0/hourly'/relative
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b'new')
    audit=tmp_path/'forcing/work/prism-rain-pipeline/123_0/acceptance.json'
    proof = {'retained_window_rain_exact': True, 'original_windows_modified': False,
             'accepted_window_identities': {'window': [1, 2, 3]}}
    publisher.atomic_json(audit,{'status':'passed','files':[{'path':str(candidate),'sha256':digest(candidate),
                            'envelope_assembly': proof, 'unchanged_chunks_verified': 168}]})
    original_manifest = {'static_envelope': {'policy': 'test'}} if historical_envelope else {}
    publisher.atomic_json(Path(str(target)+'.manifest.json'),original_manifest)
    monkeypatch.setattr(publisher, 'envelope_asset', lambda root: {'policy': 'test', 'mask_sha256': 'mask'})
    def fail(*args):
        raise RuntimeError('subset failure')
    monkeypatch.setattr(publisher,'publish_subsets',fail)
    options=(tmp_path,wave,output,tmp_path,'123',date(1986,2,1),date(1986,2,1))
    with pytest.raises(RuntimeError,match='subset failure'):
        publisher.publish_day(options)
    assert target.read_bytes()==b'new' and backup.read_bytes()==b'old'
    assert (output/'1986-02-01.derived-pending.json').exists()
    before=publisher.fingerprint(target)
    monkeypatch.setattr(publisher,'publish_subsets',lambda *args:{'cnrfc':{'status':'published'}})
    result=publisher.publish_day(options)
    assert result['publication']['status']=='already_published'
    assert publisher.fingerprint(target)==before
    assert json.loads((output/'1986-02-01.original-manifest.json').read_text()) == original_manifest
    metadata = json.loads(Path(str(target)+'.manifest.json').read_text())['static_envelope']
    assert metadata['valid_outside_mask'] == 0
    assert metadata['verified_fields'] == ['RAINRATE']
    if not historical_envelope:
        assert metadata['metadata_origin'] == 'verified_envelope_reassembly'


def test_missing_envelope_without_new_evidence_fails_closed(publisher, tmp_path):
    with pytest.raises(ValueError, match='assembly evidence'):
        publisher.envelope_metadata(tmp_path, {}, {})
