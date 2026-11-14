"""Isolated commissioning commands; no live activation or deletion commands."""
import argparse
import json
from pathlib import Path

from hydro_ops.web.globus_transfer import (
    cleanup_plan,
    poll_test,
    prepare_test,
    submit_test,
    transfer_client,
    validate_plan,
    verify_http,
)

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare-test', 'submit-test', 'status', 'verify-http', 'cleanup-plan'])
    parser.add_argument('--config', type=Path, default=ROOT/'config/web_globus.json')
    parser.add_argument('--plan', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare-test':
        print(prepare_test(ROOT, json.loads(args.config.read_text())))
        return
    if args.plan is None:
        parser.error('--plan is required')
    if args.action in ('verify-http', 'cleanup-plan'):
        result = (verify_http if args.action == 'verify-http' else cleanup_plan)(ROOT, args.plan)
    else:
        plan, _ = validate_plan(ROOT, args.plan)
        with transfer_client(plan['config']) as client:
            result = (submit_test if args.action == 'submit-test' else poll_test)(ROOT, args.plan, client)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- never leak SDK authentication details to cron logs
        # SDK exception text may contain authentication details; do not dump it to cron logs.
        raise SystemExit(f'{type(error).__name__}: command failed; check credential configuration, '
                         'plan integrity and collection permissions. No live activation was attempted.') from None
