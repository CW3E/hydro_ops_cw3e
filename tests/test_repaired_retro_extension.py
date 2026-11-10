import importlib
import json
from datetime import datetime
from pathlib import Path

import pytest


@pytest.mark.parametrize('domain,year,chain', [
    ('b120', 2001, ('conus', 'cnrfc', 'b120')),
    ('ucrb', 2026, ('conus', 'ucrb')),
    ('cnrfc', 2026, ('conus', 'cnrfc')),
])
def test_provenance_chain(tmp_path, monkeypatch, domain, year, chain):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    extension = importlib.import_module('extend_repaired_retro')
    day = datetime(year, 1, 1)  # noqa: DTZ001
    relative = f'{year}/01/{year}0101.LDASIN_DOMAIN1'
    previous = None
    for child in chain:
        path = tmp_path/f'forcing/outputs/{child}/retro/hourly'/relative
        path.parent.mkdir(parents=True)
        path.touch()
        identity = extension.forcing_identity(path)
        if previous:
            receipt = {'status': 'passed', 'source': str(previous),
                       'source_identity': extension.forcing_identity(previous),
                       'output_identity': identity, 'records': 24,
                       'missing_active_values': 0, 'retained_values_exact_match': True}
            path.with_name(path.name+'.subset.json').write_text(json.dumps(receipt))
        else:
            wave = extension.WAVES[max(y for y in extension.WAVES if y <= year)]
            p = tmp_path/'forcing/work'/wave/f'production/{year}-01-01.publication.json'
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({'status': 'published', 'active_rain_coverage_verified': True,
                                    'outside_envelope_missing': True,
                                    'published_identity': dict(zip(('inode', 'bytes', 'mtime_ns'), identity, strict=True))}))
        previous = path
    assert len(extension.audit_day(tmp_path, domain, day)) == len(chain)
    previous.write_bytes(b'changed')
    with pytest.raises(ValueError, match='Stale'):
        extension.audit_day(tmp_path, domain, day)


def test_ucrb_terminal_midnight_included(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = importlib.import_module('extend_repaired_retro')
    days = []
    def audit(root, domain, day):
        days.append(day)
        return {}
    monkeypatch.setattr(module, 'audit_day', audit)
    end = datetime(2026, 4, 17)  # noqa: DTZ001
    result = module.audit_year(tmp_path, 'ucrb', 2026, end=end)
    assert days[-1] == end and len(days) == 107
    assert result['end'] == '2026-04-17 00:00:00'


def test_partial_end_and_domain_guard(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = importlib.import_module('run_cnrfc_production')
    _, end = module.simulation_window(2020, '2020-12-31')
    assert str(end) == '2020-12-31 00:00:00'
    with pytest.raises(ValueError, match='explicit initialization'):
        module.run(tmp_path, 2020, campaign='repaired_rain_retro_v1', domain='cnrfc',
                   archive_gate=tmp_path/'gate.json', initial_restart_dir=None,
                   continuation_end=end, end_date='2020-12-31')


def test_completed_coupled_replay_lineage(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = importlib.import_module('extend_repaired_retro')
    day = datetime(2026, 3, 12)  # noqa: DTZ001
    path = tmp_path/'forcing/outputs/conus/retro/hourly/2026/03/20260312.LDASIN_DOMAIN1'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'accepted')
    identity = module.forcing_identity(path)
    work = tmp_path/'forcing/work/isolated-revision-replay/123'
    (work/'publication').mkdir(parents=True)
    manifest = {'replay_provenance': {'replay': str(work/'acceptance.json')},
                'verification': 'accepted_coupled_replay_static_envelope',
                'complete_day': True, 'derived_products_pending': False}
    path.with_name(path.name+'.manifest.json').write_text(json.dumps(manifest))
    receipt = {'status': 'passed', 'day': '2026-03-12', 'stream': 'retro',
               'published_artifacts': {str(path): identity}}
    proof = work/'publication/acceptance.json'
    proof.write_text(json.dumps(receipt))
    assert module.accepted_replay(tmp_path, path, day, identity)
    receipt['status'] = 'failed'
    proof.write_text(json.dumps(receipt))
    assert not module.accepted_replay(tmp_path, path, day, identity)
    receipt['status'] = 'passed'
    proof.write_text(json.dumps(receipt))
    assert not module.accepted_replay(tmp_path, path, day, [0, 0, 0])


def test_cnrfc_extension_reaches_startup_gate(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = importlib.import_module('run_cnrfc_production')
    gate = tmp_path/'gate.json'
    gate.write_text(json.dumps({'status': 'failed'}))
    with pytest.raises(ValueError, match='startup gate failed'):
        module.run(tmp_path, 2001, campaign='repaired_rain_retro_v1', domain='cnrfc',
                   archive_gate=gate, initial_restart_dir=tmp_path,
                   continuation_end=datetime(2026, 4, 17))  # noqa: DTZ001


@pytest.mark.parametrize('domain', ['b120', 'yampa'])
@pytest.mark.parametrize('year', range(2021, 2027))
def test_small_domain_continuation_reaches_startup_gate(monkeypatch, tmp_path, domain, year):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = importlib.import_module('run_cnrfc_production')
    gate = tmp_path/'gate.json'
    gate.write_text(json.dumps({'status': 'failed'}))
    with pytest.raises(ValueError, match='startup gate failed'):
        module.run(tmp_path, year, campaign='repaired_rain_retro_v1', domain=domain,
                   archive_gate=gate, initial_restart_dir=tmp_path,
                   end_date='2026-04-17' if year == 2026 else None,
                   continuation_end=datetime.fromisoformat('2026-04-17'))


@pytest.mark.parametrize('end,initial', [('2026-04-18', True), ('2026-04-17', False)])
def test_b120_continuation_still_bounded(monkeypatch, tmp_path, end, initial):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    module = importlib.import_module('run_cnrfc_production')
    with pytest.raises(ValueError, match='explicit initialization'):
        module.run(tmp_path, 2026, campaign='repaired_rain_retro_v1', domain='b120',
                   archive_gate=tmp_path/'gate.json', initial_restart_dir=tmp_path if initial else None,
                   end_date=end, continuation_end=datetime.fromisoformat(end))
