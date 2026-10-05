#!/usr/bin/env python3
"""Backrest HTTP API (DEP-003 derived config from x-homelab)."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backrest_integration as bi  # noqa: E402


def api_config() -> dict:
    return bi.derived_api_config()


def config_json_path() -> Path:
    return bi.config_json_path()


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
