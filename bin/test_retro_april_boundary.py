"""Isolated April 18 repair trial with explicitly frozen April 19 NRT boundary."""
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from netCDF4 import Dataset

from hydro_ops.forcing.repair_transaction import atomic_json, create_snapshot


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    job = os.environ['SLURM_JOB_ID']
    work = root/'forcing/work/retro-april-boundary'/f'job-{job}'
    work.mkdir(parents=True, exist_ok=False)
    retro = root/'forcing/outputs/conus/retro/hourly'
    nrt = root/'forcing/outputs/conus/nrt/hourly/2026/04/20260419.LDASIN_DOMAIN1'
    report = {'status': 'running', 'production_modified': False, 'boundary_stream': 'nrt',
              'boundary_file': str(nrt), 'repair_day': '2026-04-18'}
    try:
        with Dataset(retro/'2026/04/20260418.LDASIN_DOMAIN1') as a, Dataset(nrt) as b:
            report['schema'] = {}
            for name in ('RAINRATE', 'T2D', 'Q2D', 'PSFC', 'U2D', 'V2D', 'SWDOWN', 'LWDOWN'):
                if a[name].shape != b[name].shape or a[name].dimensions != b[name].dimensions:
                    raise ValueError(f'Boundary schema mismatch: {name}')
                report['schema'][name] = list(b[name].shape)
        create_snapshot(retro, work/'originals',
                        [date(2026, 4, d) for d in (17, 18, 19)], root,
                        source_overrides={date(2026, 4, 19): nrt})
        subprocess.run([sys.executable, 'bin/trial_prism_rain_pipeline.py',
                        '--original-root', str(work/'originals'),
                        '--snapshot-manifest', str(work/'originals/snapshot.json'),
                        '--start', '2026-04-18', '--days', '1',
                        '--window-workers', '2', '--writer-workers', '2'], check=True)
        candidate = root/'forcing/work/prism-rain-pipeline'/job/'acceptance.json'
        accepted = json.loads(candidate.read_text())
        if accepted.get('status') != 'passed' or len(accepted.get('files', [])) != 1:
            raise ValueError('Boundary candidate not accepted')
        report.update(status='passed', candidate_report=str(candidate),
                      note='Candidate only; requires adjacent-window review before any publication')
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(work/'acceptance.json', report)


if __name__ == '__main__':
    main()
