import json

import pytest
from test_nwm_forcing_coordinator import load_script


def test_identity_requires_time_and_size(tmp_path):
    module = load_script('reconcile_prism_revision_records.py')
    path = tmp_path/'file'
    path.write_bytes(b'abc')
    stat = path.stat()
    assert not module.identity_matches({}, path)
    assert not module.identity_matches({'bytes': 3}, path)
    assert module.identity_matches({'bytes': 3, 'mtime_ns': stat.st_mtime_ns}, path)
    assert not module.identity_matches({'bytes': 3, 'mtime_ns': stat.st_mtime_ns,
                                        'inode': stat.st_ino+1}, path)


def test_temperature_dates_are_not_source_acceptance():
    module = load_script('reconcile_prism_revision_records.py')
    assert module.chronology(None, {}) == 'unknown'
    assert module.chronology('2026-09-20T12:00:00Z', {'release_date': '2026-09-22'}) == 'release_postdates_build'
    assert module.chronology('2026-10-27T12:00:00Z', {'release_date': '2026-09-22'}) == 'release_not_later_than_build_not_proof_of_use'


@pytest.mark.parametrize('variable,pinned,expected', [
    ('ppt', True, 'accepted_rain_source_identity_matches'),
    ('ppt', False, 'accepted_rain_repair_source_version_unrecorded'),
    ('tmin', True, 'unresolved_no_exact_source_evidence'),
])
def test_rain_evidence_does_not_clear_temperature(tmp_path, monkeypatch, variable, pinned, expected):
    module = load_script('reconcile_prism_revision_records.py')
    target = tmp_path/'forcing/outputs/conus/retro/hourly/2026/03/20260304.LDASIN_DOMAIN1'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'data')
    stat = target.stat()
    audit = tmp_path/'audit.json'
    audit.write_text(json.dumps({'status': 'passed', 'prism_sources': {
        '2026-03-04': {'source_identity': [1, 2, 3], 'release': {'version': 'stable'}}
    } if pinned else {}}))
    target.with_name(target.name+'.manifest.json').write_text(json.dumps({
        'rain_repair': {'status': 'published', 'candidate_audit': str(audit),
                        'published_identity': {'bytes': 4, 'mtime_ns': stat.st_mtime_ns}}}))
    monkeypatch.setattr(module, 'source_signature', lambda p: {
        'signature': 'current', 'inode': 1, 'bytes': 2, 'mtime_ns': 3,
        'release': {'version': 'stable'}})
    result = module.reconcile(tmp_path, 'retro/2026-03-04', {
        'sources': {f'{variable}/2026-03-04': 'current'}})
    assert result['sources'][0]['classification'] == expected
    assert result['queue_acknowledgement_authorized'] is False
