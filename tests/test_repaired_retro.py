import importlib
from pathlib import Path

import pytest


def tools(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    return importlib.import_module('run_cnrfc_production'), importlib.import_module('cleanup_old_subdomain_results')


def test_corrected_roots_and_gate(tmp_path, monkeypatch):
    production, _ = tools(monkeypatch)
    for domain in ('cnrfc', 'ucrb', 'b120', 'yampa'):
        assert production.publication_roots(tmp_path, 'repaired_rain_retro_v1', domain=domain) == (
            tmp_path/'nwm/outputs'/domain/'retro', tmp_path/'nwm/restarts'/domain/'retro')
        with pytest.raises(ValueError, match='explicit initialization'):
            production.run(tmp_path, 1979, campaign='repaired_rain_retro_v1', domain=domain)
    with pytest.raises(ValueError):
        production.publication_roots(tmp_path, 'repaired_rain_retro_v1', domain='conus')


def test_cleanup_preserves_spinup_tests_and_conus(tmp_path, monkeypatch):
    _, cleanup = tools(monkeypatch)
    keep = ['nwm/restarts/cnrfc/retro/repaired_rain_spinup_v1',
            'nwm/outputs/cnrfc/tests/repaired-retro-start-5150178',
            'nwm/outputs/cnrfc/retro/tests', 'nwm/outputs/conus/retro/hourly']
    remove = ['nwm/outputs/cnrfc/retro/hourly', 'nwm/restarts/b120/retro/1981',
              'nwm/outputs/ucrb/retro/production_v1', 'nwm/restarts/yampa/nrt/2026']
    for p in keep+remove:
        (tmp_path/p).mkdir(parents=True)
    assert set(cleanup.targets(tmp_path)) == {tmp_path/p for p in remove}
