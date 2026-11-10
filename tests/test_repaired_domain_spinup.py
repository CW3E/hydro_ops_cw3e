"""Safety checks for the isolated output-free corrected-forcing spin-up runner."""
# Model helper clocks use timezone-naive UTC.
# ruff: noqa: DTZ001
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN))
spec = importlib.util.spec_from_file_location('repaired_domain_spinup', BIN / 'run_repaired_domain_spinup.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize('year,hours', [(1981, 672), (1984, 696)])
def test_calendar_month_checkpoint_and_no_history(year, hours):
    land, hydro = runner.settings(datetime(year, 2, 1), datetime(year, 3, 1), Path('/forcing'), None)
    assert land['KHOUR'] == land['RESTART_FREQUENCY_HOURS'] == hours
    assert hydro['rst_dt'] == hours * 60
    for key in ('CHRTOUT_DOMAIN', 'CHRTOUT_HOURLY', 'CHRTOUT_DAILY',
                'LDASOUT_HOURLY', 'LDASOUT_DAILY', 'LSMOUT_DOMAIN', 'RTOUT_DOMAIN'):
        assert hydro[key] == 0
    assert hydro['rst_typ'] == 0
    assert hydro['RSTRT_SWC'] == 1
    assert hydro['GW_RESTART'] == 0


def test_warm_settings():
    land, hydro = runner.settings(datetime(1981, 2, 1), datetime(1981, 3, 1),
                                  Path('/forcing'), [Path('/land'), Path('/hydro')])
    assert (hydro['rst_typ'], hydro['RSTRT_SWC'], hydro['GW_RESTART']) == (1, 0, 1)
    assert hydro['DTRT_TER'] == hydro['DTRT_CH'] == 600
    assert hydro['t0OutputFlag'] == hydro['lake_option'] == 0
    assert land['PCP_PARTITION_OPTION'] == 1
    assert land['RESTART_FILENAME_REQUESTED'] == "'/land'"


def test_snapshot_rejects_stale_receipt(tmp_path):
    source = tmp_path / 'source/1981/01/19810101.LDASIN_DOMAIN1'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'test')
    source.with_name(source.name + '.subset.json').write_text(json.dumps({
        'status': 'passed', 'missing_active_values': 0, 'output_identity': [0, 0, 0]}))
    with pytest.raises(ValueError, match='stale forcing'):
        runner.snapshot_forcing(tmp_path / 'source', tmp_path / 'snapshot',
                                datetime(1981, 1, 1), datetime(1981, 1, 1))


def test_snapshot_pins_replaced_source(tmp_path, monkeypatch):
    source = tmp_path / 'source/1981/01/19810101.LDASIN_DOMAIN1'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'old accepted data')
    source.with_name(source.name + '.subset.json').write_text(json.dumps({
        'status': 'passed', 'missing_active_values': 0, 'output_identity': runner.identity(source)}))
    monkeypatch.setattr(runner, 'check_forcing', lambda *args: None)
    runner.snapshot_forcing(tmp_path / 'source', tmp_path / 'snapshot',
                            datetime(1981, 1, 1), datetime(1981, 1, 1))
    replacement = source.with_suffix('.new')
    replacement.write_bytes(b'new data')
    replacement.replace(source)
    assert (tmp_path / 'snapshot/1981/01/19810101.LDASIN_DOMAIN1').read_bytes() == b'old accepted data'


def test_resume_requires_matching_signature_and_checkpoint(tmp_path, monkeypatch):
    pair = [tmp_path / 'land', tmp_path / 'hydro']
    for path in pair:
        path.write_bytes(b'checkpoint')
    marker = tmp_path / 'accepted.json'
    begin, stop = datetime(1981, 1, 1), datetime(1981, 2, 1)
    record = {'status': 'passed', 'start': str(begin), 'end': str(stop), 'signature': {'v': 1},
              'restarts': list(map(str, pair)), 'restart_sha256': [runner.digest(p) for p in pair]}
    marker.write_text(json.dumps(record))
    monkeypatch.setattr(runner, 'restart_check', lambda *args: None)
    assert runner.accepted(marker, begin, stop, {'v': 1}) == pair
    with pytest.raises(ValueError, match='Incompatible'):
        runner.accepted(marker, begin, stop, {'v': 2})
    pair[0].write_bytes(b'changed')
    with pytest.raises(ValueError, match='Checkpoint changed'):
        runner.accepted(marker, begin, stop, {'v': 1})
