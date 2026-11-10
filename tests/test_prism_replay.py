import json
from datetime import date

import pytest

from hydro_ops.forcing.nrt_support_production import candidate_path
from hydro_ops.forcing.prism_replay import dependencies, plan
from hydro_ops.forcing.prism_revision_queue import enqueue, source_signature


def test_exact_dependencies_and_missing_baseline(tmp_path):
    d = dependencies(date(2026, 1, 1))
    assert d['baselines'] == ['2025-12-31', '2026-01-01', '2026-01-02']
    assert d['monthly_summaries'] == ['2025-12', '2026-01']
    sources = {}
    for stamp in ('2026-01-01', '2026-01-02'):
        day = date.fromisoformat(stamp)
        for var in ('ppt', 'tmin', 'tmax'):
            p = tmp_path/'forcing/inputs/oregon_state/prism/an/4km/daily'/var/day.strftime(
                f'%Y/%m/prism_{var}_us_25m_%Y%m%d.nc')
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('frozen')
            p.with_suffix('.release.json').write_text(json.dumps({'release_date': '2026-10-22'}))
            sources[f'{var}/{stamp}'] = source_signature(p)
    state = enqueue({}, sources, lambda stream, day: stream == 'retro' and str(day) == '2026-01-01')
    record = plan(state, tmp_path)['items'][0]
    assert record['status'] == 'needs_baseline'
    assert len(record['missing_baselines']) == 3
    for stamp in d['baselines']:
        p = tmp_path/'forcing/outputs/conus/baseline/hourly'/date.fromisoformat(stamp).strftime(
            '%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    assert plan(state, tmp_path)['items'][0]['status'] == 'ready_for_candidate'
    state['pending']['retro/2026-01-01']['sources']['ppt/2026-01-01'] = 'stale'
    assert plan(state, tmp_path)['items'][0]['status'] == 'blocked_sources'


def test_candidate_only_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    monkeypatch.setenv('SLURM_JOB_USER', 'testuser')
    assert str(candidate_path('/scratch/testuser/job_123/windows/nrt/day', tmp_path)).endswith('/day')
    for path in ('/scratch/testuser/job_124/day', '/scratch/other/job_123/day',
                 tmp_path/'forcing/outputs/conus/nrt/hourly/day'):
        with pytest.raises(ValueError):
            candidate_path(path, tmp_path)
    link = tmp_path/'forcing/work/link'
    link.parent.mkdir(parents=True)
    target = tmp_path/'forcing/outputs/conus/nrt'
    target.mkdir(parents=True)
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        candidate_path(link/'day', tmp_path)
