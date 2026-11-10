"""Isolated durable replay stages; no live queue acknowledgement or publication."""
import fcntl
import json
from pathlib import Path

from .rain_repair_writer import fingerprint
from .repair_transaction import atomic_json


def run_stage(workspace, name, signature, action):
    """Resume accepted unchanged artifacts; fail closed on changed receipts.

    Actions must be idempotent and return passed acceptance plus artifact paths.
    Failure after an atomic output write is retried through the action's own
    acceptance/cache checks, not by treating mere file existence as success.
    """
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace/'stages.json'
    with (workspace/'stages.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(path.read_text()) if path.exists() else {'stages': {}}
        old = state['stages'].get(name)
        if old and old['signature'] != signature:
            raise ValueError('Stage source signature changed; start a new revision workspace')
        if old and old['status'] == 'passed':
            for item in old['artifacts']:
                p = Path(item['path']).resolve()
                if not p.is_relative_to(workspace) or list(fingerprint(p)) != item['identity']:
                    raise ValueError('Accepted stage artifact changed')
            return old | {'reused': True}
        state['stages'][name] = {'status': 'running', 'signature': signature}
        atomic_json(path, state)
        try:
            result = action()
            if result.get('status') != 'passed' or not result.get('artifacts'):
                raise ValueError('Stage has no accepted artifacts')
            artifacts = []
            for value in result['artifacts']:
                p = Path(value).resolve()
                if not p.is_relative_to(workspace):
                    raise ValueError('Replay artifact outside isolated workspace')
                artifacts.append({'path': str(p), 'identity': list(fingerprint(p))})
            record = {'status': 'passed', 'signature': signature, 'artifacts': artifacts,
                      'acceptance': result.get('acceptance', {})}
            state['stages'][name] = record
            atomic_json(path, state)
            return record | {'reused': False}
        except BaseException as exc:
            state['stages'][name] = {'status': 'failed', 'signature': signature, 'error': str(exc)}
            atomic_json(path, state)
            raise
