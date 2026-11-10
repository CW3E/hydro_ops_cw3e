import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path


def module():
    path = Path(__file__).resolve().parents[1] / 'bin/test_prism_strategy_campaign.py'
    spec = importlib.util.spec_from_file_location('strategy_campaign', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_campaign_has_no_overlapping_writers():
    cases = module().CASES
    windows = [date.fromisoformat(d) + timedelta(days=i) for d in cases for i in range(3)]
    assert len(cases) == 28
    assert len(windows) == len(set(windows))


def test_missing_reports_cannot_pass(tmp_path):
    assert module().summarize(tmp_path, 'test') == 1
    report = json.loads((tmp_path / 'forcing/work/prism-fallback-tests/test/campaign-summary.json').read_text())
    assert report['passed_blocks'] == 0
    assert report['passed_windows'] == 0
    assert report['status'] == 'needs_review'
