#!/usr/bin/env python3
"""Run data-plane boot render scripts listed in config/boot-secrets.yml."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required", file=sys.stderr)
    raise SystemExit(1)

import homelab_paths as hp  # noqa: E402


def _load_cfg(data: Path) -> dict:
    path = data / "config" / "boot-secrets.yml"
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    return raw if isinstance(raw, dict) else {}


def render_scripts(cfg: dict) -> list[str]:
    scripts: set[str] = set()
    for key in ("render_scripts", "render_scripts_extra"):
        for script in cfg.get(key) or []:
            s = str(script).strip()
            if s:
                scripts.add(s)
    return sorted(scripts)


def warn_on_fail(cfg: dict) -> frozenset[str]:
    return frozenset(
        str(s).strip() for s in (cfg.get("warn_on_fail") or []) if str(s).strip()
    )


def main() -> int:
    data = hp.data_root()
    cfg = _load_cfg(data)
    warn = warn_on_fail(cfg)
    scripts = render_scripts(cfg)
    if not scripts:
        print("boot-secrets: no render_scripts in config/boot-secrets.yml", file=sys.stderr)
        return 0

    failed = 0
    for rel in scripts:
        path = data / rel
        if not path.is_file():
            print(f"skip missing {rel}", file=sys.stderr)
            continue
        proc = subprocess.run([sys.executable, str(path)], cwd=str(data))
        if proc.returncode == 0:
            print(f"ok {rel}")
            continue
        if rel in warn:
            print(f"warn: {rel} failed (optional boot render)", file=sys.stderr)
            continue
        print(f"FAIL {rel} exit={proc.returncode}", file=sys.stderr)
        failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
