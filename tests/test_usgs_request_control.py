import json
from types import SimpleNamespace

import pytest
import requests

from hydro_ops.download import usgs_request_control as m


def test_key_required(tmp_path, monkeypatch):
    monkeypatch.delenv('USGS_API_KEY', raising=False)
    with pytest.raises(ValueError, match='USGS_API_KEY'):
        m.ControlledRequest(tmp_path, tmp_path/'trace')


def test_shared_gate_and_quota_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv('USGS_API_KEY', 'test-secret')
    clock = [10000.]
    monkeypatch.setattr(m.time, 'time', lambda: clock[0])
    monkeypatch.setattr(m.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(m.time, 'sleep', lambda n: clock.__setitem__(0, clock[0]+n))
    a = m.ControlledRequest(tmp_path, tmp_path/'a')
    b = m.ControlledRequest(tmp_path, tmp_path/'b')
    a.gate(); b.gate()
    assert clock[0] == 10005
    a.gate(response=SimpleNamespace(status_code=429, headers={'Retry-After': '600'}))
    with pytest.raises(m.DeferredRequest, match='cooldown'):
        b.gate()
    assert clock[0] == 10005  # no requests/sleep through a long provider cooldown


def test_lower_reported_quota_reduces_rate(tmp_path, monkeypatch):
    monkeypatch.setenv('USGS_API_KEY', 'test-secret')
    a = m.ControlledRequest(tmp_path, tmp_path/'trace')
    a.gate(response=SimpleNamespace(status_code=200, headers={'X-RateLimit-Limit': '100'}))
    assert json.loads((a.directory/'controlled-budget.json').read_text())['interval'] == 45


def test_retry_logging_without_secret(tmp_path, monkeypatch):
    monkeypatch.setenv('USGS_API_KEY', 'test-secret')
    a = m.ControlledRequest(tmp_path, tmp_path/'trace')
    calls = []
    monkeypatch.setattr(a, 'gate', lambda **kw: calls.append(kw))
    class Session:
        def __init__(self): self.headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, **kw):
            assert kw['allow_redirects'] is False
            raise requests.ReadTimeout('do not log arbitrary exception contents test-secret')
    monkeypatch.setattr(m.requests, 'Session', Session)
    with pytest.raises(m.DeferredRequest, match='Two attempts'):
        a(tmp_path, None, 'https://api.waterdata.usgs.gov/ogcapi/v1/collections/daily/items?limit=1')
    assert len(calls) == 2
    trace = a.trace.read_text()
    assert 'test-secret' not in trace
    assert len(json.loads(trace)['attempts']) == 2


def test_retry_after_http_date_and_unknown():
    assert m.retry_delay('Thu, 01 Jan 1970 00:02:00 GMT', 60) == 60
    assert m.retry_delay(None, 60) == 3600


@pytest.mark.parametrize('status', [429, 401, 403])
def test_provider_pause_does_not_retry_other_stations(tmp_path, monkeypatch, status):
    monkeypatch.setenv('USGS_API_KEY', 'test-secret')
    controller = m.ControlledRequest(tmp_path, tmp_path/'trace')
    calls = []
    class Session:
        def __init__(self): self.headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, **kwargs):
            calls.append(url)
            return SimpleNamespace(status_code=status, headers={'Retry-After': '600'}, close=lambda: None)
    monkeypatch.setattr(m.requests, 'Session', Session)
    with pytest.raises(m.ProviderPause):
        controller(tmp_path, None, 'https://api.waterdata.usgs.gov/ogcapi/v1/collections/daily/items?limit=1')
    assert len(calls) == 1
    assert json.loads(controller.trace.read_text())['attempts'][0]['status_code'] == status
