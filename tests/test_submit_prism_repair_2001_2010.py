import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1]/'bin/submit_prism_repair_2001_2010.py'
spec = importlib.util.spec_from_file_location('submit_wave5', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_queued_predecessor_gates_and_no_reservation(tmp_path, monkeypatch):
    gate = tmp_path/'forcing/work/rain-summary-publication-test/job-5118047/acceptance.json'
    gate.parent.mkdir(parents=True)
    gate.write_text(json.dumps({'status': 'passed', 'idempotent_retry_verified': True,
                               'all_variables_reference_equal': True, 'published_files': 160}))
    prior = tmp_path/'forcing/work/prism-repair-wave4-199101-200012/plan.json'
    prior.parent.mkdir(parents=True)
    prior.write_text(json.dumps({'status': 'prepared',
                                'months': [{'start': '2000-12-01'}]*120}))
    monkeypatch.setenv('HYDRO_OPS_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('SBATCH_RESERVATION', 'must-not-be-inherited')
    monkeypatch.setattr('sys.argv', [str(SCRIPT), '--submit'])
    calls = []

    def submit(command, **kwargs):
        assert 'SBATCH_RESERVATION' not in kwargs['env']
        assert not any(s.startswith('--reservation') for s in command)
        calls.append(command)
        return str(1000+len(calls))+'\n'

    monkeypatch.setattr(module.subprocess, 'check_output', submit)
    module.main()
    assert len(calls) == 16
    assert '--dependency=afterok:5118063' in calls[0]
    assert '--dependency=afterok:1001' in calls[1]
    assert '--dependency=afterok:1003:5118065' in calls[3]
    assert '--dependency=afterok:1004' in calls[4]
    assert '--dependency=afterok:1005:5118076' in calls[5]
    assert '--dependency=afterok:1005' in calls[-1]
    saved = json.loads((tmp_path/'forcing/work/repair-submissions/2001-2010.json').read_text())
    assert saved['status'] == 'submitted'
    assert saved['days'] == 3652
    assert saved['deferred_daily'] == ['2010-12-31']
    with pytest.raises(FileExistsError):
        module.main()
    assert len(calls) == 16
