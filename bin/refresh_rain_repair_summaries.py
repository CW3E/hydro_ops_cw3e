#!/usr/bin/env python3
"""Gated monthly rain-only summary refresh after accepted hourly repair publication."""
import argparse
import fcntl
import json
import os
import time
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path

from backfill_cnrfc_forcing import process
from backfill_forcing_summaries import audited, publication_locks
from publish_prism_repair_wave import DOMAINS, PARENTS, acquire_locks

from hydro_ops.forcing.model_interval import load_forcing_reducers
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.rain_summary_publication import check_inputs, publish_summary
from hydro_ops.forcing.rain_summary_repair import repair_summary
from hydro_ops.forcing.repair_transaction import atomic_json, sandbox
from hydro_ops.forcing.temporal_summary import input_path, next_month, summarize, summary_path


def prior_hourly(summary, current):
    """Bind old summary sources to current files or verified rain-only originals."""
    from netCDF4 import Dataset
    with Dataset(summary) as ds:
        inventory = json.loads(ds.source_files)
    if len(inventory) != len(current):
        raise ValueError('Old hourly inventory incomplete')
    prior = []
    for row, path in zip(inventory, current, strict=True):
        manifest = json.loads(path.with_name(path.name+'.manifest.json').read_text())
        publication = Path(manifest['static_envelope']['audit'])
        tx = json.loads((publication.parent/f"{manifest['day']}.transaction.json").read_text())
        plan = json.loads((publication.parent/f"{manifest['day']}.plan.json").read_text())
        original = Path(tx['original_snapshot'])
        if list(fingerprint(original)) != plan['old_identity']:
            raise ValueError('Preserved hourly original changed')
        options = [p for p in (path, original)
                   if (p.stat().st_size, p.stat().st_mtime_ns) == (row['bytes'], row['mtime_ns'])]
        if not options:
            raise ValueError('Cannot prove non-rain provenance')
        prior.append(options[0])
    return prior


