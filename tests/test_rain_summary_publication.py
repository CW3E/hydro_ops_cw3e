import json

import pytest

from hydro_ops.forcing.rain_repair_writer import fingerprint
from hydro_ops.forcing.rain_summary_publication import publish_summary


def setup(tmp_path):
    candidate, target, receipt, source = [tmp_path/p for p in ('new', 'live', 'receipt.json', 'input')]
    candidate.write_bytes(b'new accepted summary')
    target.write_bytes(b'old summary')
    source.write_bytes(b'forcing')
    return candidate, target, receipt, {str(source): list(fingerprint(source))}, {'status': 'passed'}


def test_publish_retry_preserves_identity_and_backup(tmp_path):
    args = setup(tmp_path)
    assert publish_summary(*args)['status'] == 'published'
    before = fingerprint(args[1])
    assert publish_summary(*args)['status'] == 'unchanged'
    assert fingerprint(args[1]) == before
    record = json.loads(args[2].read_text())
    from pathlib import Path
    assert Path(record['backup']).read_bytes() == b'old summary'


def test_post_replace_recovery(tmp_path):
    args = setup(tmp_path)

    def crash():
        raise RuntimeError('injected')

    with pytest.raises(RuntimeError, match='injected'):
        publish_summary(*args, after_replace=crash)
    assert json.loads(args[2].read_text())['status'] == 'prepared'
    before = fingerprint(args[1])
    assert publish_summary(*args)['status'] == 'recovered'
    assert fingerprint(args[1]) == before
    assert json.loads(args[2].read_text())['status'] == 'published'


def test_changed_input_blocks_publication(tmp_path):
    args = setup(tmp_path)
    (tmp_path/'input').write_bytes(b'new forcing')
    with pytest.raises(ValueError, match='source identity changed'):
        publish_summary(*args)
    assert args[1].read_bytes() == b'old summary'


def test_external_change_blocks_retry(tmp_path):
    args = setup(tmp_path)
    publish_summary(*args)
    args[1].write_bytes(b'foreign replacement')
    with pytest.raises(ValueError, match='changed externally'):
        publish_summary(*args)


def test_failed_validation_cannot_publish(tmp_path):
    args = list(setup(tmp_path))
    args[-1] = {'status': 'failed'}
    with pytest.raises(ValueError, match='validation'):
        publish_summary(*args)
