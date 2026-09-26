"""Timezone from config/saturn.yml (America/New_York). Used by host scripts."""
from __future__ import annotations

import os
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

DEFAULT = "America/New_York"


def timezone(root: Path) -> str:
    path = root / "config" / "saturn.yml"
    if yaml is None or not path.is_file():
        return DEFAULT
    raw = yaml.safe_load(path.read_text()) or {}
    tz = raw.get("timezone")
    return str(tz) if tz else DEFAULT


def apply(root: Path) -> str:
    tz = timezone(root)
    os.environ["TZ"] = tz
    if hasattr(time, "tzset"):
        time.tzset()
    return tz
