"""Freeze remaining accepted diagnostic scope and submit month-owned candidates."""
import argparse
import json
import os
import subprocess
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.rain_repair_writer import fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json


def calendar_rows(streams, excluded):
    rows = {}
    for stream,record in streams.items():
        for window in record['windows']:
            stamp = window['day']
            if (stream,stamp) in excluded:
                continue
            day = date.fromisoformat(stamp)
            for d,hours in ((day-timedelta(days=1),list(range(12,24))), (day,list(range(12)))):
                key = (stream,str(d))
                row = rows.setdefault(key,{'stream':stream,'day':str(d),'windows':[],'changed_hours':[]})
                if set(row['changed_hours']) & set(hours):
                    raise ValueError('Duplicate window scope')
                row['windows'].append(stamp)
                row['changed_hours'] = sorted(row['changed_hours']+hours)
    return [rows[k] for k in sorted(rows)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-plan',type=Path,required=True)
    parser.add_argument('--pilot-publication',type=Path,required=True)
    parser.add_argument('--held-model-job',required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    parent = root/'forcing/work/temperature-refresh'
    old = json.loads(args.refresh_plan.read_text())
    pilot = json.loads(args.pilot_publication.read_text())
    model = subprocess.check_output(['scontrol','show','job','-o',args.held_model_job],text=True)
    if not all(token in model for token in ('JobState=PENDING','Reason=JobHeldUser','JobName=wrfh-cnrfc-repaired-retro-2026-')):
        raise ValueError('CNRFC affected model year must remain held')
    if pilot['status'] != 'passed' or not pilot['rain_preserved']:
        raise ValueError('Successful pilot publication required')
    for p,pin in pilot['published_artifacts'].items():
        if list(fingerprint(Path(p))) != pin:
            raise ValueError('Pilot artifact changed')
    campaign = parent/datetime.now(UTC).strftime('campaign-%Y%m%dT%H%M%SZ')
    campaign.mkdir(exist_ok=False)
    excluded = {(s,pilot['window']) for s in pilot['streams']}
    rows = calendar_rows(old['streams'],excluded)
    sources,diagnostic_pins = {},{}
    for stream,record in old['streams'].items():
        for window in record['windows']:
            if (stream,window['day']) in excluded:
                continue
            diagnostic = json.loads(Path(window['diagnostic']).read_text())
            diagnostic_pins.update(diagnostic['streams'][stream]['forcing_pins'])
            for v,pin in window['prism_sources'].items():
                if source_signature(Path(pin['path'])) != pin:
                    raise ValueError('PRISM superseded; repeat diagnostics')
                sources[f'{v}/{window["day"]}'] = pin
    pins = {}
    batches = defaultdict(list)
    for row in rows:
        stream,d = row['stream'],date.fromisoformat(row['day'])
        relative = d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
        source = root/f'forcing/outputs/conus/{stream}/hourly'/relative
        expected = pilot['published_artifacts'].get(str(source),diagnostic_pins[str(source)])
        if list(fingerprint(source)) != expected:
            raise ValueError('Forcing changed outside accepted pilot')
        original = campaign/'originals'/stream/relative
        original.parent.mkdir(parents=True,exist_ok=True)
        os.link(source,original)
        if list(fingerprint(original)) != expected or list(fingerprint(source)) != expected:
            raise ValueError('Snapshot race')
        pins[str(source)] = expected
        batches[d.strftime('%Y-%m')].append(row)
    plan = {'version':2,'files':rows,'prism_sources':sources,'production_pins':pins,
            'excluded_accepted_windows':sorted([list(x) for x in excluded]),
            'batches':[{'month':m,'files':batches[m]} for m in sorted(batches)],
            'production_modified':False}
    atomic_json(campaign/'plan.json',plan)
    env = dict(os.environ)
    env.pop('SBATCH_RESERVATION',None)
    def submit(name,cmd):
        atomic_json(campaign/f'{name}-submission.json',{'status':'submission_uncertain','command':cmd})
        job = subprocess.check_output(cmd,cwd=root,env=env,text=True).strip().split(';')[0]
        if not job.isdecimal():
            raise ValueError('Uncertain submission; inspect before retry')
        atomic_json(campaign/f'{name}-submission.json',{'status':'submitted','job':job,'command':cmd})
        return job
    array = submit('candidates',['sbatch','--parsable',f'--array=0-{len(batches)-1}%8',
        '--job-name=prism-thermal-monthly-candidates','slurm/run_temperature_refresh_batch.sh','--campaign',str(campaign)])
    collector = submit('acceptance',['sbatch','--parsable',f'--dependency=afterok:{array}',
        '--job-name=prism-thermal-batch-acceptance','slurm/collect_temperature_refresh_campaign.sh','--campaign',str(campaign)])
    publication = submit('publication',['sbatch','--parsable',f'--dependency=afterok:{collector}',
        '--time=48:00:00','--job-name=prism-thermal-batch-publication-summaries-subsets',
        'slurm/publish_temperature_refresh_trial.sh','--workspace',str(campaign),
        '--held-model-job',args.held_model_job,'--workers','8'])
    print(json.dumps({'campaign':str(campaign),'candidate_array':array,'acceptance':collector,
                      'publication':publication,'months':len(batches),'files':len(rows)}))


if __name__ == '__main__':
    main()
