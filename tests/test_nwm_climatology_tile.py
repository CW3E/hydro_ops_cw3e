"""Exercise the complete array worker on a tiny full-calendar history."""
import importlib
import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np


def test_tile_worker_end_to_end(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    worker = importlib.import_module('produce_nwm_climatology_tile')
    benchmark = importlib.import_module('benchmark_nwm_climatology')
    inputs = worker.dates(worker.REFERENCE_START-timedelta(days=15), worker.REFERENCE_END+timedelta(days=15))
    tile = {'window': [0, 2, 0, 3], 'active': np.ones((2, 3), bool),
            'x': [0., 1000., 2000.], 'y': [0., 1000.],
            'lat': np.full((2, 3), 40.), 'lon': np.full((2, 3), -120.),
            'crs': {'grid_mapping_name': 'latitude_longitude'}}
    values = np.zeros((len(inputs), 2, 2, 3), dtype='f4')
    values[:, 0] = np.arange(len(inputs))[:, None, None] % 500
    values[:, :, 0, 0] = np.nan
    source = tmp_path / 'tile-002.nc'
    benchmark.write_history(source, tile, inputs, values)
    state = tmp_path / 'nwm/status/cnrfc/climatology/history-stream-1'
    state.mkdir(parents=True)
    (state / 'status.json').write_text(json.dumps({'status': 'passed', 'artifacts': [
        {'path': str(source), 'sha256': benchmark.digest(source)}]}))
    monkeypatch.setattr(worker, '__file__', str(tmp_path / 'bin/worker.py'))
    real_path = Path
    monkeypatch.setattr(worker, 'Path', lambda p: real_path(str(p).replace('/scratch/test-user/', str(tmp_path / 'scratch')+'/')))
    for key, value in {'SLURM_ARRAY_JOB_ID': '2', 'SLURM_ARRAY_TASK_ID': '0',
                       'SLURM_JOB_ID': '3', 'SLURM_JOB_USER': 'test-user'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, 'argv', ['worker', '--history-job', '1'])
    worker.main()
    report = json.loads((tmp_path / 'nwm/status/cnrfc/climatology/tiles-2/task-000.json').read_text())
    assert report['status'] == 'passed'
    assert report['fields']['SOIL_WATER']['valid_cells'] == 5
    assert report['fields']['SNEQV']['oracle_comparisons'] > 0
    assert Path(report['artifact']['path']).is_file()
    mask = tmp_path / 'nwm/static/domains/cnrfc/masks/cnrfc_masks.nc'
    mask.parent.mkdir(parents=True)
    mask.write_bytes(b'fixture-mask')
    (state / 'plan.json').write_text(json.dumps({'tiles': [{'name': 'tile-002', 'window': tile['window']}],
                                               'active_cells': 5, 'mask_sha256': benchmark.digest(mask)}))
    audit = importlib.import_module('audit_nwm_climatology_tiles')
    monkeypatch.setattr(audit, '__file__', str(tmp_path / 'bin/audit.py'))
    monkeypatch.setattr(sys, 'argv', ['audit', '--history-job', '1', '--array-job', '2'])
    audit.main()
    acceptance = json.loads((tmp_path / 'nwm/status/cnrfc/climatology/tiles-2/acceptance.json').read_text())
    assert acceptance['status'] == 'passed'
    assert acceptance['tile_count'] == 1
