"""Push wrapper sequencing and failure gates, without network or real remotes."""
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('mode', ['normal', 'sync-only', 'push-fails', 'fetch-fails', 'wrong-destination', 'mismatch'])
def test_mirror_wrapper(tmp_path, mode):
    project = tmp_path/'project with spaces'
    (project/'bin').mkdir(parents=True)
    (tmp_path/'hydro_ops_sync').mkdir()
    wrapper = project/'bin/push_repo.sh'
    wrapper.write_bytes((Path(__file__).parents[1]/'bin/push_repo.sh').read_bytes())
    commands = tmp_path/'commands'
    fake = tmp_path/'git'
    fake.write_text('''#!/usr/bin/env python3
import os, sys
args = sys.argv[3:]
with open(os.environ['TEST_LOG'], 'a') as f:
    f.write(' '.join(args)+'\\n')
mode = os.environ['TEST_MODE']
if args[:2] == ['remote', 'get-url']:
    dest = args[-1] == 'destination'
    print('https://github.com/' + ('wrong/repo.git' if dest and mode == 'wrong-destination' else 'CW3E/hydro_ops_cw3e.git' if dest else 'fallspinach/hydro_ops.git'))
elif args[0] == 'rev-parse': print('true')
elif args[0] == 'config': print('+refs/*:refs/*')
elif args == ['push', 'origin', 'main'] and mode == 'push-fails': sys.exit(1)
elif args[0] == 'fetch' and mode == 'fetch-fails': sys.exit(1)
elif args[0] == 'ls-remote':
    print(('different' if mode == 'mismatch' and args[-1] == 'destination' else 'abc') + '\\trefs/heads/main')
''')
    fake.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path)+os.pathsep+os.environ['PATH'],
               TEST_LOG=str(commands), TEST_MODE=mode)
    result = subprocess.run(['bash', str(wrapper)] + (['--sync-only'] if mode == 'sync-only' else []),
                            env=env, capture_output=True, text=True, check=False)
    calls = commands.read_text().splitlines()
    assert (result.returncode == 0) == (mode in ('normal', 'sync-only'))
    mirrored = 'push --mirror --atomic destination'
    if mode in ('push-fails', 'fetch-fails', 'wrong-destination'):
        assert mirrored not in calls
    else:
        assert calls.index('fetch --prune origin') < calls.index(mirrored)
    if mode == 'normal':
        assert calls.index('push origin main') < calls.index('fetch --prune origin')
    if mode == 'sync-only':
        assert 'push origin main' not in calls
