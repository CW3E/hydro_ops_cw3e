import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from test_nwm_forcing_coordinator import load_script


@pytest.fixture
def publisher(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    return load_script('publish_bounded_prism_replay.py')


def fixture(p, root, stream='nrt', day=date(2026, 4, 12)):
    work = root/'forcing/work/isolated-revision-replay/123'
    work.mkdir(parents=True)
    stages = {}
    def artifact(stage, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'accepted')
        stages.setdefault(stage, {'status': 'passed', 'artifacts': []})['artifacts'].append(
            {'path': str(path), 'identity': list(p.fingerprint(path))})
    for offset in (-1, 0, 1):
        d = day+timedelta(days=offset)
        artifact(f'baseline/{d}', work/'baseline'/str(d))
    for offset in (0, 1):
        d = day+timedelta(days=offset)
        artifact(f'window/{d}', work/'window'/str(d))
    relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    months = sorted({d.replace(day=1) for d in (day-timedelta(days=1), day)})
    for domain in ('conus', *p.DOMAINS):
        output = work/'outputs'/domain/stream
        paths = [('hourly', output/'hourly'/relative),
                 *( (f'daily/{d}', p.summary_path(output/'daily', d))
                    for d in (day-timedelta(days=1), day)),
                 *((('monthly' if len(months) == 1 else f'monthly/{m:%Y-%m}'),
                    p.summary_path(output/'monthly', m, monthly=True)) for m in months)]
        for name, path in paths:
            artifact(name if domain == 'conus' else f'subsets/{domain}', path)
            target = root/'forcing/outputs'/domain/stream/path.relative_to(output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'old')
    prism = {}
    for i in (0, 1):
        d = day+timedelta(days=i)
        for var in ('ppt', 'tmin', 'tmax'):
            source = work/f'{var}-{d}.nc'
            source.write_bytes(b'source')
            p.atomic_json(source.with_suffix('.release.json'), {'release': 'stable'})
            prism[f'{var}/{d}'] = p.source_signature(source)
    p.atomic_json(work/'context.json', {'pins': {}, 'prism': prism})
    p.atomic_json(work/'stages.json', {'stages': stages})
    p.atomic_json(work/'acceptance.json', {'status': 'passed', 'stream': stream})
    p.atomic_json(work/'noop.json', {'status': 'passed', 'noop': True})
    return work, day


@pytest.mark.parametrize('stream', ['nrt', 'retro'])
def test_exact_twenty_product_plan(publisher, tmp_path, stream):
    work, day = fixture(publisher, tmp_path, stream)
    plan = publisher.bounded_plan(tmp_path, work, stream, day)
    assert len(plan['targets']) == 20
    assert len(plan['source_signatures']) == 6
    assert plan['daily'] == ['2026-04-11', '2026-04-12']


def test_open_month_publication_scope_requires_explicit_acceptance(publisher, tmp_path):
    from hydro_ops.forcing.replay_month_scope import month_scope
    today = datetime.now(UTC).date()
    if today.day < 3:
        pytest.skip('Needs two completed dates in the current month')
    day = today.replace(day=2)
    work, day = fixture(publisher, tmp_path, day=day)
    # Remove only the synthetic monthly artifacts; no real data involved.
    for path in tmp_path.rglob('*.monthly'):
        path.unlink()
    scope = month_scope(tmp_path, 'nrt', day, defer_open_month=True)
    context = publisher.load(work/'context.json')
    context['month_scope'] = scope
    publisher.atomic_json(work/'context.json', context)
    stages = publisher.load(work/'stages.json')
    del stages['stages']['monthly']
    for stage in stages['stages'].values():
        stage['artifacts'] = [a for a in stage['artifacts'] if not a['path'].endswith('.monthly')]
    publisher.atomic_json(work/'stages.json', stages)
    with pytest.raises(ValueError, match='Monthly obligations'):
        publisher.bounded_plan(tmp_path, work, 'nrt', day)
    for name in ('acceptance.json', 'noop.json'):
        report = publisher.load(work/name)
        report['month_scope'] = scope
        publisher.atomic_json(work/name, report)
    plan = publisher.bounded_plan(tmp_path, work, 'nrt', day)
    assert plan['months'] == []
    assert plan['month_scope']['deferred_months'] == [str(day.replace(day=1))]
    assert len(plan['targets']) == 15


def test_month_boundary_requires_both_months(publisher, tmp_path):
    work, day = fixture(publisher, tmp_path, day=date(2026, 4, 1))
    plan = publisher.bounded_plan(tmp_path, work, 'nrt', day)
    assert plan['months'] == ['2026-03-01', '2026-04-01']
    assert len(plan['targets']) == 25
    stages = publisher.load(work/'stages.json')
    del stages['stages']['monthly/2026-03']
    publisher.atomic_json(work/'stages.json', stages)
    with pytest.raises(ValueError, match='affected months'):
        publisher.bounded_plan(tmp_path, work, 'nrt', day)


@pytest.mark.parametrize('failure', ['date', 'stream', 'repeat', 'candidate', 'source', 'target'])
def test_fail_closed(publisher, tmp_path, failure):
    work, day = fixture(publisher, tmp_path)
    stream = 'nrt'
    if failure == 'date':
        day += timedelta(days=1)
    elif failure == 'stream':
        stream = 'retro'
    elif failure == 'repeat':
        publisher.atomic_json(work/'noop.json', {'status': 'failed', 'noop': False})
    elif failure == 'candidate':
        Path(publisher.load(work/'stages.json')['stages']['hourly']['artifacts'][0]['path']).write_bytes(b'changed')
    elif failure == 'source':
        (work/'ppt-2026-04-12.nc').write_bytes(b'changed')
    elif failure == 'target':
        target = tmp_path/'forcing/outputs/conus/nrt/hourly/2026/04/20260412.LDASIN_DOMAIN1'
        target.unlink()
        target.symlink_to(work/'ppt-2026-04-12.nc')
    with pytest.raises(ValueError):
        publisher.bounded_plan(tmp_path, work, stream, day)


def test_dry_run_never_writes_production(publisher, tmp_path, monkeypatch, capsys):
    work, day = fixture(publisher, tmp_path)
    before = {str(p): p.stat().st_mtime_ns for p in (tmp_path/'forcing/outputs').rglob('*') if p.is_file()}
    monkeypatch.setenv('HYDRO_OPS_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr('sys.argv', ['publisher', '--workspace', str(work), '--day', str(day), '--stream', 'nrt'])
    publisher.main()
    assert json.loads(capsys.readouterr().out)['production_modified'] is False
    assert before == {str(p): p.stat().st_mtime_ns for p in (tmp_path/'forcing/outputs').rglob('*') if p.is_file()}
    assert not (work/'publication').exists()


@pytest.mark.parametrize('day', [date(2026, 4, 12), date(2026, 4, 1)])
def test_publication_retry_acknowledgement_and_noop(publisher, tmp_path, monkeypatch, day):
    work, day = fixture(publisher, tmp_path, day=day)
    monkeypatch.setenv('HYDRO_OPS_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('SLURM_JOB_ID', '456')
    monkeypatch.setattr(publisher, 'scratch_directory', lambda: tmp_path)
    monkeypatch.setattr('sys.argv', ['publisher', '--workspace', str(work), '--day', str(day),
                                    '--stream', 'nrt', '--publish'])
    monkeypatch.setattr(publisher, 'load_forcing_reducers', lambda p: ({}, {}, {}))
    calls = []
    fail = [True]
    def summarize(root, output, *a, **kw):
        calls.append('summary')
        if output.read_bytes() != b'summary':
            temporary = output.with_name(output.name+'.tmp')
            temporary.write_bytes(b'summary')
            temporary.replace(output)
    def subset(task):
        calls.append('subset')
        if fail[0]:
            return {'status': 'failed', 'error': 'injected downstream interruption'}
        source, target = task[:2]
        if target.read_bytes() != source.read_bytes():
            temporary = target.with_name(target.name+'.tmp')
            temporary.write_bytes(source.read_bytes())
            temporary.replace(target)
        return {'status': 'published'}
    monkeypatch.setattr(publisher, 'summarize', summarize)
    monkeypatch.setattr(publisher, 'process', subset)
    for domain in publisher.DOMAINS:
        mask = tmp_path/f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
        mask.parent.mkdir(parents=True)
        mask.write_bytes(b'mask')
    sources = publisher.load(work/'context.json')['prism']
    key = f'nrt/{day}'
    queue = tmp_path/'forcing/status/prism-revisions/queue.json'
    publisher.atomic_json(queue, {'pending': {key: {'sources': {k: v['signature'] for k, v in sources.items()}}}})
    with pytest.raises(ValueError, match='Subset failed'):
        publisher.main()
    assert key in publisher.load(queue)['pending']
    assert publisher.load(work/'publication/acceptance.json')['status'] == 'failed'
    originals = work/'publication/originals'
    assert len(list(originals.rglob('*.LDASIN_DOMAIN1*'))) == (25 if day.day == 1 else 20)
    assert all(p.read_bytes() == b'old' for p in originals.rglob('*') if p.is_file())
    fail[0] = False
    publisher.main()
    assert not publisher.load(queue)['pending']
    assert publisher.load(work/'publication/acceptance.json')['status'] == 'passed'
    before = len(calls)
    publisher.main()
    assert len(calls) == before


def test_live_publication_requires_slurm(publisher, tmp_path, monkeypatch):
    work, day = fixture(publisher, tmp_path)
    monkeypatch.setenv('HYDRO_OPS_PROJECT_ROOT', str(tmp_path))
    monkeypatch.delenv('SLURM_JOB_ID', raising=False)
    monkeypatch.setattr('sys.argv', ['publisher', '--workspace', str(work), '--day', str(day),
                                    '--stream', 'nrt', '--publish'])
    with pytest.raises(ValueError, match='SLURM'):
        publisher.main()
