"""Accept all disjoint calendar candidates before any campaign publication."""
import argparse
import json
import os
from pathlib import Path

from hydro_ops.forcing.prism_revision_queue import source_signature
from hydro_ops.forcing.rain_repair_writer import digest, fingerprint
from hydro_ops.forcing.repair_transaction import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign',type=Path,required=True)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    root = Path(os.environ['HYDRO_OPS_PROJECT_ROOT']).resolve()
    if not campaign.is_relative_to(root/'forcing/work/temperature-refresh'):
        raise ValueError('Dedicated campaign required')
    plan = json.loads((campaign/'plan.json').read_text())
    rows = []
    for i,batch in enumerate(plan['batches']):
        report = json.loads((campaign/f'batches/{i:03d}.json').read_text())
        if report['status'] != 'passed_candidate_only' or len(report['files']) != len(batch['files']):
            raise ValueError('Incomplete candidate batch')
        rows.extend(report['files'])
    expected = {(r['stream'],r['day']):r for r in plan['files']}
    if len(rows) != len(expected) or {(r['stream'],r['day']) for r in rows} != set(expected):
        raise ValueError('Duplicate or missing calendar writer')
    for row in rows:
        want = expected[(row['stream'],row['day'])]
        if any(row[k] != want[k] for k in ('windows','changed_hours')):
            raise ValueError('Candidate scope differs')
        path = Path(row['path']).resolve()
        if not path.is_relative_to(campaign/'candidates') or digest(path) != row['sha256']:
            raise ValueError('Candidate integrity failed')
    for stamp in {s for row in rows for s in row['windows']}:
        receipt = json.loads((campaign/'windows'/f'{stamp}.json').read_text())
        if receipt['status'] != 'passed' or list(fingerprint(campaign/'windows'/f'{stamp}.nc')) != receipt['identity']:
            raise ValueError('Shared temperature window changed')
        if any(list(fingerprint(Path(p))) != pin for p,pin in receipt['input_pins'].items()):
            raise ValueError('Shared temperature-window input changed')
    for p,pin in plan['production_pins'].items():
        if list(fingerprint(Path(p))) != pin:
            raise ValueError('Production changed during candidate phase')
    for pin in plan['prism_sources'].values():
        if source_signature(Path(pin['path'])) != pin:
            raise ValueError('PRISM superseded')
    report = {'status':'passed_candidate_only','version':2,'window':'batch',
              'changed_variables':['T2D','Q2D','LWDOWN'],'files':rows,
              'prism_sources':plan['prism_sources'],'production_pins':plan['production_pins'],
              'plan_sha256':digest(campaign/'plan.json'),'production_modified':False,'queue_modified':False}
    atomic_json(campaign/'acceptance.json',report)
    print(json.dumps({'status':report['status'],'files':len(rows)}))


if __name__ == '__main__':
    main()
