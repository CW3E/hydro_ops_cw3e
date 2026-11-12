"""Audit complete tile-array publication against the accepted extraction plan."""
import argparse
import json
from pathlib import Path

from benchmark_nwm_climatology import digest, identity

from hydro_ops.wrf_hydro.production import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history-job', required=True, type=int)
    parser.add_argument('--array-job', required=True, type=int)
    parser.add_argument('--domain', choices=('cnrfc', 'ucrb'), default='cnrfc')
    args = parser.parse_args()
    domain = args.domain
    root = Path(__file__).resolve().parents[1]
    history_root = root / f'nwm/status/{domain}/climatology/history-stream-{args.history_job}'
    target = root / f'nwm/status/{domain}/climatology/tiles-{args.array_job}'
    report = {'status': 'running', 'domain': domain, 'history_job': args.history_job, 'array_job': args.array_job}
    try:
        history = json.loads((history_root / 'status.json').read_text())
        plan = json.loads((history_root / 'plan.json').read_text())
        if history['status'] != 'passed' or len(history['artifacts']) != len(plan['tiles']):
            raise ValueError('History acceptance/plan mismatch')
        if history.get('source_model_campaign') != plan.get('source_model_campaign'):
            raise ValueError('History model campaign mismatch')
        if history.get('domain', 'cnrfc') != domain or plan.get('domain', 'cnrfc') != domain:
            raise ValueError('History/plan domain mismatch')
        if digest(root / f'nwm/static/domains/{domain}/masks/{domain}_masks.nc') != plan['mask_sha256']:
            raise ValueError('Domain mask changed')
        for pin in [*plan.get('sources', []), *plan.get('model_acceptance_pins', [])]:
            if identity(root/pin['path']) != pin['identity']:
                raise ValueError(f'Climatology source/evidence changed: {pin["path"]}')
        products = []
        for index, (tile, source) in enumerate(zip(plan['tiles'], history['artifacts'], strict=True)):
            task = json.loads((target / f'task-{index:03d}.json').read_text())
            if (task['status'] != 'passed' or task['history_job'] != args.history_job
                    or task.get('domain', 'cnrfc') != domain
                    or task['tile'] != tile['name'] or Path(source['path']).stem != tile['name']):
                raise ValueError(f'Tile acceptance mismatch: {index}')
            product = task['artifact']
            if digest(Path(product['path'])) != product['sha256']:
                raise ValueError(f'Published tile checksum mismatch: {index}')
            products.append({'name': tile['name'], 'window': tile['window'], **product})
        report.update(status='passed', tiles=products, tile_count=len(products),
                      active_cells=plan['active_cells'], mask_sha256=plan['mask_sha256'],
                      source_model_campaign=plan.get('source_model_campaign', 'legacy_unspecified'),
                      output_bytes=sum(p['bytes'] for p in products))
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        write_json(target / 'acceptance.json', report)


if __name__ == '__main__':
    main()
