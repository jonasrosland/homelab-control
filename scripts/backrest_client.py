#!/usr/bin/env python3
"""Backrest HTTP API (DEP-003 derived config; legacy config/backrest.yml fallback)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

_DATA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backrest_integration as bi  # noqa: E402
import homelab_paths as hp  # noqa: E402


def _load_legacy_backrest_yaml() -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML required")
    root = Path(os.environ.get("HOMELAB_DATA_ROOT", _DATA))
    path = root / "config" / "backrest.yml"
    if not path.is_file():
        raise FileNotFoundError(path)
    return yaml.safe_load(path.read_text()) or {}


def _legacy_api_config() -> dict:
    br = _load_legacy_backrest_yaml()
    api = br.get("api") or {}
    plans = api.get("plans") or {}
    primary = str(api.get("url") or "http://host.docker.internal:9898").rstrip("/")
    fallbacks = [str(u).rstrip("/") for u in (api.get("fallback_urls") or []) if u]
    urls = [primary] + [u for u in fallbacks if u != primary]
    return {
        "url": primary,
        "urls": urls,
        "timeout_seconds": int(api.get("timeout_seconds") or 3600),
        "repo_id": str(br.get("repo_id") or api.get("repo_id") or "gdrive-homelab"),
        "predeploy_plan": str(plans.get("predeploy") or "homelab-predeploy"),
        "manual_plan": str(plans.get("manual") or "homelab-manual"),
    }


def api_config() -> dict:
    try:
        return bi.derived_api_config()
    except (FileNotFoundError, ValueError, RuntimeError):
        return _legacy_api_config()


def config_json_path() -> Path:
    try:
        return bi.config_json_path()
    except (FileNotFoundError, ValueError, RuntimeError):
        br = _load_legacy_backrest_yaml()
        return hp.host_path_to_runtime(
            Path(br.get("config_path") or "/var/lib/homelab/services/backrest/config/config.json")
        )


def _post(path: str, body: dict, *, timeout: int) -> bytes:
    cfg = api_config()
    data = json.dumps(body).encode()
    last_err: Exception | None = None
    for base in cfg.get("urls") or [cfg["url"]]:
        url = f"{base}{path}"
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as exc:
            last_err = exc
    raise last_err or urllib.error.URLError("no Backrest API URL configured")


def get_config(*, timeout: int = 30) -> dict:
    raw = _post("/v1.Backrest/GetConfig", {}, timeout=timeout)
    data = json.loads(raw.decode())
    if not isinstance(data, dict):
        raise RuntimeError("Backrest GetConfig returned non-object JSON")
    return data


def set_config(body: dict, *, timeout: int = 30) -> dict:
    raw = _post("/v1.Backrest/SetConfig", body, timeout=timeout)
    data = json.loads(raw.decode())
    if not isinstance(data, dict):
        raise RuntimeError("Backrest SetConfig returned non-object JSON")
    return data


def trigger_plan(plan_id: str | None = None, *, role: str = "predeploy") -> None:
    cfg = api_config()
    if not plan_id:
        plan_id = cfg["manual_plan"] if role == "manual" else cfg["predeploy_plan"]
    timeout = cfg["timeout_seconds"]
    print(f"+ Backrest backup plan {plan_id!r} ({cfg['url']})", flush=True)
    try:
        _post("/v1.Backrest/Backup", {"value": plan_id}, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:2000]
        raise RuntimeError(f"Backrest backup failed HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Backrest unreachable at {cfg['url']}: {exc}") from exc


def list_snapshots(repo_id: str | None = None) -> list[dict]:
    cfg = api_config()
    rid = repo_id or cfg["repo_id"]
    raw = _post(
        "/v1.Backrest/ListSnapshots",
        {"repoId": rid},
        timeout=min(cfg["timeout_seconds"], 120),
    )
    data = json.loads(raw.decode())
    snaps = data.get("snapshots") or data.get("Snapshots") or []
    if isinstance(snaps, list):
        return [s for s in snaps if isinstance(s, dict)]
    return []
