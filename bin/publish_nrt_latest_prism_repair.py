"""Publish audited NRT rain candidates, preserving originals and NRT provenance."""
import fcntl
import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
from backfill_cnrfc_forcing import process
from netCDF4 import Dataset
from publish_prism_repair_wave import DOMAINS, PARENTS, acquire_locks

from hydro_ops.forcing.nrt_cycle import identity
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.rain_summary_publication import publish_summary
from hydro_ops.forcing.repair_transaction import atomic_json


def publish_one(task):
    root, report_path, row, state, scratch = task
    report = json.loads(report_path.read_text())
    candidate = Path(row['path'])
    stamp = candidate.name[:8]
    relative = f'{stamp[:4]}/{stamp[4:6]}/{candidate.name}'
    target = root/'forcing/outputs/conus/nrt/hourly'/relative
    original = report_path.parent/'originals'/relative
    receipt = state/f'{stamp}.transaction.json'
    old = state/f'{stamp}.original-manifest.json'
    manifest = target.with_name(target.name+'.manifest.json')
    if not receipt.exists():
        if fingerprint(target) != fingerprint(original):
            raise ValueError(f'Original NRT changed: {stamp}')
        previous = json.loads(manifest.read_text())
        if old.exists() and json.loads(old.read_text()) != previous:
            raise ValueError('Previous provenance changed')
        atomic_json(old,previous)
    if digest(candidate) != row['sha256']:
        raise ValueError('Candidate changed after acceptance')
    inputs = {str(original):list(fingerprint(original))}
    for info in report['prism_sources'].values():
        inputs[info['source']] = info['source_identity']
    proof = {'status':'passed','candidate_audit':str(report_path),
             'candidate_audit_sha256':digest(report_path),
             'nonrain_compressed_chunks_unchanged':True,'sha256':row['sha256']}
    result = publish_summary(candidate,target,receipt,inputs,proof)
    previous = json.loads(old.read_text())
    current = dict(previous)
    # These attestations describe the old rain/inode; retain them only in the
    # immutable previous manifest, not as evidence for the repaired product.
    for key in ('static_envelope','fully_verified_overrides','overridden_time_variables'):
        current.pop(key,None)
    current.update(status='passed',verified=True,day=f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}',
                   daily_file=str(target),forcing_stream='nrt',published_identity=identity(target),
                   sha256=row['sha256'],complete_day=True,prism_constrained=True,
                   previous_manifest=str(old),rain_repair=proof,derived_products_pending=True,
                   verification='rain_readback_active_envelope_and_nonrain_raw_chunks',
                   input_fingerprint=hashlib.sha256(json.dumps(proof,sort_keys=True).encode()).hexdigest(),
                   as_of=datetime.now(UTC).isoformat())
    nrt_receipt=target.with_name(target.name+'.nrt-receipt.json')
    atomic_json(nrt_receipt,current)
    current['nrt_receipt']=str(nrt_receipt)
    atomic_json(manifest,current)
    subsets={}
    for domain in DOMAINS:
        source=root/'forcing/outputs'/PARENTS[domain]/'nrt/hourly'/relative
        destination=root/'forcing/outputs'/domain/'nrt/hourly'/relative
        mask=root/'nwm/static/domains'/domain/'masks'/f'{domain}_masks.nc'
        subset=process((source,destination,mask,digest(mask),scratch,list(fingerprint(source))))
        if subset['status'] not in ('published','skipped'):
            raise ValueError(f'Subset failed: {domain}/{stamp}')
        subsets[domain]=subset['status']
    return {'day':stamp,'publication':result,'subsets':subsets}


def main():
    root=Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    campaign=root/'forcing/work/nrt-latest-prism-campaign'
    state=campaign/'publication'
    state.mkdir(exist_ok=True)
    scratch=Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}/nrt-publish")
    scratch.mkdir(parents=True,exist_ok=True)
    tasks=[]
    previous_path=None
    for i in range(8):
        task=json.loads((campaign/f'task-{i}.json').read_text())
        if task.get('status')!='passed':raise ValueError('Incomplete candidate task')
        path=Path(task['candidate_report'])
        report=json.loads(path.read_text())
        if report.get('status')!='passed' or report.get('stream')!='nrt' or len(report['files'])!=task['days']:
            raise ValueError('Candidate acceptance failed')
        if previous_path is not None:
            label=date.fromisoformat(task['start'])
            relative=label.strftime('windows/%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            with Dataset(previous_path.parent/relative) as left, Dataset(path.parent/relative) as right:
                for name in ('RAINRATE','prism_timing_source_id'):
                    for h in range(24):
                        a,b=left[name][h],right[name][h]
                        np.testing.assert_array_equal(np.ma.getmaskarray(a),np.ma.getmaskarray(b))
                        np.testing.assert_array_equal(a.compressed(),b.compressed())
        previous_path=path
        tasks.extend((root,path,row,state,scratch) for row in report['files'])
    if len(tasks)!=240:raise ValueError('Expected 240 constrained NRT days, excluding initial boundary')
    report={'status':'running','job_id':os.environ['SLURM_JOB_ID'],'days':[],
            'originals_retained':True,'summaries_status':'pending'}
    with ExitStack() as stack:
        lock=stack.enter_context((root/'forcing/status/nrt-gfs/cycle.lock').open('a'))
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        first=date(2026,3,3)
        last=date(2026,10,28)
        acquire_locks(stack,root,first,last,blocking=True)
        try:
            # Serial first-day canary exercises production metadata and subsets.
            report['days'].append(publish_one(tasks[0]))
            atomic_json(state/'acceptance.json',report)
            with ProcessPoolExecutor(max_workers=4) as pool:
                for result in pool.map(publish_one,tasks[1:]):
                    report['days'].append(result)
                    atomic_json(state/'acceptance.json',report)
            report['status']='passed'
        except BaseException as error:
            report.update(status='failed',error=str(error))
            raise
        finally:
            atomic_json(state/'acceptance.json',report)


if __name__=='__main__':
    main()
