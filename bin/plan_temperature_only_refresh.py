"""Freeze numerically mismatched windows and affected calendar-day halves."""
import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.rain_repair_writer import fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan-plan', type=Path, required=True)
    parser.add_argument('--array-job', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.array_job.isdecimal() or args.output.exists():
        raise ValueError('Numeric array and new output required')
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    plan = json.loads(args.scan_plan.read_text())
    streams = {s: {'windows': [], 'calendar_days': {}} for s in ('retro', 'nrt')}
    for i, stamp in enumerate(plan['days']):
        log = root/f'forcing/logs/temperature-reconciliation-{args.array_job}_{i}.out'
        emitted = [json.loads(line) for line in log.read_text().splitlines() if line.startswith('{')]
        report_path = root/f'forcing/work/temperature-reconciliation/{emitted[-1]["job"]}/report.json'
        report = json.loads(report_path.read_text())
        if report['status'] != 'diagnosed' or report['day'] != stamp:
            raise ValueError(f'Incomplete diagnostic: {stamp}')
        for stream, item in report['streams'].items():
            if item['status'] != 'diagnosed' or not item['active']['cells_above_0_001k']:
                continue
            if any(list(fingerprint(Path(p))) != pin for p, pin in item['forcing_pins'].items()):
                raise ValueError(f'Forcing superseded: {stream}/{stamp}')
            if any(source_signature(Path(pin['path'])) != pin for pin in report['prism_sources'].values()):
                raise ValueError(f'PRISM superseded: {stamp}')
            streams[stream]['windows'].append({'day': stamp, 'diagnostic': str(report_path),
                'statistics': item['active'], 'prism_sources': report['prism_sources']})
            day = date.fromisoformat(stamp)
            for d, hours in ((day-timedelta(days=1), list(range(12,24))), (day, list(range(12)))):
                record = streams[stream]['calendar_days'].setdefault(str(d), {'hours': [], 'windows': []})
                record['hours'] = sorted(set(record['hours']+hours))
                record['windows'].append(stamp)
    result = {'status': 'planned_not_published', 'diagnostic_array': args.array_job,
              'streams': streams,
              'changed_variables': ['T2D','Q2D','LWDOWN'],
              'preserve': 'RAINRATE and all other datasets byte-for-byte where chunked',
              'publication_gate': 'Candidate audit, downstream summaries/subsets, model input coordination',
              'production_modified': False, 'queue_modified': False}
    atomic_json(args.output, result)
    print(json.dumps({s: {'windows': len(v['windows']), 'calendar_days': len(v['calendar_days'])}
                      for s,v in streams.items()}))


if __name__ == '__main__':
    main()
