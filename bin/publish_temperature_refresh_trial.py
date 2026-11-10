"""Explicit, receipt-gated publication of an accepted thermal-only trial.

No queue acknowledgement: a single PRISM window does not resolve all source
obligations of either calendar day. All originals are retained for recovery.
"""
import argparse
import fcntl
import json
import multiprocessing
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from backfill_cnrfc_forcing import process
from netCDF4 import Dataset
from publish_prism_repair_wave import DOMAINS, PARENTS

from hydro_ops.forcing.model_interval import load_forcing_reducers
from hydro_ops.forcing.nrt_cycle import identity
from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.rain_summary_publication import publish_summary
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.forcing.temperature_refresh import unchanged_payloads
from hydro_ops.forcing.temporal_summary import next_month, numeric_time, summarize, summary_path


def load(path):
    return json.loads(path.read_text())


def scope(acceptance):
    if acceptance.get('status') != 'passed_candidate_only' or acceptance.get('changed_variables') != ['T2D','Q2D','LWDOWN']:
        raise ValueError('Accepted thermal-only candidate required')
    window = date.fromisoformat(acceptance['window'])
    days = [window-timedelta(days=1), window]
    expected = {(s,str(d)) for s in ('retro','nrt') for d in days}
    rows = acceptance['files']
    if len(rows) != 4 or {(r['stream'],r['day']) for r in rows} != expected:
        raise ValueError('Exactly both calendar halves in both streams required')
    for row in rows:
        hours = list(range(12,24)) if row['day'] == str(days[0]) else list(range(12))
        if row['changed_hours'] != hours:
            raise ValueError('Invalid changed-hour scope')
    return days, [days[0]-timedelta(days=1), *days]


def verify_summary_preservation(original, updated):
    changed = {'T2D','T2D_MIN','T2D_MAX','Q2D','LWDOWN'}
    with Dataset(original) as a, Dataset(updated) as b:
        if set(a.variables) != set(b.variables):
            raise ValueError('Summary variable inventory changed')
        for name in a.variables:
            if name in changed:
                continue
            x,y = a[name],b[name]
            if x.shape != y.shape:
                raise ValueError(f'Summary shape changed: {name}')
            # Spatial summary fields have a single time record; bound reads by row.
            if x.ndim >= 2:
                for i in range(0,x.shape[-2],120):
                    left,right = x[...,i:i+120,:],y[...,i:i+120,:]
                    np.testing.assert_array_equal(np.ma.getmaskarray(left), np.ma.getmaskarray(right))
                    np.testing.assert_array_equal(np.ma.asarray(left).compressed(), np.ma.asarray(right).compressed())
            else:
                np.testing.assert_array_equal(x[:],y[:])


def publication_scopes(accepted, work):
    if accepted.get('version') != 2:
        days, daily = scope(accepted)
        return {s:(days,daily) for s in ('retro','nrt')}
    if accepted.get('status') != 'passed_candidate_only' or accepted.get('changed_variables') != ['T2D','Q2D','LWDOWN']:
        raise ValueError('Accepted thermal-only candidate required')
    if digest(work/'plan.json') != accepted['plan_sha256']:
        raise ValueError('Campaign plan changed')
    plan = load(work/'plan.json')
    def keyed(rows):
        result = {(r['stream'],r['day']):(r['windows'],r['changed_hours']) for r in rows}
        if len(result) != len(rows):
            raise ValueError('Duplicate calendar-day writer')
        return result
    if keyed(plan['files']) != keyed(accepted['files']):
        raise ValueError('Accepted scope differs from plan')
    scopes = {}
    for row in accepted['files']:
        if row['stream'] not in ('retro','nrt'):
            raise ValueError('Unknown stream')
        day = date.fromisoformat(row['day'])
        hours = []
        for window in row['windows']:
            delta = (date.fromisoformat(window)-day).days
            if delta not in (0,1):
                raise ValueError('Invalid window/day mapping')
            hours += list(range(12)) if delta == 0 else list(range(12,24))
        if sorted(hours) != row['changed_hours'] or not hours or len(set(hours)) != len(hours):
            raise ValueError('Invalid changed-hour scope')
        days,daily = scopes.setdefault(row['stream'],(set(),set()))
        days.add(day)
        daily.update(day-timedelta(days=int(h == 0)) for h in hours)
    return {s:(sorted(days),sorted(daily)) for s,(days,daily) in scopes.items()}


