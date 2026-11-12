"""Keyed station-cycle acquisition; every attempt uses one shared budget."""
import fcntl
import json
import os
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from hydro_ops.forcing.repair_transaction import atomic_json


class DeferredRequest(RuntimeError):
    """Retain old data and retry this station in a later pass."""


class ProviderPause(DeferredRequest):
    """Stop the collection pass rather than querying more stations during a pause."""


def retry_delay(value, now):
    try:
        return max(0., float(value))
    except (ValueError, TypeError):
        try:
            return max(0., parsedate_to_datetime(value).timestamp()-now)
        except (ValueError, TypeError, AttributeError):
            return 3600.  # Unknown quota reset: conservatively stop this pass.


class ControlledRequest:
    """One instance per station; do not share a requests session across threads.

    The 120-second admission budget includes pacing and retries; socket read
    timeouts are inactivity limits, not a hard overall transfer deadline.
    """

    def __init__(self, root, trace, *, budget_seconds=120):
        if not os.environ.get('USGS_API_KEY'):
            raise ValueError('Keyed USGS acquisition requires USGS_API_KEY')
        self.directory = Path(root)/'observations/status/usgs/discharge'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.trace = Path(trace)
        self.deadline = time.monotonic()+budget_seconds
        self.events = []

    def gate(self, *, response=None):
        state_path = self.directory/'controlled-budget.json'
        with (self.directory/'request-budget.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            lock.seek(0)
            previous = float(lock.read() or '0')
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            now = time.time()
            if response is not None:
                headers = response.headers
                try:
                    limit = int(headers.get('X-RateLimit-Limit', ''))
                    if limit > 0:
                        state['interval'] = max(5., 3600/(limit*.8))
                except ValueError:
                    pass
                if response.status_code in (429, 503) and (response.status_code == 429 or headers.get('Retry-After')):
                    state['cooldown_until'] = max(state.get('cooldown_until', 0),
                                                  now+retry_delay(headers.get('Retry-After'), now))
                if headers.get('X-RateLimit-Remaining') == '0':
                    state['cooldown_until'] = max(state.get('cooldown_until', 0), now+3600)
                atomic_json(state_path, state)
                return
            delay = max(0., previous+state.get('interval', 5.)-now,
                        state.get('cooldown_until', 0)-now)
            if time.monotonic()+delay >= self.deadline:
                raise ProviderPause('Shared cooldown/pacing exceeds station admission budget')
            # Sleep under the shared lock so two workers cannot reserve the same slot.
            if delay:
                time.sleep(delay)
            lock.seek(0)
            lock.truncate()
            lock.write(str(time.time()))
            lock.flush()

    def __call__(self, root, unused_session, url):
        if not url.startswith('https://api.waterdata.usgs.gov/ogcapi/v1/collections/daily/items?') or 'api_key=' in url.lower():
            raise ValueError('Unexpected USGS request URL')
        with requests.Session() as session:
            # Default adapters have no automatic retries; the key never enters a URL/report.
            session.headers['X-Api-Key'] = os.environ['USGS_API_KEY']
            for attempt in range(2):
                started = time.monotonic()
                event = {'attempt': attempt+1, 'at': datetime.now(UTC).isoformat(), 'url': url}
                try:
                    self.gate()
                    event['pacing_seconds'] = time.monotonic()-started
                    remaining = self.deadline-time.monotonic()
                    if remaining <= 0:
                        raise DeferredRequest('Station admission budget exhausted')
                    response = session.get(url, timeout=(min(10., remaining), min(30., remaining)),
                                           allow_redirects=False)
                    event.update(status_code=response.status_code,
                                 headers={k: response.headers.get(k) for k in
                                          ('X-RateLimit-Limit', 'X-RateLimit-Remaining', 'Retry-After')})
                    self.gate(response=response)
                    if response.status_code == 429:
                        response.close()
                        raise ProviderPause('Rate limited; shared cooldown installed')
                    if response.status_code in (401, 403):
                        response.close()
                        raise ProviderPause('USGS authentication/authorization rejected; inspect key configuration')
                    if response.status_code in (500, 502, 503, 504):
                        response.close()
                        continue
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise DeferredRequest('Unexpected response status')
                    return response
                except (requests.Timeout, requests.ConnectionError) as error:
                    event['error_type'] = type(error).__name__
                except DeferredRequest as error:
                    event['deferred'] = str(error)
                    raise
                finally:
                    event['elapsed_seconds'] = time.monotonic()-started
                    self.events.append(event)
                    atomic_json(self.trace, {'key_configured': True, 'attempts': self.events})
        raise DeferredRequest('Two attempts exhausted; retry station in a later pass')
