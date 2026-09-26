"""Shared Docker health helpers for deploy and upgrade playbooks."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

import homelab_paths as hp

ROOT = hp.data_root()
SERVICES_ROOT = hp.services_root()


def load_deploy_health() -> tuple[float, float]:
    path = ROOT / "config" / "deploy.yml"
    timeout, interval = 120.0, 2.0
    if path.is_file() and yaml is not None:
        cfg = yaml.safe_load(path.read_text()) or {}
        health = cfg.get("health") or {}
        timeout = float(health.get("timeout_seconds") or timeout)
        interval = float(health.get("poll_seconds") or interval)
    return timeout, interval


def compose_file_for_stack(stack: str) -> Path:
    # Prefer the repo that contains this script (live git after pull), then services/.
    repo = ROOT / "stacks" / stack
    services = SERVICES_ROOT / stack
    for base in (repo, services):
        for name in ("docker-compose.yml", "compose.yml"):
            path = base / name
            if path.is_file():
                return path
    raise FileNotFoundError(f"No compose file for stack {stack}")


def load_compose_yaml(compose_file: Path) -> dict[str, Any]:
    if yaml is None:
        raise SystemExit("PyYAML required: pip install pyyaml")
    return yaml.safe_load(compose_file.read_text()) or {}


def compose_project_and_services(compose_file: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    """Return (project_name, services) from `docker compose config`."""
    raw = subprocess.check_output(
        ["docker", "compose", "-f", str(compose_file), "config", "--format", "json"],
        text=True,
        cwd=str(compose_file.parent),
    )
    cfg = json.loads(raw)
    project = str(cfg.get("name") or compose_file.parent.name)
    services = cfg.get("services") or {}
    if not isinstance(services, dict):
        raise RuntimeError(f"Unexpected compose services in {compose_file}")
    return project, services


def skip_wait_services(compose_file: Path) -> set[str]:
    """Services that must not block deploy (Compose drops x-* from config JSON)."""
    raw = load_compose_yaml(compose_file)
    services = raw.get("services") or {}
    skip: set[str] = set()
    if not isinstance(services, dict):
        return skip
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        if svc.get("x-homelab.health_wait") is False:
            skip.add(name)
            continue
        x = svc.get("x-homelab")
        if isinstance(x, dict) and x.get("health_wait") is False:
            skip.add(name)
            continue
        labels = svc.get("labels") or []
        label_map: dict[str, str] = {}
        if isinstance(labels, dict):
            label_map = {str(k): str(v) for k, v in labels.items()}
        elif isinstance(labels, list):
            for item in labels:
                if isinstance(item, str) and "=" in item:
                    k, v = item.split("=", 1)
                    label_map[k.strip()] = v.strip()
        if label_map.get("homelab.health_wait", "").lower() in ("false", "0", "no"):
            skip.add(name)
    return skip


def service_has_healthcheck(svc_cfg: dict[str, Any]) -> bool:
    return bool(svc_cfg.get("healthcheck"))


def containers_for_project(project: str) -> list[dict[str, str]]:
    """Label-filtered containers for a project (may include orphans). Prefer compose_ps."""
    out = subprocess.check_output(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            '{{.Names}}\t{{.Label "com.docker.compose.service"}}',
        ],
        text=True,
    ).strip()
    rows: list[dict[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        rows.append({"name": parts[0], "service": parts[1]})
    return rows


def containers_for_compose(compose_file: Path) -> list[dict[str, str]]:
    """Containers owned by this compose file — excludes label orphans from old recreates."""
    raw = subprocess.check_output(
        [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "ps",
            "-a",
            "--format",
            "json",
        ],
        text=True,
        cwd=str(compose_file.parent),
    ).strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        items: list[Any] = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        items = [json.loads(line) for line in raw.splitlines() if line.strip()]
    rows: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or item.get("Names") or "")
        service = str(item.get("Service") or "")
        if name and service:
            rows.append({"name": name, "service": service})
    return rows


def inspect_state(container: str) -> dict[str, Any]:
    raw = subprocess.check_output(
        ["docker", "inspect", "--format", "{{json .State}}", container],
        text=True,
    )
    return json.loads(raw)


def wait_container(
    name: str,
    *,
    timeout_s: float = 120,
    interval_s: float = 2,
    require_healthcheck: bool = False,
) -> None:
    """Wait until container is running; if Health exists (or required), until healthy."""
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        state = inspect_state(name)
        status = state.get("Status")
        health_block = state.get("Health")
        health = (health_block or {}).get("Status") if health_block else None
        last = f"{status} health={health}"
        if status == "running":
            if health_block is None:
                if require_healthcheck:
                    last = f"{status} health=missing"
                else:
                    return
            elif health == "healthy":
                return
        time.sleep(interval_s)
    raise RuntimeError(f"{name} not healthy ({last})")


def wait_stack(
    stack: str,
    *,
    timeout_s: float | None = None,
    interval_s: float | None = None,
    compose_file: Path | None = None,
) -> None:
    """Wait for all non-skipped services in a compose stack to be healthy."""
    default_t, default_i = load_deploy_health()
    timeout_s = default_t if timeout_s is None else timeout_s
    interval_s = default_i if interval_s is None else interval_s
    compose_file = compose_file or compose_file_for_stack(stack)
    project, services = compose_project_and_services(compose_file)
    skip = skip_wait_services(compose_file)

    required: list[str] = []
    for svc_name, svc_cfg in services.items():
        if not isinstance(svc_cfg, dict):
            continue
        if svc_name in skip:
            print(f"health: skip {stack}/{svc_name} (x-homelab.health_wait=false)", flush=True)
            continue
        if not service_has_healthcheck(svc_cfg):
            raise SystemExit(
                f"health: {stack}/{svc_name} has no healthcheck — add one or set "
                f"x-homelab.health_wait: false"
            )
        required.append(svc_name)

    if not required:
        print(f"health: {stack} has no services to wait on", flush=True)
        return

    deadline = time.time() + timeout_s
    pending = set(required)
    last: dict[str, str] = {}
    print(
        f"health: waiting for {stack} ({project}): {', '.join(sorted(pending))} "
        f"(timeout {timeout_s:.0f}s)",
        flush=True,
    )
    while pending and time.time() < deadline:
        containers = containers_for_compose(compose_file)
        by_svc = {c["service"]: c["name"] for c in containers}
        still: set[str] = set()
        for svc in pending:
            name = by_svc.get(svc)
            if not name:
                last[svc] = "no container"
                still.add(svc)
                continue
            try:
                state = inspect_state(name)
            except subprocess.CalledProcessError as exc:
                last[svc] = f"inspect failed: {exc}"
                still.add(svc)
                continue
            status = state.get("Status")
            health = ((state.get("Health") or {}) or {}).get("Status")
            last[svc] = f"{name} {status} health={health}"
            if status == "running" and health == "healthy":
                print(f"health: {stack}/{svc} healthy ({name})", flush=True)
                continue
            still.add(svc)
        pending = still
        if pending:
            time.sleep(interval_s)

    if pending:
        detail = "; ".join(f"{s}={last.get(s, '?')}" for s in sorted(pending))
        raise SystemExit(f"health: {stack} not healthy after {timeout_s:.0f}s — {detail}")
    print(f"health: {stack} all required services healthy", flush=True)
