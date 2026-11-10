import importlib
import json
import os
import shutil
from pathlib import Path


def test_nrt_publication_preserves_provenance_and_is_repeatable(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'bin'))
    tool=importlib.import_module('publish_nrt_latest_prism_repair')
    relative='2026/04/20260412.LDASIN_DOMAIN1'
    target=tmp_path/'forcing/outputs/conus/nrt/hourly'/relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b'old-nrt')
    target.with_name(target.name+'.manifest.json').write_text(json.dumps({'source_files':['original-hrrr'], 'gfs_hours':4}))
    campaign=tmp_path/'forcing/work/test'
    original=campaign/'originals'/relative
    original.parent.mkdir(parents=True)
    os.link(target,original)
    candidate=campaign/'hourly'/relative
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b'new-rain')
    report=campaign/'acceptance.json'
    report.write_text(json.dumps({'prism_sources':{}}))
    state=campaign/'publication'
    state.mkdir()
    for domain in tool.DOMAINS:
        mask=tmp_path/'nwm/static/domains'/domain/'masks'/f'{domain}_masks.nc'
        mask.parent.mkdir(parents=True)
        mask.write_bytes(b'mask')
    def subset(task):
        source,destination,*_=task
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,destination)
        return {'status':'published'}
    monkeypatch.setattr(tool,'process',subset)
    row={'path':str(candidate),'sha256':tool.digest(candidate)}
    task=(tmp_path,report,row,state,tmp_path)
    result=tool.publish_one(task)
    assert len(result['subsets'])==4
    assert original.read_bytes()==b'old-nrt'
    assert target.read_bytes()==b'new-rain'
    manifest=json.loads(target.with_name(target.name+'.manifest.json').read_text())
    assert manifest['source_files']==['original-hrrr']
    assert manifest['gfs_hours']==4
    assert manifest['forcing_stream']=='nrt'
    assert not (tmp_path/'forcing/outputs/conus/retro').exists()
    assert tool.publish_one(task)['publication']['status']=='unchanged'
