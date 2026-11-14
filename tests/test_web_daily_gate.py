from copy import deepcopy

import pytest

from hydro_ops.web.daily import dependency_command, identity, validate_inputs
from hydro_ops.web.globus_transfer import atomic_json


@pytest.fixture
def setup(tmp_path):
    request = {'start': '2026-11-10', 'end': '2026-11-10', 'jobs': {}}
    records = {}

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, value)
        return path

    for i, domain in enumerate(('cnrfc', 'ucrb')):
        fid, mid = str(100 + i), str(200 + i)
        request['jobs'][domain] = {'forcing': fid, 'model': mid}
        fpath = tmp_path / f'forcing/status/{domain}' / (
            f'sync-{fid}/status.json' if domain == 'cnrfc' else f'nrt-catchup-{fid}/status.json')
        write(fpath, {'status': 'passed', 'job_id': fid, 'domain': domain, 'source_domain': 'conus',
                      'failed': 0, 'total': 1, 'published': 1, 'skipped': 0})
        forcing = write(tmp_path / f'forcing/outputs/{domain}/nrt/daily/2026/11/20261110.LDASIN_DOMAIN1.daily', {})
        source = write(tmp_path / f'parent-{domain}', {})
        subset = write(forcing.with_name(forcing.name + '.subset.json'), {'status': 'passed',
            'output': str(forcing), 'output_identity': identity(forcing),
            'source': str(source), 'source_identity': identity(source)})
        model_file = write(tmp_path / f'nwm/outputs/{domain}/nrt/daily/2026/11/20261110.LDASOUT_DOMAIN1.daily', {})
        model = tmp_path / f'nwm/runs/{domain}/nrt/cycles/job-{mid}/cycle.json'
        journal = write(model.parent / 'publication/transaction.json', {'status': 'complete',
            'files': [{'target': str(model_file), 'after': identity(model_file)}]})
        write(model, {'status': 'complete', 'test': False, 'job_id': mid, 'domain': domain,
                     'rst_typ': 1, 'start': '2026-11-10 00:00:00', 'end': '2026-11-11 00:00:00',
                     'journal': str(journal), 'published_files': 1, 'source_revisions_after_snapshot': []})
        records[domain] = {'forcing': fpath, 'model': model, 'journal': journal,
                           'subset': subset, 'model_file': model_file}
    return tmp_path, request, records


def test_accepts_only_job_specific_current_publications(setup):
    root, request, _ = setup
    pins = validate_inputs(root, request)
    assert len(pins) == 8
    assert all(len(v) == 64 for v in pins.values())
    command = dependency_command(root, root / 'request.json', '123')
    assert '--dependency=afterok:123' in command and '--kill-on-invalid-dep=yes' in command
    assert not any('reservation' in arg for arg in command)


@pytest.mark.parametrize('record,field,value', [
    ('forcing', 'status', 'running'), ('forcing', 'failed', 1), ('forcing', 'job_id', '999'),
    ('model', 'test', True), ('model', 'domain', 'yampa'), ('model', 'rst_typ', 0),
    ('model', 'end', '2026-11-10 00:00:00'), ('model', 'source_revisions_after_snapshot', ['changed']),
    ('journal', 'status', 'publishing'), ('subset', 'output_identity', [1, 2, 3])])
def test_rejects_incomplete_wrong_stale_receipts(setup, record, field, value):
    import json
    root, request, records = setup
    path = records['cnrfc'][record]
    data = json.loads(path.read_text())
    data[field] = value
    atomic_json(path, data)
    with pytest.raises(ValueError):
        validate_inputs(root, request)


def test_rejects_changed_outputs_missing_domain_and_bad_dependency(setup):
    root, request, records = setup
    records['ucrb']['model_file'].write_text('new output')
    with pytest.raises(ValueError, match='accepted job version'):
        validate_inputs(root, request)
    missing = deepcopy(request)
    del missing['jobs']['ucrb']
    with pytest.raises(ValueError):
        validate_inputs(root, missing)
    with pytest.raises(ValueError):
        dependency_command(root, root / 'request.json', '123:456')
