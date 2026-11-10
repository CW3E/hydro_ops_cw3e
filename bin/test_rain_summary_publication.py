#!/usr/bin/env python3
"""October 1986 isolated publication, subsetting, interruption and idempotency test."""
import json
import os
from datetime import date, timedelta
from pathlib import Path

from benchmark_rain_summary_repair import compare
from refresh_rain_repair_summaries import refresh

from hydro_ops.forcing.rain_repair_writer import fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json
from hydro_ops.forcing.temporal_summary import summary_path


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT'])
    benchmark = root/'forcing/work/rain-summary-benchmark/job-5116870'
    gate = json.loads((benchmark/'acceptance.json').read_text())
    if not (gate.get('status') == 'passed' and gate.get('monthly_all_variables_reference_equal')):
        raise ValueError('Benchmark gate failed')
    job = os.environ['SLURM_JOB_ID']
    result = root/'forcing/work/rain-summary-publication-test'/f'job-{job}'
    result.mkdir(parents=True, exist_ok=False)
    outputs = result/'outputs'
    start = date(1986, 10, 1)
    days = [start+timedelta(days=i) for i in range(31)]
    for day, monthly in [*((d, False) for d in days), (start, True)]:
        resolution = 'monthly' if monthly else 'daily'
        source = summary_path(benchmark/f'old-{resolution}', day, monthly=monthly)
        target = summary_path(outputs/'conus/retro'/resolution, day, monthly=monthly)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, target)
    report = {'status': 'running', 'production_modified': False, 'job_id': job}
    try:
        try:
            refresh(root, start, result/'work', outputs, inject_after_replace=True)
        except RuntimeError as error:
            if str(error) != 'Intentional post-replace test interruption':
                raise
            report['interrupted_after_replace'] = True
        else:
            raise ValueError('Failure injection was not exercised')
        report['resumed'] = refresh(root, start, result/'work', outputs)
        if report['resumed']['daily'][0]['publication']['status'] != 'recovered':
            raise ValueError('Post-replacement receipt was not recovered')
        # Full-grid oracle comparisons reuse the independent full-regeneration benchmark.
        for day in (days[0], days[-1]):
            compare(summary_path(outputs/'conus/retro/daily', day),
                    summary_path(benchmark/'reference-daily', day))
        compare(summary_path(outputs/'conus/retro/monthly', start, monthly=True),
                summary_path(benchmark/'reference-monthly', start, monthly=True))
        paths = list(outputs.glob('*/retro/daily/*/*/*.daily'))
        paths += list(outputs.glob('*/retro/monthly/*/*.monthly'))
        before = {str(p): fingerprint(p) for p in paths}
        report['retry'] = refresh(root, start, result/'work', outputs)
        if any(fingerprint(Path(p)) != v for p, v in before.items()):
            raise ValueError('Successful retry rewrote a published file')
        if len(paths) != 32*5:
            raise ValueError(f'Incomplete five-domain publication: {len(paths)}')
        report.update(status='passed', all_variables_reference_equal=True,
                      idempotent_retry_verified=True, published_files=len(paths))
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        atomic_json(result/'acceptance.json', report)


if __name__ == '__main__':
    main()
