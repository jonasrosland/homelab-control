#!/usr/bin/env python3
"""Trigger a Backrest backup plan (DEP-003 derived wiring)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backrest_client import trigger_plan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "role",
        choices=("predeploy", "manual"),
        help="Plan role from x-homelab.integrations.backrest.plans",
    )
    args = parser.parse_args()
    try:
        trigger_plan(role=args.role)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
