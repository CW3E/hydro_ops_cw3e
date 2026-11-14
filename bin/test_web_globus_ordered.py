"""Commission ordered transfers ONLY in a unique isolated test namespace."""
import argparse
import json
from pathlib import Path

from hydro_ops.web.globus_transfer import transfer_client
from hydro_ops.web.ordered_test import advance, prepare, validate

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'advance'))
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    if args.action == 'prepare':
        print(prepare(ROOT, json.loads((ROOT / 'config/web_globus.json').read_text())))
        return
    if args.plan is None:
        parser.error('--plan required')
    plan, _ = validate(ROOT, args.plan)
    with transfer_client(plan['config']) as client:
        result = advance(ROOT, args.plan, client, retry_failed=args.retry_failed)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- suppress SDK credential-bearing error bodies
        raise SystemExit(f'{type(error).__name__}: isolated test stopped; inspect local phase state. '
                         'Live publication was not touched.') from None
