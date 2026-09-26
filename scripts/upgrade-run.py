#!/usr/bin/env python3
"""Run pending major-upgrade jobs (postgres dump/restore). Reads config/upgrades.yml."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(1)

import homelab_paths as hp

ROOT = hp.data_root()  # data plane checkout
UPGRADES = ROOT / "config" / "upgrades.yml"
DEPLOY = ROOT / "config" / "deploy.yml"
GITHUB = ROOT / "config" / "secrets" / "github.yml"
SRE = ROOT / "config" / "sre.yml"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def jobs_dir() -> Path:
    cfg = load_yaml(UPGRADES)
    rel = cfg.get("jobs_dir") or ".generated/upgrade-jobs"
    path = ROOT / rel if not Path(rel).is_absolute() else Path(rel)
    path.mkdir(parents=True, exist_ok=True)
    return path


def telegram_notify(text: str) -> None:
    deploy = load_yaml(DEPLOY)
    notify = deploy.get("notify") or {}
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(ROOT / "scripts"))
    from sops_secrets import load_yaml as load_secret_yaml
    from sops_secrets import secrets_path

    tg_name = _Path(notify.get("telegram_config") or "config/secrets/telegram.yml").name
    if not secrets_path(tg_name).is_file():
        return
    raw = load_secret_yaml(tg_name)
    notif = raw.get("telegram") or (raw.get("notif") or {}).get("telegram") or {}
    token = notif.get("token")
    chats = notif.get("chatIDs") or notif.get("chat_ids") or []
    if not token or not chats:
        return
    for chat in chats:
        body = json.dumps({"chat_id": str(chat), "text": text[:3500]}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                resp.read()
        except urllib.error.URLError as exc:
            print(f"Telegram notify failed: {exc}", file=sys.stderr)


def github_token_repo() -> tuple[str, str]:
    sys.path.insert(0, str(ROOT / "scripts"))
    from sops_secrets import load_yaml as load_secret_yaml
    from sops_secrets import secrets_path

    secrets = load_secret_yaml("github.yml") if secrets_path("github.yml").is_file() else {}
    token = str(secrets.get("token") or "")
    repo = str((load_yaml(SRE).get("github") or {}).get("repo") or "")
    if not repo:
        repo = str((load_yaml(UPGRADES).get("github") or {}).get("repo") or "")
    if not repo:
        raise RuntimeError("Set github.repo in config/sre.yml or config/upgrades.yml")
    if not token or "REPLACE_WITH_" in token:
        raise RuntimeError("Missing config/secrets/github.yml token")
    return token, repo


def gh(token: str, method: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "homelab-upgrade-run",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"GitHub {method} {path} failed: {exc.code} {err}") from exc


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, **kwargs)


def patch_postgres_compose(text: str, to_image: str, to_major: int) -> str:
    """Bump postgres image and point its data mount at a fresh PG18+ volume path."""
    vol = f"postgres_data_pg{to_major}"
    # Match postgres:N, alpine tags, and docker.io/library/postgres:…
    text, n = re.subn(
        r"image:\s*(?:docker\.io/library/)?postgres:\S+",
        f"image: {to_image}",
        text,
        count=1,
    )
    if n != 1:
        raise RuntimeError("Could not find a postgres image: line to patch in compose")
    # Any volume name currently on the legacy /data path (or already on PG18 path).
    text, n = re.subn(
        r"^(\s*-\s+)\S+:/var/lib/postgresql(?:/data)?\s*$",
        rf"\1{vol}:/var/lib/postgresql",
        text,
        count=1,
        flags=re.M,
    )
    if n != 1:
        raise RuntimeError("Could not find a postgres data volume mount to patch")
    if re.search(rf"^  {re.escape(vol)}:", text, re.M):
        return text
    if not text.endswith("\n"):
        text += "\n"
    if not re.search(r"^volumes:\s*$", text, re.M):
        text += "\nvolumes:\n"
    text += f"  {vol}:\n"
    return text

def commit_compose(token: str, repo: str, path: str, new_text: str, message: str) -> None:
    meta = gh(token, "GET", f"/repos/{repo}/contents/{path}?ref=main")
    sha = meta.get("sha")
    if not sha:
        raise RuntimeError(f"No sha for {path}")
    content = base64.b64encode(new_text.encode()).decode()
    gh(
        token,
        "PUT",
        f"/repos/{repo}/contents/{path}",
        {
            "message": message,
            "content": content,
            "sha": sha,
            "branch": "main",
        },
    )


def close_pr(token: str, repo: str, number: int, comment: str) -> None:
    gh(token, "POST", f"/repos/{repo}/issues/{number}/comments", {"body": comment})
    gh(token, "PATCH", f"/repos/{repo}/pulls/{number}", {"state": "closed"})


def wait_healthy(name: str, timeout: int = 120) -> None:
    from docker_health import wait_container

    wait_container(name, timeout_s=float(timeout), require_healthcheck=False)


def dump_postgres(container: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "docker",
        "exec",
        container,
        "sh",
        "-c",
        'exec pg_dumpall -U "${POSTGRES_USER:-${POSTGRESQL_USER:-postgres}}"',
    ]
    print(f"+ dump {container} → {dest}", flush=True)
    with dest.open("wb") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))


def restore_postgres(container: str, dump: Path) -> None:
    with dump.open("rb") as fh:
        proc = subprocess.run(
            ["docker", "exec", "-i", container, "sh", "-c", "exec psql -U \"${POSTGRES_USER:-postgres}\" -d postgres"],
            stdin=fh,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))


DB_IMAGE_RE = re.compile(r"(?:^|/)(postgres|mariadb|mysql)(?::|$)", re.I)


def find_db(stack: str, service: str | None = None) -> tuple[str, str]:
    """Return (container_name, compose_service) for the stack database.

    Prefer an explicit service name when given; otherwise pick by image
    (postgres/mariadb/mysql). Prefer a service literally named ``db``.
    """
    fmt = '{{.Names}}\t{{.Label "com.docker.compose.service"}}\t{{.Image}}'
    out = subprocess.check_output(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={stack}",
            *(
                ["--filter", f"label=com.docker.compose.service={service}"]
                if service
                else []
            ),
            "--format",
            fmt,
        ],
        text=True,
    ).strip()
    seen: list[str] = []
    candidates: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, svc, image = parts[0], parts[1], parts[2]
        seen.append(f"{svc or '?'}={image}")
        if service or DB_IMAGE_RE.search(image):
            candidates.append((name, svc))
    if not candidates:
        detail = ", ".join(seen) if seen else "none"
        want = service or "postgres|mariadb|mysql image"
        raise RuntimeError(f"No database container for {stack} ({want}); saw: {detail}")
    if service:
        return candidates[0]
    for name, svc in candidates:
        if svc == "db":
            return name, svc
    return candidates[0]


def compose_base(stack: str, compose_file: Path) -> list[str]:
    """docker compose argv with stack env interpolation (same idea as deploy-stack.sh)."""
    services = hp.services_root() / stack
    if (services / "docker-compose.yml").is_file():
        compose_file = services / "docker-compose.yml"
    elif (services / "compose.yml").is_file():
        compose_file = services / "compose.yml"
    cmd = ["docker", "compose", "-f", str(compose_file)]
    render = ROOT / "scripts" / f"render-{stack}-env.py"
    env_path = hp.generated_dir() / f"{stack}.env"
    if render.is_file():
        subprocess.run(["python3", str(render)], check=True)
    if env_path.is_file():
        # Host-side ${VAR} in compose needs --env-file; service env_file is container-only.
        cmd += ["--env-file", str(env_path)]
    return cmd, compose_file


def no_init_override(db_service: str, to_major: int) -> Path:
    """Strip initdb bind mounts so pg_dumpall can recreate schema cleanly."""
    vol = f"postgres_data_pg{to_major}"
    path = hp.generated_dir() / f"upgrade-{db_service}-no-init.yml"
    path.write_text(
        "services:\n"
        f"  {db_service}:\n"
        "    volumes: !override\n"
        f"      - {vol}:/var/lib/postgresql\n"
    )
    return path


def run_dump_restore(job: dict) -> None:
    stack = job["stack"]
    compose_rel = job["compose_file"]
    to_image = job["to_image"]
    pr = int(job["pr"])
    m = re.search(r"postgres:(\d+)", to_image)
    to_major = int(m.group(1)) if m else 18
    token, repo = github_token_repo()
    preferred = str(job.get("db_service") or "").strip() or None
    container, db_service = find_db(stack, preferred)
    dump = hp.generated_dir() / "db-dumps" / f"upgrade-{stack}-pg{to_major}.sql"
    dump.parent.mkdir(parents=True, exist_ok=True)

    telegram_notify(
        f"{hp.notify_prefix()}major upgrade #{pr} — restic + dump {container} "
        f"(service {db_service})"
    )
    subprocess.run(
        ["python3", str(hp.control_script("restic-run.py")), "backup", "--no-forget"],
        check=True,
        env={**os.environ, "HOMELAB_DATA_ROOT": str(ROOT)},
    )
    dump_postgres(container, dump)

    compose_path = ROOT / compose_rel
    new_text = patch_postgres_compose(compose_path.read_text(), to_image, to_major)
    commit_compose(
        token,
        repo,
        compose_rel,
        new_text,
        f"Upgrade {stack} Postgres to {to_major} via dump/restore playbook.",
    )
    close_pr(
        token,
        repo,
        pr,
        f"Closed: dump/restore playbook committed the {to_major} image and mount. "
        f"Dump at `{dump.name}`.",
    )
    subprocess.run(["git", "-C", str(ROOT), "pull", "--ff-only", "origin", "main"], check=True)
    link = hp.data_script("link-stacks.sh")
    if link.is_file():
        subprocess.run(["bash", str(link)], check=True)

    compose_file = ROOT / compose_rel
    base, compose_cwd_file = compose_base(stack, compose_file)
    override = no_init_override(db_service, to_major)
    up = base + ["-f", str(override), "up", "-d", db_service]
    print("+", " ".join(up), flush=True)
    subprocess.run(up, check=True, cwd=str(compose_cwd_file.parent))
    new_container, _ = find_db(stack, db_service)
    wait_healthy(new_container)
    telegram_notify(f"{hp.notify_prefix()}restoring dump into {new_container}")
    restore_postgres(new_container, dump)
    wait_healthy(new_container)
    # Bring the rest of the stack up with the real compose (init mounts restored).
    full = base + ["up", "-d"]
    print("+", " ".join(full), flush=True)
    subprocess.run(full, check=True, cwd=str(compose_cwd_file.parent))
    telegram_notify(
        f"{hp.notify_prefix()}#{pr} dump/restore finished. {stack} is on Postgres {to_major}. "
        f"Old volume kept for rollback."
    )


def process_jobs() -> int:
    if not UPGRADES.is_file():
        return 0
    pending = [
        p
        for p in sorted(jobs_dir().glob("pending-*.json"))
        if not p.name.startswith("pending-restic-")
    ]
    if not pending:
        return 0
    code = 0
    for path in pending:
        job = json.loads(path.read_text())
        running = path.with_name(path.name.replace("pending-", "running-", 1))
        path.rename(running)
        try:
            if job.get("playbook") != "dump_restore":
                raise RuntimeError(f"unsupported playbook {job.get('playbook')}")
            run_dump_restore(job)
            running.rename(running.with_name(running.name.replace("running-", "done-", 1)))
        except Exception as exc:
            code = 1
            failed = running.with_name(running.name.replace("running-", "failed-", 1))
            running.rename(failed)
            print(f"upgrade job {path.name} failed: {exc}", file=sys.stderr)
            telegram_notify(f"{hp.notify_prefix()}major upgrade failed: {exc}"[:3500])
    return code


def main() -> int:
    return process_jobs()


if __name__ == "__main__":
    raise SystemExit(main())
