"""Generate one month-owned candidate batch with shared baseline/window caches."""
import argparse
import fcntl
import json
import os
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing import nrt_cycle
from hydro_ops.forcing.operations import OperationalLayout
from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.prism_temperature import (
    apply_daily_temperature_constraint,
    create_prism_temperature_constraints,
)
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.forcing.temperature_refresh import replace_hours


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign',type=Path,required=True)
    parser.add_argument('--task',type=int,required=True)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    campaign = args.campaign.resolve()
    if not campaign.is_relative_to(root/'forcing/work/temperature-refresh'):
        raise ValueError('Dedicated thermal campaign required')
    plan = json.loads((campaign/'plan.json').read_text())
    rows = plan['batches'][args.task]['files']
    job = os.environ['SLURM_JOB_ID']
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/thermal-batch')
    scratch.mkdir(parents=True,exist_ok=False)
    reports = campaign/'batches'
    reports.mkdir(exist_ok=True)
    report_path = reports/f'{args.task:03d}.json'
    if report_path.exists():
        raise ValueError('Existing task report; explicit recovery review required')
    report = {'status':'running','job':job,'task':args.task,'files':[], 'production_modified':False}
    layout = OperationalLayout.project_defaults(root)
    engine = nrt_cycle.RecentNrt(root,scratch,datetime.now(UTC),baseline_root=campaign/'baseline',output_root=campaign/'unused')
    lockdir = campaign/'cache-locks'
    lockdir.mkdir(exist_ok=True)
    def baseline(day):
        with (lockdir/f'baseline-{day}.lock').open('a') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            path,receipt = engine.baseline_day(day)
            if receipt['status'] != 'passed' or not receipt['complete_day']:
                raise ValueError('Baseline failed acceptance')
            return path
    def window(stamp):
        day = date.fromisoformat(stamp)
        out = campaign/'windows'/f'{stamp}.nc'
        receipt = out.with_suffix('.json')
        expected = {v:plan['prism_sources'][f'{v}/{stamp}'] for v in ('tmin','tmax')}
        for pin in expected.values():
            if source_signature(Path(pin['path'])) != pin:
                raise ValueError('PRISM changed; replan required')
        with (lockdir/f'window-{stamp}.lock').open('a') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            if receipt.exists():
                record = json.loads(receipt.read_text())
                if record['sources'] != expected or record['identity'] != list(fingerprint(out)):
                    raise ValueError('Shared window cache changed')
                if any(list(fingerprint(Path(p))) != pin for p,pin in record['input_pins'].items()):
                    raise ValueError('Shared window inputs changed')
                return out
            first,second = baseline(day-timedelta(days=1)),baseline(day)
            input_paths = [first,second,layout.target_grid,layout.target_elevation,
                           root/'forcing/static/prism/prism_an_4km_elevation.nc',
                           root/'forcing/static/remapping/nwm_conus_1km/prism_bilinear.nc']
            input_pins = {str(p):list(fingerprint(p)) for p in input_paths}
            target = scratch/f'target-{stamp}.nc'
            create_prism_temperature_constraints(
                Path(expected['tmin']['path']),Path(expected['tmax']['path']),
                root/'forcing/static/prism/prism_an_4km_elevation.nc',layout.target_grid,layout.target_elevation,
                root/'forcing/static/remapping/nwm_conus_1km/prism_bilinear.nc',target,work_directory=scratch)
            out.parent.mkdir(exist_ok=True)
            apply_daily_temperature_constraint([first]*12+[second]*12,target,out,
                baseline_variable='T2D',source_time_indices=list(range(12,24))+list(range(12)))
            for pin in expected.values():
                if source_signature(Path(pin['path'])) != pin:
                    raise ValueError('PRISM changed while generating window')
            if any(list(fingerprint(Path(p))) != pin for p,pin in input_pins.items()):
                raise ValueError('Window inputs changed during computation')
            atomic_json(receipt,{'status':'passed','sources':expected,'identity':list(fingerprint(out)),
                                 'input_pins':input_pins})
            return out
    try:
        with Dataset(root/'forcing/static/coverage/conus/nldas2_seven_met_static_envelope_v4.nc') as ds:
            active,keep = np.asarray(ds['active'][:],bool),np.asarray(ds['keep'][:],bool)
        for row in rows:
            d = date.fromisoformat(row['day'])
            stream = row['stream']
            relative = d.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            original = campaign/'originals'/stream/relative
            source = root/f'forcing/outputs/conus/{stream}/hourly'/relative
            pin = plan['production_pins'][str(source)]
            if list(fingerprint(source)) != pin or list(fingerprint(original)) != pin:
                raise ValueError('Production/snapshot changed')
            mapping,provenance = {},{}
            for stamp in row['windows']:
                corrected = window(stamp)
                wd = date.fromisoformat(stamp)
                if wd not in (d,d+timedelta(days=1)):
                    raise ValueError('Invalid window/day mapping')
                hours = range(12) if wd==d else range(12,24)
                for h in hours:
                    if h in mapping:
                        raise ValueError('Overlapping window hours')
                    mapping[h] = (corrected,h+12 if wd==d else h-12)
                provenance.update({f'{v}/{stamp}':plan['prism_sources'][f'{v}/{stamp}'] for v in ('tmin','tmax')})
            if sorted(mapping) != row['changed_hours']:
                raise ValueError('Planned changed hours differ')
            staged = scratch/f'{stream}-{d}.nc'
            chunks = replace_hours(original,staged,mapping,active,keep,provenance)
            target = campaign/'candidates'/stream/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                raise ValueError('Candidate already exists')
            checksum = digest(staged)
            partial = target.with_name(target.name+'.part')
            with staged.open('rb') as src,partial.open('xb') as dst:
                shutil.copyfileobj(src,dst,8*1024*1024)
            if digest(partial) != checksum:
                raise ValueError('Candidate transfer mismatch')
            os.link(partial,target)
            partial.unlink()
            staged.unlink()
            if list(fingerprint(source)) != pin:
                raise ValueError('Source changed during candidate generation')
            report['files'].append({**row,'path':str(target),'sha256':checksum,
                                    'unchanged_payload_chunks_verified':chunks})
            atomic_json(report_path,report)
        report['status'] = 'passed_candidate_only'
    except BaseException as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        atomic_json(report_path,report)
        print(json.dumps({'report':str(report_path),'status':report['status'],'files':len(report['files'])}),flush=True)


if __name__ == '__main__':
    main()
