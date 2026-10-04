"""Derive Backrest API wiring from x-homelab (DEP-003). No site backrest.yml required."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

import homelab_paths as hp

_DEFAULT_STACK = "backrest"
_DEFAULT_TIMEOUT = 3600
_ENV_PAIR = re.compile(r"^([^=]+)=(.*)$")


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None or not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    return raw if isinstance(raw, dict) else {}


def backrest_stack_key() -> str:
    try:
        manifest = _load_yaml(hp.data_root() / "config" / "homelab.yml")
        key = str((manifest.get("integrations") or {}).get("backrest_stack") or "").strip()
        if key:
            return key
    except RuntimeError:
        pass
    return _DEFAULT_STACK


def _compose_path(stack_key: str) -> Path:
    root = hp.data_root()
    for name in ("docker-compose.yml", "compose.yml"):
        path = root / "stacks" / stack_key / name
        if path.is_file():
            return path
    raise FileNotFoundError(f"Missing compose for stack {stack_key!r}")


def load_backrest_compose(stack_key: str | None = None) -> tuple[str, dict[str, Any]]:
    key = stack_key or backrest_stack_key()
    path = _compose_path(key)
    compose = _load_yaml(path)
    if not compose:
        raise ValueError(f"Empty compose: {path}")
    return key, compose


def x_homelab_block(compose: dict[str, Any]) -> dict[str, Any]:
    block = compose.get("x-homelab")
    if not isinstance(block, dict):
        raise ValueError("x-homelab block missing")
    return block


def integration_block(compose: dict[str, Any]) -> dict[str, Any]:
    block = x_homelab_block(compose)
    integrations = block.get("integrations") or {}
    if not isinstance(integrations, dict):
        raise ValueError("integrations must be a mapping")
    br = integrations.get("backrest")
    if not isinstance(br, dict):
        raise ValueError("x-homelab.integrations.backrest required for Backrest stack")
    return br


def _service_env(services: dict[str, Any], service_name: str) -> dict[str, str]:
    svc = services.get(service_name) or {}
    if not isinstance(svc, dict):
        return {}
    out: dict[str, str] = {}
    env = svc.get("environment") or []
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    if isinstance(env, list):
        for item in env:
            if isinstance(item, str):
                m = _ENV_PAIR.match(item.strip())
                if m:
                    out[m.group(1).strip()] = m.group(2).strip()
    return out


def _expand_services_path(spec: str) -> str:
    s = spec.strip()
    svc_root = str(hp.services_root())
    svc_host = os.environ.get("HOMELAB_SERVICES_HOST", "").rstrip("/")
    if svc_host:
        s = s.replace("${HOMELAB_SERVICES_HOST:?}", svc_host)
        s = s.replace("${HOMELAB_SERVICES_HOST}", svc_host)
    if svc_host and s.startswith(svc_host):
        s = svc_root + s[len(svc_host) :]
    return s


def _publish_port(block: dict[str, Any], compose: dict[str, Any], service_name: str) -> int:
    for entry in block.get("publish") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("service") or "").strip() != service_name:
            continue
        port = entry.get("port")
        if port is not None:
            return int(port)
    services = compose.get("services") or {}
    svc = services.get(service_name) or {}
    ports = svc.get("ports") or [] if isinstance(svc, dict) else []
    for mapping in ports:
        if not isinstance(mapping, str):
            continue
        host_part = mapping.split(":")[0]
        if host_part.isdigit():
            return int(host_part)
    raise ValueError(f"Cannot resolve publish port for service {service_name!r}")


def config_json_host_path(
    compose: dict[str, Any],
    stack_key: str,
    integration: dict[str, Any],
) -> Path:
    cfg = integration.get("config") or {}
    service_name = str(cfg.get("service") or "backrest").strip()
    env_name = str(cfg.get("env") or "BACKREST_CONFIG").strip()
    services = compose.get("services") or {}
    env = _service_env(services, service_name)
    container_path = env.get(env_name) or "/config/config.json"
    container_dir = str(Path(container_path).parent)

    svc = services.get(service_name) or {}
    volumes = svc.get("volumes") or [] if isinstance(svc, dict) else []

    def mounts_file(container_file: str, mount_point: str) -> bool:
        cf = container_file.rstrip("/")
        mp = mount_point.strip().rstrip("/")
        return cf == mp or cf.startswith(mp + "/")

    for vol in volumes:
        if not isinstance(vol, str) or ":" not in vol:
            continue
        host_spec, ctr_spec = vol.rsplit(":", 1)
        if not mounts_file(container_path, ctr_spec):
            continue
        host_path = _expand_services_path(host_spec)
        if container_path.endswith("config.json"):
            if host_path.rstrip("/").endswith("/config"):
                return Path(host_path.rstrip("/")) / "config.json"
            return Path(host_path)
        return Path(host_path) / Path(container_path).name

    return hp.services_root() / stack_key / "config" / "config.json"


def api_urls(port: int) -> tuple[str, list[str]]:
    if os.environ.get("HOMELAB_CONTROL_CONTAINER") == "1":
        primary = f"http://host.docker.internal:{port}"
        fallbacks = [f"http://127.0.0.1:{port}"]
    else:
        primary = f"http://127.0.0.1:{port}"
        fallbacks = []
    urls = [primary] + [u for u in fallbacks if u != primary]
    return primary, urls


def derived_api_config(stack_key: str | None = None) -> dict[str, Any]:
    key, compose = load_backrest_compose(stack_key)
    block = x_homelab_block(compose)
    integration = integration_block(compose)
    cfg = integration.get("config") or {}
    service_name = str(cfg.get("service") or "backrest").strip()
    port = _publish_port(block, compose, service_name)
    primary, urls = api_urls(port)
    plans = integration.get("plans") or {}
    manifest = _load_yaml(hp.data_root() / "config" / "homelab.yml")
    timeout = int(
        (manifest.get("integrations") or {}).get("backrest_timeout_seconds") or _DEFAULT_TIMEOUT
    )
    return {
        "url": primary,
        "urls": urls,
        "timeout_seconds": timeout,
        "repo_id": str(integration.get("repo_id") or "gdrive-homelab"),
        "predeploy_plan": str(plans.get("predeploy") or "homelab-predeploy"),
        "manual_plan": str(plans.get("manual") or "homelab-manual"),
        "stack_key": key,
    }


def config_json_path(stack_key: str | None = None) -> Path:
    key, compose = load_backrest_compose(stack_key)
    integration = integration_block(compose)
    path = config_json_host_path(compose, key, integration)
    return hp.host_path_to_runtime(path)
