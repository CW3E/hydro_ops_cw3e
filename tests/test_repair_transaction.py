import json
import os
from datetime import date

import pytest

from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import (
    atomic_json,
    check_snapshot,
    create_snapshot,
    publish,
)


def fixture_plan(root):
    work = root / 'forcing/work/test'
    work.mkdir(parents=True)
    original, target, candidate = (work / name for name in ('old', 'target', 'candidate'))
    original.write_bytes(b'old-content')
    os.link(original, target)
    candidate.write_bytes(b'new-content')
    audit = work / 'audit.json'
    atomic_json(audit, {'status': 'passed', 'files': [
        {'path': str(candidate), 'sha256': digest(candidate)}]})
    plan = {'target': str(target), 'candidate': str(candidate),
            'original_snapshot': str(original), 'old_identity': list(fingerprint(target)),
            'old_sha256': digest(original), 'new_sha256': digest(candidate),
            'audit': str(audit), 'audit_sha256': digest(audit), 'receipt': str(work / 'receipt.json')}
    path = work / 'plan.json'
    atomic_json(path, plan)
    return path, plan


def test_resume_after_replace_and_duplicate(tmp_path):
    from pathlib import Path
    path, plan = fixture_plan(tmp_path)
    with pytest.raises(RuntimeError, match='Injected interruption'):
        publish(path, tmp_path, interrupt_after_replace=True)
    before = fingerprint(plan['target'])
    assert json.loads(Path(plan['receipt']).read_text())['status'] == 'intent'
    assert publish(path, tmp_path)['recovered_after_replace']
    assert fingerprint(plan['target']) == before
    assert publish(path, tmp_path)['status'] == 'already_published'
    assert Path(plan['original_snapshot']).read_bytes() == b'old-content'


@pytest.mark.parametrize('which', ['candidate', 'audit', 'target'])
def test_changed_artifacts_refused(tmp_path, which):
    from pathlib import Path
    path, plan = fixture_plan(tmp_path)
    if which == 'audit':
        atomic_json(plan[which], {'status': 'failed'})
    else:
        Path(plan[which]).write_bytes(b'changed')
    with pytest.raises(ValueError):
        publish(path, tmp_path)
    assert not Path(plan['receipt']).exists()


def test_snapshot_survives_atomic_replace_but_detects_inplace_change(tmp_path):
    source = tmp_path / 'forcing/outputs/conus/retro/hourly'
    source_file = source / '1981/01/19810101.LDASIN_DOMAIN1'
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b'original')
    snapshot = tmp_path / 'forcing/work/snapshot'
    create_snapshot(source, snapshot, [date(1981, 1, 1)], tmp_path)
    new = source_file.with_suffix('.new')
    new.write_bytes(b'new')
    os.replace(new, source_file)
    check_snapshot(snapshot / 'snapshot.json', snapshot)
    (snapshot / '1981/01/19810101.LDASIN_DOMAIN1').write_bytes(b'modified')
    with pytest.raises(ValueError, match='Snapshot changed'):
        check_snapshot(snapshot / 'snapshot.json', snapshot)


def test_live_destination_forbidden(tmp_path):
    path, plan = fixture_plan(tmp_path)
    plan['target'] = str(tmp_path / 'forcing/outputs/conus/retro/hourly/live')
    atomic_json(path, plan)
    with pytest.raises(ValueError, match='restricted'):
        publish(path, tmp_path)


def test_snapshot_override_uses_preserved_preceding_day(tmp_path):
    source = tmp_path/'forcing/outputs/conus/retro/hourly'
    relative = '1986/01/19860131.LDASIN_DOMAIN1'
    live = source/relative
    live.parent.mkdir(parents=True)
    live.write_bytes(b'corrected live file')
    preserved = tmp_path/'forcing/work/previous'/relative
    preserved.parent.mkdir(parents=True)
    preserved.write_bytes(b'original boundary input')
    output = tmp_path/'forcing/work/next'
    record = create_snapshot(source,output,[date(1986,1,31)],tmp_path,
                             source_overrides={date(1986,1,31):preserved})
    assert (output/relative).read_bytes() == b'original boundary input'
    assert fingerprint(output/relative) == fingerprint(preserved)
    assert record['files'][0]['source'] == str(preserved)
    check_snapshot(output/'snapshot.json',output)


def test_production_requires_explicit_scope_and_recovers(tmp_path):
    from pathlib import Path
    path, plan = fixture_plan(tmp_path)
    target = tmp_path / 'forcing/outputs/conus/retro/hourly/1981/01/19810101.LDASIN_DOMAIN1'
    target.parent.mkdir(parents=True)
    os.link(plan['target'], target)
    plan['target'] = str(target)
    atomic_json(path, plan)
    with pytest.raises(ValueError, match='authorization'):
        publish(path, tmp_path, production=True)
    plan['authorization'] = 'audited-rain-only-production-v1'
    atomic_json(path, plan)
    with pytest.raises(RuntimeError, match='Injected interruption'):
        publish(path, tmp_path, production=True, interrupt_after_replace=True)
    assert publish(path, tmp_path, production=True)['recovered_after_replace']
    assert publish(path, tmp_path, production=True)['status'] == 'already_published'
    assert Path(plan['original_snapshot']).read_bytes() == b'old-content'
    plan['target'] = str(target).replace('/retro/', '/nrt/')
    atomic_json(path, plan)
    with pytest.raises(ValueError, match='exact calendar destination'):
        publish(path, tmp_path, production=True)
