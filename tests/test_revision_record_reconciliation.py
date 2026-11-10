import hashlib
import json
from pathlib import Path

import pytest
from test_nwm_forcing_coordinator import load_script


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    return load_script('reconcile_prism_revision_records.py'), load_script('acknowledge_thermal_revision_records.py')


def test_identity_formats(tmp_path, modules):
    inventory, _ = modules
    target = tmp_path/'data'
    target.write_text('test')
    st = target.stat()
    assert inventory.identity_matches([st.st_ino, st.st_size, st.st_mtime_ns], target)
    assert inventory.identity_matches({'bytes': st.st_size, 'mtime_ns': st.st_mtime_ns}, target)
    assert not inventory.identity_matches({}, target)
    assert not inventory.identity_matches([st.st_ino, 0, st.st_mtime_ns], target)


def test_uncertain_sources_never_acknowledged(modules):
    _, ack = modules
    row = {'thermal_publication': 'report', 'sources': [
        {'classification': 'accepted_thermal_source_signature_matches'},
        {'classification': 'accepted_rain_source_identity_matches'}]}
    assert ack.eligible(row)
    assert not ack.eligible(row | {'thermal_publication': None})
    assert not ack.eligible(row | {'sources': []})
    for classification in ('queue_superseded', 'accepted_rain_repair_source_version_unrecorded',
                           'unresolved_no_exact_source_evidence'):
        assert not ack.eligible(row | {'sources': [{'classification': classification}]})


def test_all_publication_artifacts_must_still_match(tmp_path, modules):
    _, ack = modules
    artifact = tmp_path/'subset'
    artifact.write_text('data')
    st = artifact.stat()
    report = tmp_path/'report.json'
    report.write_text(json.dumps({'status': 'passed', 'rain_preserved': True,
                                 'summary_nonthermal_values_preserved': True,
                                 'published_artifacts': {str(artifact): [st.st_ino, st.st_size, st.st_mtime_ns]}}))
    assert ack.publication_valid(str(report))
    artifact.write_text('changed')
    assert not ack.publication_valid(str(report))


def test_accepted_candidate_requires_current_publication(tmp_path, modules, monkeypatch):
    inventory, _ = modules
    target = tmp_path/'forcing/outputs/conus/nrt/hourly/2026/05/20260515.LDASIN_DOMAIN1'
    target.parent.mkdir(parents=True)
    target.write_text('data')
    st = target.stat()
    candidate = tmp_path/'candidate.json'
    candidate.write_text(json.dumps({'status': 'passed_candidate_only'}))
    publication = tmp_path/'publication.json'
    publication.write_text(json.dumps({'status': 'passed', 'published_artifacts': {
        str(target): [st.st_ino, st.st_size, st.st_mtime_ns]}}))
    manifest = {'published_identity': {'bytes': st.st_size, 'mtime_ns': st.st_mtime_ns},
                'temperature_refresh': {
                    'status': 'passed', 'publication_acceptance': str(publication),
                    'candidate_audit': str(candidate),
                    'candidate_audit_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    'prism_sources': {'tmin/2026-05-15': {'signature': 'exact'}}}}
    Path(str(target)+'.manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(inventory, 'source_signature', lambda path: {'signature': 'exact', 'release': {}})
    event = {'sources': {'tmin/2026-05-15': 'exact'}}
    row = inventory.reconcile(tmp_path, 'nrt/2026-05-15', event)
    assert row['sources'][0]['classification'] == 'accepted_thermal_source_signature_matches'
    target.write_text('modified after publication')
    row = inventory.reconcile(tmp_path, 'nrt/2026-05-15', event)
    assert row['sources'][0]['classification'] == 'unresolved_no_exact_source_evidence'


def test_cycle_gate_requires_all_six_publications(tmp_path):
    gate = load_script('test_forcing_cycle_after_model_refresh.py')
    plan = tmp_path/'plan.json'
    plan.write_text('{}')
    for domain in ('yampa', 'b120', 'ucrb'):
        for stream in ('retro', 'nrt'):
            path = tmp_path/domain/stream/'acceptance.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'status': 'complete', 'domain': domain,
                'stream': stream, 'plan_sha256': hashlib.sha256(plan.read_bytes()).hexdigest(),
                'published_files': 10, 'job': '123'}))
    assert len(gate.check_models(tmp_path)) == 6
    path.write_text(json.dumps({'status': 'failed'}))
    with pytest.raises(ValueError, match='not accepted'):
        gate.check_models(tmp_path)
