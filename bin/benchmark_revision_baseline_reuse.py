"""Isolated full replay benchmark against an accepted cold-build hourly file."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-workspace', required=True, type=Path)
    args = parser.parse_args()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    reference = args.reference_workspace.resolve()
    base = root/'forcing/work/isolated-revision-replay'
    if reference.parent != base or json.loads((reference/'acceptance.json').read_text())['status'] != 'passed':
        raise ValueError('Require an accepted isolated reference replay')
    context = json.loads((reference/'context.json').read_text())
    day, case = context['day'], context['case']
    stream = 'nrt' if case == 'daily' else 'retro'
    workspace = base/os.environ['SLURM_JOB_ID']
    command = [sys.executable, 'bin/test_isolated_revision_replay.py', '--case', case,
               '--day', day, '--baseline-reuse-root', str(reference/'baseline'),
               '--baseline-reuse-root', str(root/'forcing/outputs/conus/baseline/hourly')]
    started = time.monotonic()
    subprocess.run(command, check=True)
    seconds = time.monotonic()-started
    subprocess.run(command, check=True)  # Existing exact-input no-op gate.
    relative = Path('outputs/conus')/stream/'hourly'/day[:4]/day[5:7]/(day.replace('-', '')+'.LDASIN_DOMAIN1')
    compared = []
    with Dataset(reference/relative) as old, Dataset(workspace/relative) as new:
        if set(old.variables) != set(new.variables):
            raise ValueError('Variable schema changed')
        for name, variable in old.variables.items():
            other = new.variables[name]
            if variable.shape != other.shape or variable.dimensions != other.dimensions:
                raise ValueError(f'Variable dimensions changed: {name}')
            # Read one leading record at a time, not whole CONUS histories.
            for i in range(variable.shape[0] if variable.ndim else 1):
                key = i if variable.ndim else ()
                a, b = variable[key], other[key]
                if not np.array_equal(np.ma.getmaskarray(a), np.ma.getmaskarray(b)):
                    raise ValueError(f'Mask changed: {name}/{i}')
                valid = ~np.ma.getmaskarray(a)
                if not np.array_equal(np.asarray(a)[valid], np.asarray(b)[valid],
                                      equal_nan=variable.dtype.kind in 'fc'):
                    raise ValueError(f'Values changed: {name}/{i}')
            compared.append(name)
    stages = json.loads((workspace/'stages.json').read_text())['stages']
    imports = {k: v['acceptance'].get('baseline_cache') for k, v in stages.items()
               if k.startswith('baseline/')}
    result = {'status': 'passed', 'candidate_seconds': seconds, 'baseline_imports': imports,
              'exact_hourly_variables': compared, 'reference': str(reference),
              'production_modified': False, 'routine_enabled': False}
    atomic_json(workspace/'baseline-reuse-benchmark.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
