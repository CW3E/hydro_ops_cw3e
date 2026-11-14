"""Plan or advance manifest-owned web publication; live writes are disabled by default."""
import argparse
import json
from pathlib import Path

from hydro_ops.web.globus_transfer import atomic_json, transfer_client
from hydro_ops.web.publication import advance, prepare, raster_inventory, validate

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'status', 'advance'))
    parser.add_argument('--publication', type=Path)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/web_globus.json')
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--enable-transfer', action='store_true')
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    if args.action == 'plan':
        if args.publication is None:
            parser.error('--publication required')
        inventory = raster_inventory(args.publication)
        path = prepare(ROOT, args.publication, inventory, json.loads(args.config.read_text()))
        atomic_json(path.with_name('inventory.json'), inventory)
        print(path)
        return
    if args.plan is None:
        parser.error('--plan required')
    plan, state = validate(ROOT, args.plan)
    if args.action == 'advance':
        # Both frozen and current configuration must explicitly enable exactly
        # this destination. Disabling the config stops further submissions.
        config = json.loads(args.config.read_text())
        if not args.enable_transfer or not config.get('production_enabled') or config != plan['config']:
            raise ValueError('Explicit enablement and matching configuration required')
        with transfer_client(config) as client:
            state = advance(ROOT, args.plan, client, enabled=True, retry_failed=args.retry_failed)
    print(json.dumps(state, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- SDK errors may disclose OAuth details
        raise SystemExit(f'{type(error).__name__}: publication stopped; inspect deployment phase, '
                         'configuration and accepted inventory. No downstream phase was advanced.') from None
