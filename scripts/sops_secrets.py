#!/usr/bin/env python3
"""Decrypt SOPS-encrypted config/secrets/* into /run/homelab (tmpfs).

Uses scripts/sops-run.sh (digest-pinned container). Plaintext never lands under
the git tree. Callers should prefer load_yaml() / decrypt_to() over reading
config/secrets directly.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(1)

import homelab_paths as hp

ROOT = hp.data_root()
SOPS_CFG = ROOT / "config" / "sops.yml"
SOPS_RUN = hp.control_script("sops-run.sh")
SECRETS_DIR = ROOT / "config" / "secrets"


def _cfg() -> dict:
    if not SOPS_CFG.is_file():
        return {}
    return yaml.safe_load(SOPS_CFG.read_text()) or {}


def runtime_dir() -> Path:
    override = os.environ.get("HOMELAB_RUNTIME_DIR")
    if override:
        return Path(override)
    return Path(str(_cfg().get("runtime_dir") or "/run/homelab"))


def ensure_runtime() -> Path:
    path = runtime_dir()
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def is_sops_encrypted(path: Path) -> bool:
    """Heuristic: SOPS YAML/JSON embeds a top-level sops: key; binary has sops metadata."""
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return False
    if b"sops:" in head or b'"sops":' in head:
        return True
    # Encrypted binary / dotenv often still contain "sops" in the MAC block when text.
    text = head.decode("utf-8", errors="ignore")
    return "ENC[AES256_GCM" in text or "sops_version" in text


def secrets_path(name: str) -> Path:
    """Path to encrypted (or legacy plaintext) secret file in the repo."""
    return SECRETS_DIR / name


def decrypt_to(name: str, dest_name: str | None = None) -> Path:
    """Decrypt config/secrets/<name> to runtime_dir/<dest_name or name>. Return path.

    If the source is not SOPS-encrypted (legacy plaintext during migration), copy it.
    """
    src = secrets_path(name)
    if not src.is_file():
        raise FileNotFoundError(f"Missing secret source: {src}")

    out_dir = ensure_runtime()
    dest = out_dir / (dest_name or name)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not is_sops_encrypted(src):
        data = src.read_bytes()
        dest.write_bytes(data)
        dest.chmod(0o600)
        return dest

    if not SOPS_RUN.is_file():
        raise RuntimeError(f"Missing {SOPS_RUN}")

    # Mount runtime dir as /out; source is under /work (repo).
    rel = src.relative_to(ROOT).as_posix()
    env = os.environ.copy()
    env["SOPS_OUT_DIR"] = str(out_dir)
    # Decrypt to stdout inside container, write via host redirect... better: -d to /out/
    proc = subprocess.run(
        [str(SOPS_RUN), "-d", f"/work/{rel}"],
        capture_output=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"").decode(errors="replace").strip()
        raise RuntimeError(f"sops decrypt failed for {name}: {err}")
    dest.write_bytes(proc.stdout)
    dest.chmod(0o600)
    return dest


def load_yaml(name: str) -> dict:
    """Decrypt (if needed) and parse YAML secret named under config/secrets/."""
    path = decrypt_to(name)
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Expected mapping in {name}, got {type(raw).__name__}")
    return raw


def load_text(name: str) -> str:
    return decrypt_to(name).read_text()


def write_runtime(name: str, content: str | bytes, mode: int = 0o600) -> Path:
    """Write a rendered artifact (e.g. litellm.env) into tmpfs."""
    out = ensure_runtime() / name
    out.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        out.write_text(content if content.endswith("\n") else content + "\n")
    else:
        out.write_bytes(content)
    out.chmod(mode)
    return out
