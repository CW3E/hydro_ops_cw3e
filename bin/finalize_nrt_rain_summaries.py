"""Refresh summaries and their four subsets only after accepted NRT rain publication."""
import fcntl
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from pathlib import Path

from backfill_cnrfc_forcing import discover, process
from publish_prism_repair_wave import DOMAINS, PARENTS

from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json


def run():
    root=Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    state=root/'forcing/work/nrt-latest-prism-campaign'
    accepted=json.loads((state/'publication/acceptance.json').read_text())
    if accepted.get('status')!='passed' or len(accepted['days'])!=240:
        raise ValueError('Complete hourly publication/subsets required')
    previous_path = state/'summary-acceptance.json'
    if previous_path.exists():
        previous = json.loads(previous_path.read_text())
        old_job = str(previous.get('job_id', 'legacy'))
        if not (old_job.isdigit() or old_job == 'legacy'):
            raise ValueError('Unexpected previous summary job identifier')
        archived = state/f'summary-acceptance-{old_job}.json'
        if not archived.exists():
            atomic_json(archived, previous)
    report={'status':'running','domains':{},'job_id':os.environ['SLURM_JOB_ID']}
    scratch=Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}/summary-subsets")
    scratch.mkdir(parents=True,exist_ok=True)
    try:
        subprocess.run([sys.executable,'bin/refresh_nrt_summaries.py'],check=True)
        for domain in DOMAINS:
            source=root/'forcing/outputs'/PARENTS[domain]/'nrt'
            destination=root/'forcing/outputs'/domain/'nrt'
            mask=root/'nwm/static/domains'/domain/'masks'/f'{domain}_masks.nc'
            paths = discover(source.parent, ('nrt',), ('daily', 'monthly'))
            tasks=[(p,destination/p.relative_to(source),mask,digest(mask),scratch,list(fingerprint(p)))
                   for p in paths]
            with ProcessPoolExecutor(max_workers=4) as pool:
                results=list(pool.map(process,tasks))
            if any(r['status'] not in ('published','skipped') for r in results):
                raise ValueError(f'Summary subset failure: {domain}')
            report['domains'][domain]=len(results)
            report.setdefault('resolution_counts', {})[domain] = {
                resolution: sum(p.relative_to(source).parts[0] == resolution for p in paths)
                for resolution in ('daily', 'monthly')}
            atomic_json(state/'summary-acceptance.json',report)
        report['status']='passed'
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        atomic_json(state/'summary-acceptance.json',report)
        atomic_json(state/f'summary-acceptance-{os.environ["SLURM_JOB_ID"]}.json',report)


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    with ExitStack() as stack:
        for path in [root/'forcing/status/nrt-gfs/cycle.lock', *[
                root/f'forcing/outputs/{domain}/.subset.lock' for domain in DOMAINS]]:
            handle = stack.enter_context(path.open('a'))
            fcntl.flock(handle, fcntl.LOCK_EX)
        run()


if __name__=='__main__':
    main()
