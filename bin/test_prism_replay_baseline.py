"""Rebuild missing March baselines in isolation, testing interruption and no-op recovery."""
import json
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path

from hydro_ops.forcing import nrt_cycle
from hydro_ops.forcing.prism_replay_execution import run_stage
from hydro_ops.forcing.rain_repair_writer import fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    job = os.environ['SLURM_JOB_ID']
    work = root/f'forcing/work/operational-prism-replay/{job}'
    work.mkdir(parents=True, exist_ok=False)
    scratch = Path(f'/scratch/{os.environ["SLURM_JOB_USER"]}/job_{job}/replay-baseline')
    report = {'status': 'running', 'production_modified': False, 'live_queue_modified': False,
              'days': [], 'scope': 'missing-baseline execution/recovery gate, not full replay publication'}
    began = time.monotonic()
    def pin(path):
        return list(fingerprint(path)) if path.exists() else None
    try:
        engine = nrt_cycle.RecentNrt(root, scratch, datetime.now(UTC),
                                    baseline_root=work/'baseline', output_root=work/'nrt')
        for day in (date(2026, 3, 11), date(2026, 3, 12), date(2026, 3, 13)):
            relative = day.strftime('%Y/%m/%Y%m%d.LDASIN_DOMAIN1')
            live = root/'forcing/outputs/conus/baseline/hourly'/relative
            before = pin(live)
            selections, inputs = nrt_cycle.baseline_plan(day, engine.layout, engine.config)
            if {s.product for s in selections} != {'nldas2'}:
                raise ValueError('Expected historical NLDAS baselines; no fallback acquisition in this test')
            signature = nrt_cycle.fingerprint({'inputs': inputs, 'assets': engine.assets,
                                               'policy': 'isolated-replay-baseline-v1', 'config': engine.config})
            built_pin = None
            def build(interrupt=False, day=day, inputs=inputs):
                nonlocal built_pin
                target, receipt = engine.baseline_day(day)
                if receipt['status'] != 'passed' or not receipt['complete_day']:
                    raise ValueError('Baseline worker did not accept a complete day')
                _, after_inputs = nrt_cycle.baseline_plan(day, engine.layout, engine.config)
                if nrt_cycle.fingerprint(after_inputs) != nrt_cycle.fingerprint(inputs):
                    raise ValueError('Native inputs changed during rebuild')
                if built_pin is not None and list(fingerprint(target)) != built_pin:
                    raise ValueError('Retry rewrote accepted baseline')
                built_pin = list(fingerprint(target))
                if interrupt:
                    raise RuntimeError('injected interruption after accepted baseline write')
                return {'status': 'passed', 'artifacts': [target,
                    target.with_name(target.name+'.manifest.json'),
                    target.with_name(target.name+'.nrt-receipt.json')],
                    'acceptance': {'day': str(day), 'complete_day': True, 'receipt': receipt}}
            try:
                run_stage(work, f'baseline/{day}', signature, lambda: build(interrupt=True))
            except RuntimeError as exc:
                if str(exc) != 'injected interruption after accepted baseline write':
                    raise
            else:
                raise ValueError('Expected injected interruption')
            resumed = run_stage(work, f'baseline/{day}', signature, build)
            noop = run_stage(work, f'baseline/{day}', signature, build)
            if resumed['reused'] or not noop['reused'] or pin(live) != before:
                raise ValueError('Replay recovery/no-op/isolation check failed')
            report['days'].append({'day': str(day), 'live_baseline_existed': before is not None,
                                   'interruption_recovered': True, 'no_op': True,
                                   'output': str(work/'baseline'/relative)})
            atomic_json(work/'acceptance.json', report)
        report['status'] = 'passed'
    except BaseException as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        report['elapsed_seconds'] = time.monotonic()-began
        atomic_json(work/'acceptance.json', report)
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
