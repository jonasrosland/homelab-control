#!/usr/bin/env python3
"""Resolve data-plane vs control-plane roots (split-repo or monorepo)."""
from __future__ import annotations

import os
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

CONTROL_ROOT = Path(__file__).resolve().parents[1]


def _load_manifest(data: Path) -> dict:
    if yaml is None:
        return {}
    for name in ("homelab.yml", "homelab.yaml"):
        path = data / "config" / name
        if path.is_file():
            return yaml.safe_load(path.read_text()) or {}
    return {}


def data_root() -> Path:
    """Git checkout containing config/deploy.yml and stacks/."""
    env = os.environ.get("HOMELAB_DATA_ROOT")
    if env:
        root = Path(env).resolve()
    elif (CONTROL_ROOT / "config" / "deploy.yml").is_file():
        root = CONTROL_ROOT
    else:
        raise RuntimeError(
            "Set HOMELAB_DATA_ROOT to the data-plane git checkout "
            "(must contain config/deploy.yml)."
        )
    if not (root / "config" / "deploy.yml").is_file():
        raise RuntimeError(f"Missing config/deploy.yml under data root {root}")
    return root


def services_root() -> Path:
    data = data_root()
    manifest = _load_manifest(data)
    paths = manifest.get("paths") or {}
    if paths.get("services_root"):
        return Path(str(paths["services_root"]))
    env = os.environ.get("HOMELAB_SERVICES_ROOT")
    if env:
        return Path(env)
    return Path("/var/lib/homelab/services")


def generated_dir() -> Path:
    data = data_root()
    manifest = _load_manifest(data)
    rel = (manifest.get("paths") or {}).get("generated") or ".generated"
    path = Path(str(rel))
    if path.is_absolute():
        return path
    return data / path


def resolve_data(p: str | Path) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return data_root() / path


def control_root() -> Path:
    """Checkout of homelab-control (this repo when deployed from control)."""
    env = os.environ.get("HOMELAB_CONTROL_ROOT")
    if env:
        return Path(env).resolve()
    try:
        data = data_root()
        manifest = _load_manifest(data)
        cr = (manifest.get("paths") or {}).get("control_root")
        if cr:
            path = Path(str(cr))
            if path.is_dir():
                return path.resolve()
    except RuntimeError:
        pass
    return CONTROL_ROOT


def control_script(name: str) -> Path:
    return control_root() / "scripts" / name


def data_script(name: str) -> Path:
    return data_root() / "scripts" / name


def notify_label(cfg: dict | None = None) -> str:
    """Short label for Telegram/log prefixes (no trailing colon)."""
    env = os.environ.get("HOMELAB_NOTIFY_LABEL")
    if env:
        return env.strip()
    if cfg:
        label = (cfg.get("notify") or {}).get("host_label")
        if label:
            return str(label).strip()
    try:
        manifest = _load_manifest(data_root())
    except RuntimeError:
        manifest = {}
    return str((manifest.get("notify") or {}).get("host_label") or "deploy")


def notify_prefix(cfg: dict | None = None) -> str:
    return f"{notify_label(cfg)}: "