def summary_task(task):
    source,out,start,stop,reducers,names,units,stream,monthly,original = task
    summarize(source,out,start,stop,reducers,names,units,from_daily=monthly,
              domain='conus',stream=stream,skip_existing=True,replace_stale=True)
    if original is not None:
        verify_summary_preservation(original,out)
    return out


def daily_scope(root, stream, days):
    """Defer absent endpoint products, never silently leave existing ones stale."""
    ready, deferred = [], []
    for day in days:
        start = (day-date(1970,1,1)).days*24
        stamps, missing = [], []
        for d in (day,day+timedelta(days=1)):
            p = root/f'forcing/outputs/conus/{stream}/hourly'/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            if not p.exists():
                missing.append(str(p))
                continue
            with Dataset(p) as ds:
                t = numeric_time(ds['time'])
            stamps.extend(t[(t>start)&(t<=start+24)].tolist())
        if len(set(stamps)) != len(stamps) or any(t != int(t) for t in stamps):
            raise ValueError(f'Invalid daily interval timestamps: {stream} {day}')
        if not missing and np.array_equal(sorted(stamps),np.arange(start+1,start+25)):
            ready.append(day)
            continue
        existing = [summary_path(root/f'forcing/outputs/{d}/{stream}/daily',day)
                    for d in ('conus',*DOMAINS)]
        if any(p.exists() for p in existing):
            raise ValueError(f'Existing daily product has incomplete hourly context: {stream} {day}')
        deferred.append({'day':str(day),'missing_hourly_files':missing,
                         'available_interval_records':len(stamps),
                         'reason':'Incomplete 01–00 UTC interval; no production daily file exists'})
    return ready, deferred


def target_identity(path):
    return list(fingerprint(path)) if path.exists() else None


