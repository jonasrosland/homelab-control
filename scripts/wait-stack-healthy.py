#!/usr/bin/env python3
"""Wait until a compose stack's services are Docker-healthy after deploy."""
from __future__ import annotations

import argparse
import sys

from docker_health import load_deploy_health, wait_stack


def main() -> int:
    default_t, default_i = load_deploy_health()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", required=True, help="Stack name (services/ dir / compose project)")
    parser.add_argument("--timeout", type=float, default=default_t)
    parser.add_argument("--interval", type=float, default=default_i)
    args = parser.parse_args()
    try:
        wait_stack(args.stack, timeout_s=args.timeout, interval_s=args.interval)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
