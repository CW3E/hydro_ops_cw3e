"""Audited small-domain Retro continuations with domain-specific end gates."""
# ruff: noqa: DTZ001
import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from run_cnrfc_hu18_pilot import check_parameters
from run_cnrfc_production import forcing_identity, run
from run_repaired_retro import CAMPAIGN, GATES

from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.wrf_hydro.production import restart_check, restart_names

END = datetime(2020, 12, 31)
WAVES = {2001: 'prism-repair-wave5-200101-201012',
         2011: 'prism-repair-wave6-201101-202012',
         2021: 'prism-repair-wave7-202101-20260417'}
PARENTS = {'b120': 'cnrfc', 'cnrfc': 'conus', 'yampa': 'ucrb', 'ucrb': 'conus'}


def accepted_replay(root, path, day, identity):
    """Recognize a completed coupled replay replacing an older rain-only inode."""
    try:
        manifest = json.loads(path.with_name(path.name+'.manifest.json').read_text())
        if manifest.get('verification') == 'accepted_thermal_refresh_static_envelope':
            proof = manifest['temperature_refresh']
            publication = Path(proof['publication_acceptance']).resolve()
            if not publication.is_relative_to((root/'forcing/work/temperature-refresh').resolve()):
                return False
            receipt = json.loads(publication.read_text())
            return (proof.get('unchanged_nonthermal_payloads') is True
                    and manifest.get('day') == day.strftime('%Y-%m-%d')
                    and manifest.get('forcing_stream') == 'retro'
                    and manifest.get('complete_day') is True
                    and manifest.get('derived_products_pending') is False
                    and receipt.get('status') == 'passed'
                    and receipt.get('rain_preserved') is True
                    and receipt.get('published_artifacts', {}).get(str(path)) == identity)
        replay = Path(manifest['replay_provenance']['replay']).resolve()
        if not replay.is_relative_to((root/'forcing/work/isolated-revision-replay').resolve()):
            return False
        receipt = json.loads((replay.parent/'publication/acceptance.json').read_text())
        return (manifest.get('verification') == 'accepted_coupled_replay_static_envelope'
                and manifest.get('complete_day') is True
                and manifest.get('derived_products_pending') is False
                and receipt.get('status') == 'passed' and receipt.get('stream') == 'retro'
                and receipt.get('day') == day.strftime('%Y-%m-%d')
                and receipt.get('published_artifacts', {}).get(str(path)) == identity)
    except (OSError, KeyError, ValueError):
        return False


def audit_day(root, domain, day):
    wave = root/'forcing/work'/WAVES[max(y for y in WAVES if y <= day.year)]
    relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
    publication = json.loads((wave/'production'/f'{day:%Y-%m-%d}.publication.json').read_text())
    path = root/'forcing/outputs/conus/retro/hourly'/relative
    identity = forcing_identity(path)
    actual = dict(zip(('inode', 'bytes', 'mtime_ns'), identity, strict=True))
    if ((publication.get('status') != 'published' or publication.get('published_identity') != actual
            or publication.get('active_rain_coverage_verified') is not True
            or publication.get('outside_envelope_missing') is not True)
            and not accepted_replay(root, path, day, identity)):
        raise ValueError(f'Unverified corrected CONUS precipitation: {day}')
    pinned = {str(path): identity}
    children = (domain,) if PARENTS[domain] == 'conus' else (PARENTS[domain], domain)
    for child in children:
        target = root/f'forcing/outputs/{child}/retro/hourly'/relative
        receipt = json.loads(target.with_name(target.name+'.subset.json').read_text())
        output_identity = forcing_identity(target)
        if (receipt.get('status') != 'passed' or receipt.get('missing_active_values') != 0
                or receipt.get('records') != 24 or receipt.get('retained_values_exact_match') is not True
                or receipt.get('source_identity') != identity
                or receipt.get('output_identity') != output_identity
                or Path(receipt['source']).resolve() != path.resolve()):
            raise ValueError(f'Stale or unaccepted subset: {target}')
        pinned[str(target)] = output_identity
        path, identity = target, output_identity
    return pinned


def audit_year(root, domain, year, end=END):
    stop = min(datetime(year+1, 1, 1), end)
    pins = {}
    day = datetime(year, 1, 1)
    while day <= stop:
        pins.update(audit_day(root, domain, day))
        day += timedelta(days=1)
    return {'status': 'passed', 'domain': domain, 'year': year,
            'end': str(stop), 'identities': pins}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--domain', choices=('b120', 'yampa', 'ucrb', 'cnrfc'))
    parser.add_argument('--year', type=int, choices=range(2001, 2027))
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    extended = args.domain in ('ucrb', 'cnrfc')
    end = datetime(2026, 4, 17) if extended else END
    if args.year is not None and args.year > end.year:
        parser.error('Year outside domain authorization')
    label = f'{args.domain}-extension-2001-20260417' if extended else 'extension-2001-2020'
    evidence = root/'nwm/status'/CAMPAIGN/label
    if args.preflight:
        for first_year, name in WAVES.items():
            if first_year > end.year:
                continue
            if json.loads((root/'forcing/work'/name/'acceptance.json').read_text())['status'] != 'passed':
                raise ValueError('Forcing campaign acceptance failed')
        for domain in ((args.domain,) if extended else ('b120', 'yampa')):
            previous = json.loads((root/'nwm/runs'/domain/'retro'/CAMPAIGN/'2000-passed.json').read_text())
            if not previous.get('passed') or previous.get('end') != '2001-01-01 00:00:00':
                raise ValueError('Year 2000 not accepted')
            restart_check([Path(p) for p in previous['restarts']], datetime(2001, 1, 1))
            for year in range(2001, end.year+1):
                result = audit_year(root, domain, year, end=end)
                result['job_id'] = os.environ['SLURM_JOB_ID']
                atomic_json(evidence/f'{domain}-{year}.json', result)
                print(domain, year, 'forcing publication and subset chain verified', flush=True)
        atomic_json(evidence/'acceptance.json', {'status': 'passed', 'end': str(end),
                                               'job_id': os.environ['SLURM_JOB_ID']})
        return
    if args.domain is None or args.year is None:
        parser.error('Model execution requires --domain and --year')
    accepted = json.loads((evidence/'acceptance.json').read_text())
    if accepted['status'] != 'passed' or accepted['end'] != str(end):
        raise ValueError('Extension preflight failed')
    pins = json.loads((evidence/f'{args.domain}-{args.year}.json').read_text())
    for path, identity in pins['identities'].items():
        if forcing_identity(Path(path)) != identity:
            raise ValueError(f'Forcing changed after preflight: {path}')
    previous = json.loads((root/'nwm/runs'/args.domain/'retro'/CAMPAIGN/f'{args.year-1}-passed.json').read_text())
    if not previous.get('passed') or previous.get('end') != str(datetime(args.year, 1, 1)):
        raise ValueError('Previous model year not accepted')
    initial = root/f'nwm/restarts/{args.domain}/retro/{args.year}/01'
    check_parameters(root/'nwm/static/domains'/args.domain)
    restart_check([initial/n for n in restart_names(datetime(args.year, 1, 1))], datetime(args.year, 1, 1))
    gate = root/f'nwm/outputs/{args.domain}/tests/repaired-retro-start-{GATES[args.domain]}/acceptance.json'
    run(root, args.year, campaign=CAMPAIGN, archive_gate=gate, domain=args.domain,
        initial_restart_dir=initial, continuation_end=end,
        end_date=end.strftime('%Y-%m-%d') if args.year == end.year else None)


if __name__ == '__main__':
    main()
