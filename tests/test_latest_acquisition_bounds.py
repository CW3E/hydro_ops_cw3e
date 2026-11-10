import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from netCDF4 import Dataset
from test_nwm_forcing_coordinator import load_script


def test_explicit_catchup_does_not_weaken_routine_guard():
    from datetime import date
    module = load_script('run_latest_nrt.py')
    first, today = date(2026, 10, 31), date(2026, 11, 8)
    with pytest.raises(ValueError, match='explicit backfill'):
        module.extension_end(first, today)
    assert module.extension_end(first, today, date(2026, 11, 3)) == date(2026, 11, 3)
    with pytest.raises(ValueError, match='seven calendar'):
        module.extension_end(first, today, date(2026, 11, 7))
    with pytest.raises(ValueError, match='completed UTC'):
        module.extension_end(first, today, today)


def setup_runner(tmp_path, monkeypatch, arguments):
    module = load_script('run_latest_nrt.py')
    monkeypatch.setattr(module, 'load_settings', lambda: SimpleNamespace(project_root=tmp_path))
    monkeypatch.setattr(module, 'work_directory', lambda: tmp_path/'scratch')
    monkeypatch.setattr(module, 'check_scratch', lambda p: None)
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    monkeypatch.setattr('sys.argv', ['run_latest_nrt.py', *arguments])
    return module


@pytest.mark.parametrize('args', [
    ['--test-day', '2026-01-01'],
    ['--test-workspace', '/tmp/outside', '--test-day', '2026-01-01', '--test-end-hour', '5'],
    ['--test-workspace', '/tmp/outside', '--test-day', '2026-01-01', '--test-end-hour', '24'],
])
def test_reject_unbounded_or_external_test(tmp_path, monkeypatch, args):
    module = setup_runner(tmp_path, monkeypatch, args)
    with pytest.raises(SystemExit):
        module.main()
    assert not (tmp_path/'forcing/outputs').exists()


def test_live_loop_bounds_and_repeat(tmp_path, monkeypatch):
    workspace = tmp_path/'forcing/work/test'
    day = (datetime.now(UTC)-timedelta(days=1)).date()
    args = ['--test-workspace', str(workspace), '--test-day', str(day), '--test-end-hour', '2']
    module = setup_runner(tmp_path, monkeypatch, args)
    downloads, produced = [], []
    class Downloader:
        def __init__(self, settings):
            pass
        def download_hour(self, t):
            downloads.append(t)
    class Engine:
        def __init__(self, root, work, now, *, output_root, baseline_root):
            self.output = output_root
            assert baseline_root.is_relative_to(workspace)
        def produce_day(self, d, *, end_hour, latest):
            produced.append((d, end_hour, latest))
            p = self.output/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            p.parent.mkdir(parents=True)
            with Dataset(p, 'w') as ds:
                ds.createDimension('time', end_hour+1)
                v = ds.createVariable('time', 'f8', ('time',))
                v.units = f'hours since {d} 00:00:00'
                v[:] = list(range(end_hour+1))
            module._atomic_json(p.with_name(p.name+'.nrt-receipt.json'), {
                'status': 'passed', 'day': str(d), 'published_identity': module.identity(p)})
            return {'day': str(d), 'status': 'published'}
    monkeypatch.setattr(module, 'HrrrDownloader', Downloader)
    monkeypatch.setattr(module, 'RecentNrt', Engine)
    monkeypatch.setattr(module, 'refresh_recent_observations', lambda *args: {})
    module.main()
    report = json.loads((workspace/'status/latest-extension.json').read_text())
    assert report['production_modified'] is False
    assert produced == [(day, 2, True)]
    assert len(downloads) == 8  # five boundary hours plus 00–02 UTC
    module.main()
    assert len(produced) == 1
    assert not (tmp_path/'forcing/status/nrt-gfs/latest-extension.json').exists()