def refresh(root, month, workspace, outputs, *, inject_after_replace=False, end=None):
    if month.day != 1 or month < date(1981, 1, 1):
        raise ValueError('Expected a daily-PRISM calendar month, 1981 or later')
    stop = next_month(month) if end is None else end+timedelta(days=1)
    if not month < stop <= next_month(month):
        raise ValueError('End must lie in the requested month')
    complete_month = stop == next_month(month)
    workspace = sandbox(workspace, root)
    workspace.mkdir(parents=True, exist_ok=True)
    parent = outputs/'conus/retro'
    parent.mkdir(parents=True, exist_ok=True)
    hourly = root/'forcing/outputs/conus/retro/hourly'
    days = [month+timedelta(days=i) for i in range((stop-month).days)]
    sources = [input_path(hourly, d) for d in [*days, stop]]
    reducers, names, units = load_forcing_reducers(root/'config/forcing_daily_reducers.toml')
    spec = {'reducers': reducers, 'output_names': names, 'output_units': units}
    scratch = Path(f"/scratch/{os.environ['SLURM_JOB_USER']}/job_{os.environ['SLURM_JOB_ID']}/rain-summaries")
    scratch.mkdir(parents=True, exist_ok=True)
    config = {'month': str(month), 'outputs': str(outputs.resolve()), 'spec': spec}
    if not complete_month:
        config['end'] = str(end)
    report = {'status': 'running', 'config': config, 'daily': [], 'monthly': None,
              'production_modified': outputs.resolve() == (root/'forcing/outputs').resolve()}
    with ExitStack() as stack:
        stack.enter_context(publication_locks(parent, month, stop-timedelta(days=1), True))
        # Adjacent yearly workers can meet at December/January. Acquire ordered
        # month locks with waiting, rather than failing a valid boundary overlap.
        acquire_locks(stack, root, month, stop, blocking=True)
        for domain in DOMAINS:
            base = outputs/domain
            base.mkdir(parents=True, exist_ok=True)
            for name in ('.subset.lock', '.subset-pilot.lock'):
                handle = stack.enter_context((base/name).open('a'))
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            handle = stack.enter_context((base/f'.summary-subset-{month.year}.lock').open('a'))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config_path = workspace/'config.json'
        if config_path.exists() and json.loads(config_path.read_text()) != config:
            raise ValueError('Workspace belongs to a different summary refresh')
        atomic_json(config_path, config)
        # Refuse unrepaired boundary hours, even if a legacy hourly file exists.
        for path in sources:
            audited(path)
            manifest = json.loads(path.with_name(path.name+'.manifest.json').read_text())
            if not manifest.get('rain_repair', {}).get('nonrain_compressed_chunks_unchanged'):
                raise ValueError(f'Hourly rain-only repair not yet published: {path}')
        source_ids = {str(p): list(fingerprint(p)) for p in sources}
        # Preserve every old daily BEFORE publishing any, so monthly reuse remains provable.
        periods = [(d, False) for d in days] + ([(month, True)] if complete_month else [])
        for day, monthly in periods:
            resolution = 'monthly' if monthly else 'daily'
            old = summary_path(parent/resolution, day, monthly=monthly)
            backup = summary_path(workspace/'originals'/resolution, day, monthly=monthly)
            if old.exists() and not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.link(old, backup)
        injected = False

        def one(day, end, monthly):
            nonlocal injected
            resolution = 'monthly' if monthly else 'daily'
            target = summary_path(parent/resolution, day, monthly=monthly)
            old = summary_path(workspace/'originals'/resolution, day, monthly=monthly)
            candidate = summary_path(workspace/'candidates'/resolution, day, monthly=monthly)
            evidence = candidate.with_name(candidate.name+'.audit.json')
            receipt = candidate.with_name(candidate.name+'.publication.json')
            inputs = ([summary_path(parent/'daily', d) for d in days] if monthly else
                      [input_path(hourly, d) for d in (day, end)])
            pins = {str(p): list(fingerprint(p)) for p in inputs}
            started = time.monotonic()
            if evidence.exists():
                validation = json.loads(evidence.read_text())
                if validation['inputs'] != pins or digest(candidate) != validation['sha256']:
                    raise ValueError('Candidate inputs/content changed; inspect workspace')
            else:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                if receipt.exists():
                    raise ValueError('Publication receipt exists without candidate audit')
                candidate.unlink(missing_ok=True)
                method, reason = 'rain_only', None
                try:
                    if not old.exists():
                        raise ValueError('Existing summary missing')
                    if monthly and any(row['method'] != 'rain_only' for row in report['daily']):
                        raise ValueError('Daily full-regeneration fallback requires full monthly reduction')
                    prior = ([summary_path(workspace/'originals/daily', d) for d in days]
                             if monthly else prior_hourly(old, inputs))
                    repair_summary(old, candidate, inputs, prior, day, end, spec, monthly=monthly)
                except (ValueError, AssertionError, KeyError, AttributeError, FileNotFoundError) as error:
                    # No publication occurred. Full reduction is the compatibility fallback.
                    candidate.unlink(missing_ok=True)
                    method, reason = 'full_regeneration', str(error)
                    summarize(parent/'daily' if monthly else hourly, candidate, day, end,
                              reducers, names, units, from_daily=monthly, domain='conus', stream='retro')
                check_inputs(pins)
                validation = {'status': 'passed', 'method': method, 'fallback_reason': reason,
                              'inputs': pins, 'sha256': digest(candidate),
                              'candidate_seconds': time.monotonic()-started}
                atomic_json(evidence, validation)

            def interrupt():
                nonlocal injected
                if inject_after_replace and not injected:
                    injected = True
                    raise RuntimeError('Intentional post-replace test interruption')

            publication = publish_summary(candidate, target, receipt, pins, validation,
                                          after_replace=interrupt if inject_after_replace else None)
            subsets = {}
            relative = target.relative_to(parent)
            for domain in DOMAINS:
                source = outputs/PARENTS[domain]/'retro'/relative
                output = outputs/domain/'retro'/relative
                mask = root/f'nwm/static/domains/{domain}/masks/{domain}_masks.nc'
                result = process((source, output, mask, digest(mask), scratch, list(fingerprint(source))))
                if result['status'] not in ('published', 'skipped'):
                    raise RuntimeError(f'Summary subsetting failed: {result}')
                subsets[domain] = result
            return {'day': str(day), 'method': validation['method'], 'publication': publication,
                    'subsets': subsets, 'seconds': time.monotonic()-started}

        try:
            for day in days:
                report['daily'].append(one(day, day+timedelta(days=1), False))
                atomic_json(workspace/'status.json', report)
            if complete_month:
                report['monthly'] = one(month, stop, True)
            else:
                report['monthly_deferred'] = 'Next-month midnight is not yet repaired'
                report['daily_end'] = str(end)
            check_inputs(source_ids)
            report['status'] = 'passed'
        except BaseException as error:
            report.update(status='failed', error=str(error))
            raise
        finally:
            atomic_json(workspace/'status.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--month', type=date.fromisoformat, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--outputs-root', type=Path)
    parser.add_argument('--production', action='store_true')
    parser.add_argument('--end', type=date.fromisoformat,
                        help='Optional last daily date within month; defer incomplete monthly summary')
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    if args.production:
        if args.outputs_root:
            parser.error('--production cannot be combined with --outputs-root')
        outputs = root/'forcing/outputs'
    elif args.outputs_root:
        outputs = sandbox(args.outputs_root, root)
    else:
        parser.error('Specify isolated --outputs-root or explicitly authorize --production')
    refresh(root, args.month, args.workspace, outputs, end=args.end)