def monthly_scope(root, stream, months, planned_daily=()):
    ready, deferred = [], []
    for month in months:
        stop = next_month(month)
        missing = [str(month+timedelta(days=i)) for i in range((stop-month).days)
                   if month+timedelta(days=i) not in planned_daily
                   and not summary_path(root/f'forcing/outputs/conus/{stream}/daily',month+timedelta(days=i)).exists()]
        if missing:
            existing = [summary_path(root/f'forcing/outputs/{d}/{stream}/monthly',month,monthly=True)
                        for d in ('conus',*DOMAINS)]
            if any(p.exists() for p in existing):
                raise ValueError('Existing monthly product has incomplete daily context')
            deferred.append({'month':str(month),'missing_daily_context':missing,
                             'reason':'Incomplete month; no production monthly file exists'})
        else:
            ready.append(month)
    return ready, deferred


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--workers', type=int, choices=range(1,9), default=1)
    parser.add_argument('--held-model-job', required=True,
                        help='Pending CNRFC 2026 job explicitly held for input refresh')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    work = args.workspace.resolve()
    if not work.is_relative_to(root/'forcing/work/temperature-refresh'):
        raise ValueError('Expected dedicated candidate workspace')
    accepted = load(work/'acceptance.json')
    scopes = publication_scopes(accepted,work)
    months = sorted({d.replace(day=1) for _,daily in scopes.values() for d in daily})
    job = os.environ['SLURM_JOB_ID']
    if not args.held_model_job.isdecimal():
        raise ValueError('Numeric model job required')
    model = subprocess.check_output(['scontrol','show','job','-o',args.held_model_job],text=True)
    if not all(token in model for token in ('JobState=PENDING','Reason=JobHeldUser',
                                           'JobName=wrfh-cnrfc-repaired-retro-2026-')):
        raise ValueError('Expected CNRFC 2026 job must remain explicitly held')
    state = work/'publication'
    state.mkdir(exist_ok=True)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/thermal-publication')
    scratch.mkdir(parents=True, exist_ok=True)
    report_path = state/'acceptance.json'
    def check_sources():
        for pin in accepted['prism_sources'].values():
            if source_signature(Path(pin['path'])) != pin:
                raise ValueError('PRISM superseded; replan')
    report = {'status':'running','job':job,'window':accepted['window'],
              'originals_retained':True,'queue_acknowledged':False,'streams':['retro','nrt']}
    with ExitStack() as stack:
        locks = [root/'forcing/work/update_nwm_forcing.lock', root/'forcing/status/nrt-gfs/cycle.lock',
                 root/'forcing/outputs/conus/.rain-repair.lock', state/'publication.lock']
        locks += [root/f'forcing/outputs/conus/.rain-repair-{m:%Y%m}.lock' for m in months]
        locks += [root/f'forcing/outputs/{d}/{n}' for d in DOMAINS for n in ('.subset.lock','.subset-pilot.lock')]
        for p in locks:
            p.parent.mkdir(parents=True, exist_ok=True)
            f = stack.enter_context(p.open('a'))
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_sources()
        if report_path.exists() and load(report_path).get('status') == 'passed':
            old = load(report_path)
            for p,pin in old['published_artifacts'].items():
                if list(fingerprint(Path(p))) != pin:
                    raise ValueError('Published artifact superseded; not a no-op')
            atomic_json(state/f'noop-{job}.json', {'status':'passed','noop':True,'production_modified':False})
            print('Accepted publication unchanged; no-op verified', flush=True)
            return
        available_months = {}
        report['monthly_deferred'] = {}
        report['daily_deferred'] = {}
        for stream,(days,daily) in list(scopes.items()):
            ready, report['daily_deferred'][stream] = daily_scope(root,stream,daily)
            scopes[stream] = (days,ready)
            available_months[stream], report['monthly_deferred'][stream] = monthly_scope(
                root,stream,sorted({d.replace(day=1) for d in daily}),planned_daily=ready)
        targets = []
        for stream,(days,daily) in scopes.items():
            for domain in ('conus',*DOMAINS):
                base = root/f'forcing/outputs/{domain}/{stream}'
                targets += [base/'hourly'/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1') for d in days]
                targets += [summary_path(base/'daily',d) for d in daily]
                targets += [summary_path(base/'monthly',m,monthly=True) for m in available_months[stream]]
        plan_path = state/'plan.json'
        if not plan_path.exists():
            for p,pin in accepted['production_pins'].items():
                if list(fingerprint(Path(p))) != pin:
                    raise ValueError('Original forcing changed before publication')
            plan = {'targets':{str(p):target_identity(p) for p in targets},
                    'candidate_audit_sha256':digest(work/'acceptance.json')}
            atomic_json(plan_path,plan)
        plan = load(plan_path)
        if digest(work/'acceptance.json') != plan['candidate_audit_sha256']:
            raise ValueError('Candidate audit changed')
        if set(plan['targets']) != {str(p) for p in targets}:
            raise ValueError('Publication context changed; review frozen target plan')
        progress_path = state/'progress.json'
        progress = load(progress_path) if progress_path.exists() else {}
        hourly_targets = set(accepted['production_pins'])
        for p,pin in plan['targets'].items():
            # Hourly replacement recovery is governed by its transaction receipt.
            if p not in hourly_targets and target_identity(Path(p)) != progress.get(p,pin):
                raise ValueError(f'Unrecorded downstream change; inspect: {p}')
            if pin is None:
                if p in hourly_targets:
                    raise ValueError('Accepted hourly original is missing')
                continue  # New downstream products have no original to snapshot.
            backup = state/'originals'/Path(p).relative_to(root/'forcing/outputs')
            backup.parent.mkdir(parents=True,exist_ok=True)
            if not backup.exists():
                if list(fingerprint(Path(p))) != pin:
                    raise ValueError('Cannot snapshot changed original')
                os.link(p,backup)
                for suffix in ('.manifest.json','.nrt-receipt.json','.subset.json'):
                    sidecar = Path(p+suffix)
                    if sidecar.exists():
                        os.link(sidecar,backup.with_name(backup.name+suffix))
            if list(fingerprint(backup)) != pin:
                raise ValueError('Backup identity mismatch')
        def record(p):
            progress[str(p)] = list(fingerprint(p))
            atomic_json(progress_path,progress)
        receipts = {}
        try:
            for row in accepted['files']:
                stream,d = row['stream'],date.fromisoformat(row['day'])
                relative = d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
                target = root/f'forcing/outputs/conus/{stream}/hourly'/relative
                original = state/'originals'/target.relative_to(root/'forcing/outputs')
                candidate = Path(row['path']).resolve()
                if not candidate.is_relative_to(work/'candidates') or digest(candidate) != row['sha256']:
                    raise ValueError('Candidate changed or outside workspace')
                unchanged_payloads(original,candidate)
                inputs = {str(original):list(fingerprint(original))}
                sources = {k:p for k,p in accepted['prism_sources'].items()
                           if 'windows' not in row or k.split('/')[-1] in row['windows']}
                inputs.update({p['path']:[p[k] for k in ('inode','bytes','mtime_ns')] for p in sources.values()})
                proof = {'status':'passed','candidate_audit':str(work/'acceptance.json'),
                         'candidate_audit_sha256':plan['candidate_audit_sha256'],
                         'sha256':row['sha256'],'unchanged_nonthermal_payloads':True,
                         'changed_hours':row['changed_hours'],'prism_sources':sources,
                         'publication_acceptance':str(report_path)}
                transaction = state/f'{stream}-{d}-hourly.json'
                if not transaction.exists() and list(fingerprint(target)) != plan['targets'][str(target)]:
                    raise ValueError('Hourly destination changed outside transaction')
                publish_summary(candidate,target,transaction,inputs,proof)
                old_meta = original.with_name(original.name+'.manifest.json')
                receipt = load(old_meta)
                for key in ('static_envelope','fully_verified_overrides','overridden_time_variables','replay_provenance'):
                    receipt.pop(key,None)
                receipt.update(status='passed',verified=True,day=str(d),forcing_stream=stream,
                    complete_day=True,latest_model_ready_hour=f'{d}T23:00:00+00:00',prism_constrained=True,
                    daily_file=str(target),published_identity=identity(target),sha256=row['sha256'],
                    verification='accepted_thermal_refresh_static_envelope',temperature_refresh=proof,
                    previous_manifest=str(old_meta),derived_products_pending=True,
                    as_of=datetime.now(UTC).isoformat())
                receipts[target] = receipt
                atomic_json(target.with_name(target.name+'.manifest.json'),receipt)
                if stream == 'nrt':
                    atomic_json(target.with_name(target.name+'.nrt-receipt.json'),receipt)
                record(target)
            reducers,names,units = load_forcing_reducers(root/'config/forcing_daily_reducers.toml')
            pool = stack.enter_context(ProcessPoolExecutor(max_workers=args.workers,mp_context=multiprocessing.get_context('spawn')))
            for stream,(days,daily) in scopes.items():
                base = root/f'forcing/outputs/conus/{stream}'
                products = [base/'hourly'/d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1') for d in days]
                for monthly,dates in ((False,daily),(True,available_months[stream])):
                    tasks = []
                    for d in dates:
                        out = summary_path(base/('monthly' if monthly else 'daily'),d,monthly=monthly)
                        tasks.append((base/('daily' if monthly else 'hourly'),out,d,
                                      next_month(d) if monthly else d+timedelta(days=1),
                                      reducers,names,units,stream,monthly,
                                      (state/'originals'/out.relative_to(root/'forcing/outputs'))
                                      if plan['targets'][str(out)] is not None else None))
                    for out in pool.map(summary_task,tasks):
                        record(out)
                        products.append(out)
                for domain in DOMAINS:
                    mask = root/f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
                    tasks,subset_targets = [],[]
                    mask_digest = digest(mask)
                    for p in products:
                        relative = p.relative_to(base)
                        source = root/f'forcing/outputs/{PARENTS[domain]}/{stream}'/relative
                        target = root/f'forcing/outputs/{domain}/{stream}'/relative
                        tasks.append((source,target,mask,mask_digest,scratch,list(fingerprint(source))))
                        subset_targets.append(target)
                    for target,result in zip(subset_targets,pool.map(process,tasks),strict=True):
                        if result['status'] not in ('published','skipped'):
                            raise ValueError(f'Subsetting failed: {result}')
                        record(target)
            check_sources()
            for target,receipt in receipts.items():
                receipt['derived_products_pending'] = False
                atomic_json(target.with_name(target.name+'.manifest.json'),receipt)
                if receipt['forcing_stream'] == 'nrt':
                    atomic_json(target.with_name(target.name+'.nrt-receipt.json'),receipt)
            artifacts = list(targets)
            for p in targets:
                artifacts += [p.with_name(p.name+s) for s in ('.manifest.json','.nrt-receipt.json','.subset.json') if p.with_name(p.name+s).exists()]
            report.update(status='passed',published_artifacts={str(p):list(fingerprint(p)) for p in artifacts},
                          rain_preserved=True,summary_nonthermal_values_preserved=True,
                          newly_created_products=[p for p,pin in plan['targets'].items() if pin is None])
        except BaseException as error:
            report.update(status='failed',error=str(error))
            raise
        finally:
            atomic_json(report_path,report)
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    main()
