#!/usr/bin/env python3
"""Poll origin/main and deploy git-managed stacks whose paths changed."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
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


def load_cfg() -> dict:
    return yaml.safe_load((hp.data_root() / "config" / "deploy.yml").read_text())


def _homelab_manifest() -> dict:
    for name in ("homelab.yml", "homelab.yaml"):
        path = hp.data_root() / "config" / name
        if path.is_file():
            raw = yaml.safe_load(path.read_text()) or {}
            return raw if isinstance(raw, dict) else {}
    return {}


def verify_control_pin() -> None:
    """Ensure control checkout matches homelab.yml paths and optional git.control_ref."""
    manifest = _homelab_manifest()
    paths = manifest.get("paths") or {}
    want_root = paths.get("control_root")
    if want_root:
        want_p = Path(str(want_root)).resolve()
        here = hp.control_root().resolve()
        if want_p.is_dir() and here != want_p:
            raise RuntimeError(
                f"control checkout is {here} but config/homelab.yml "
                f"paths.control_root is {want_p}"
            )
    ref = str((manifest.get("git") or {}).get("control_ref") or "").strip()
    if not ref:
        return
    cr = hp.control_root()
    head = subprocess.run(
        ["git", "-C", str(cr), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    want_sha = subprocess.run(
        ["git", "-C", str(cr), "rev-parse", ref],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if head != want_sha:
        raise RuntimeError(
            f"homelab.yml git.control_ref {ref!r} → {want_sha[:8]} "
            f"but control checkout is {head[:8]}"
        )


def _data() -> Path:
    return hp.data_root()


def resolve(p: str | Path) -> Path:
    return hp.resolve_data(p)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(_data()), *args],
        check=check,
        text=True,
        capture_output=True,
        env={**os.environ, "HOMELAB_DATA_ROOT": str(_data())},
    )


def git_stderr(exc: BaseException) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        return (exc.stderr or exc.stdout or "").strip()
    return str(exc).strip()


def transient_git_fetch_kind(stderr: str) -> str | None:
    s = stderr.lower()
    if "permission denied (publickey)" in s:
        return "ssh"
    if "could not resolve hostname" in s or "name or service not known" in s:
        return "dns"
    if "connection timed out" in s or "connection reset" in s:
        return "network"
    if "network is unreachable" in s or "temporary failure" in s:
        return "network"
    if "could not read from remote repository" in s:
        return "remote"
    if "the remote end hung up" in s or "early eof" in s:
        return "network"
    return None


def format_fetch_failure(kind: str | None, stderr: str, attempts: int) -> str:
    detail = {
        "ssh": "GitHub rejected the deploy key for a moment (Permission denied publickey)",
        "dns": "DNS could not resolve github.com",
        "network": "network blip talking to GitHub",
        "remote": "GitHub remote was briefly unreachable",
    }.get(kind or "", "git fetch failed")
    head = (
        f"{hp.notify_prefix()}git fetch hiccup ({detail}). "
        f"Retried {attempts}×; next poll will try again. No stacks changed."
    )
    fingerprint = f"git-fetch:{kind or 'unknown'}"
    return f"{head}\n[{fingerprint}]\n{stderr[:1500]}"


def fetch_origin(remote: str, *, attempts: int = 3, delay_s: float = 2.0) -> None:
    last: subprocess.CalledProcessError | None = None
    for i in range(1, attempts + 1):
        try:
            git("fetch", "--quiet", remote)
            return
        except subprocess.CalledProcessError as exc:
            last = exc
            err = git_stderr(exc)
            kind = transient_git_fetch_kind(err)
            if kind is None or i >= attempts:
                raise
            wait = delay_s * (2 ** (i - 1))
            print(
                f"git fetch {kind} blip ({i}/{attempts}); retry in {wait:.0f}s",
                flush=True,
            )
            time.sleep(wait)
    assert last is not None
    raise last


def format_deploy_failure(exc: BaseException, *, fetch_attempts: int = 3) -> str:
    err = git_stderr(exc)
    if isinstance(exc, subprocess.CalledProcessError):
        cmd = exc.cmd if isinstance(exc.cmd, (list, tuple)) else []
        if len(cmd) >= 4 and cmd[0] == "git" and "fetch" in cmd:
            return format_fetch_failure(
                transient_git_fetch_kind(err), err, fetch_attempts
            )
        if len(cmd) >= 4 and cmd[0] == "git" and "pull" in cmd:
            return (
                f"{hp.notify_prefix()}git pull failed after fetch. "
                f"Working tree left alone.\n{err[:1500]}"
            )
    text = str(exc)
    if "working tree is dirty" in text:
        return (
            f"{hp.notify_prefix()}deploy paused — live clone has local edits "
            f"(refusing to pull).\n{text[:1500]}"
        )
    out = f"{hp.notify_prefix().rstrip(':')} deploy-from-git failed: {exc}"
    if err and err not in out:
        out = f"{out}\n{err}"
    return out


def last_failure_path(cfg: dict) -> Path:
    return resolve(cfg.get("lock_file") or ".generated/deploy.lock").with_name(
        "deploy-last-failure.txt"
    )


def pending_path(cfg: dict) -> Path:
    return resolve(cfg.get("lock_file") or ".generated/deploy.lock").with_name(
        "deploy-pending.json"
    )


def load_pending(cfg: dict) -> list[str]:
    path = pending_path(cfg)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text()) or {}
    except json.JSONDecodeError:
        return []
    stacks = raw.get("stacks") or []
    return [str(s) for s in stacks if s]


def load_pending_record(cfg: dict) -> tuple[str | None, list[str]]:
    path = pending_path(cfg)
    if not path.is_file():
        return None, []
    try:
        raw = json.loads(path.read_text()) or {}
    except json.JSONDecodeError:
        return None, []
    stacks = [str(s) for s in (raw.get("stacks") or []) if s]
    sha = raw.get("sha")
    return (str(sha) if sha else None), stacks


def save_pending(cfg: dict, sha: str, stacks: list[str]) -> None:
    path = pending_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sha": sha, "stacks": stacks}, indent=2) + "\n")


def clear_pending(cfg: dict) -> None:
    path = pending_path(cfg)
    if path.is_file():
        path.unlink()


def telegram_notify(cfg: dict, text: str) -> None:
    notify = cfg.get("notify") or {}
    sys.path.insert(0, str(hp.CONTROL_ROOT / "scripts"))
    from sops_secrets import load_yaml as load_secret_yaml
    from sops_secrets import secrets_path

    tg_name = Path(notify.get("telegram_config") or "config/secrets/telegram.yml").name
    if not secrets_path(tg_name).is_file():
        print(f"No telegram config at config/secrets/{tg_name}", file=sys.stderr)
        return
    raw = load_secret_yaml(tg_name)
    notif = raw.get("telegram") or raw.get("notif", {}).get("telegram") or {}
    token = notif.get("token")
    chats = notif.get("chatIDs") or notif.get("chat_ids") or []
    if not token or not chats:
        print("Telegram token/chat missing; skip notify", file=sys.stderr)
        return
    for chat in chats:
        body = json.dumps({"chat_id": str(chat), "text": text}).encode()
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


def notify_failure_once(cfg: dict, text: str) -> None:
    path = last_failure_path(cfg)
    clipped = text[:3500]
    prev = path.read_text() if path.is_file() else ""
    if prev == clipped:
        print("duplicate failure; skip telegram", flush=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(clipped)
    if (cfg.get("notify") or {}).get("on_failure") == "telegram":
        telegram_notify(cfg, clipped)


def clear_last_failure(cfg: dict) -> None:
    path = last_failure_path(cfg)
    if path.is_file():
        path.unlink()


def changed_files(old: str, new: str) -> list[str]:
    out = git("diff", "--name-only", f"{old}..{new}").stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def path_hit(changed: list[str], prefixes: list[str]) -> bool:
    for item in changed:
        for prefix in prefixes:
            p = prefix.rstrip("/")
            if item == p or item.startswith(p + "/"):
                return True
    return False


def acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def dirty_tree() -> str:
    return git("status", "--porcelain").stdout.strip()


def _sub_env() -> dict[str, str]:
    return {**os.environ, "HOMELAB_DATA_ROOT": str(_data())}


def deploy_stack(name: str, spec: dict) -> None:
    deploy_script = (spec or {}).get("deploy_script")
    if deploy_script:
        cmd = ["bash", str(resolve(deploy_script))]
        print("+", " ".join(cmd), flush=True)
        subprocess.run(cmd, check=True, env=_sub_env(), cwd=_data())
        return
    cmd = ["bash", str(hp.control_script("deploy-stack.sh")), name]
    if spec.get("recreate"):
        cmd.append("--force-recreate")
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, env=_sub_env(), cwd=_data())


def sync_opnsense_dns() -> None:
    script = hp.data_script("sync-opnsense-dns.py")
    if not script.is_file():
        print(f"skip OPNsense sync (no {script})", flush=True)
        return
    cmd = ["python3", str(script)]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, env=_sub_env(), cwd=_data())


def run_ops_jobs() -> int:
    jobs = hp.generated_dir() / "upgrade-jobs"
    pending = list(jobs.glob("pending-restic-*.json")) if jobs.is_dir() else []
    if not pending:
        return 0
    print(f"+ ops-run.py ({len(pending)} restic job(s))", flush=True)
    subprocess.run(
        ["python3", str(hp.control_script("ops-run.py"))],
        check=True,
        env=_sub_env(),
        cwd=_data(),
    )
    return len(pending)


def run_upgrade_jobs() -> int:
    jobs = hp.generated_dir() / "upgrade-jobs"
    pending = list(jobs.glob("pending-*.json")) if jobs.is_dir() else []
    if not pending:
        return 0
    print(f"+ upgrade-run.py ({len(pending)} job(s))", flush=True)
    subprocess.run(
        ["python3", str(hp.control_script("upgrade-run.py"))],
        check=True,
        env=_sub_env(),
        cwd=_data(),
    )
    return len(pending)


def pre_deploy_backup(cfg: dict, stacks: list[str], *, notify: bool = True) -> None:
    if not cfg.get("pre_deploy_backup", True):
        return
    names = ", ".join(stacks)
    print(f"+ restic backup before deploy ({names})", flush=True)
    notify_cfg = cfg.get("notify") or {}
    if notify and notify_cfg.get("on_success") == "telegram":
        telegram_notify(cfg, f"{hp.notify_prefix(cfg)}restic backup before deploy ({names})")
    subprocess.run(
        ["python3", str(hp.control_script("restic-run.py")), "backup", "--no-forget"],
        check=True,
        env=_sub_env(),
        cwd=_data(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stack",
        action="append",
        default=[],
        help="Deploy this stack after pull (repeatable). Skips path matching.",
    )
    args = parser.parse_args()
    cfg = load_cfg()
    verify_control_pin()
    lock = acquire_lock(resolve(cfg.get("lock_file") or ".generated/deploy.lock"))
    if lock is None:
        print("deploy-from-git already running")
        return 0
    remote = cfg.get("remote") or "origin"
    branch = cfg.get("branch") or "main"
    notify = cfg.get("notify") or {}
    try:
        n_ops = run_ops_jobs()
        n_jobs = run_upgrade_jobs()
        if n_ops or n_jobs:
            clear_last_failure(cfg)
            return 0

        dirty = dirty_tree()
        if dirty:
            raise RuntimeError(f"refusing to pull; working tree is dirty:\n{dirty}")

        git_cfg = cfg.get("git") or {}
        fetch_origin(
            remote,
            attempts=int(git_cfg.get("fetch_retries") or 3),
            delay_s=float(git_cfg.get("fetch_retry_seconds") or 2),
        )
        local = git("rev-parse", "HEAD").stdout.strip()
        remote_head = git("rev-parse", f"{remote}/{branch}").stdout.strip()
        pulled = False
        if local != remote_head:
            print(f"fast-forward {local[:8]} → {remote_head[:8]}", flush=True)
            git("pull", "--ff-only", remote, branch)
            pulled = True
            link = hp.data_script("link-stacks.sh")
            if link.is_file():
                subprocess.run(
                    ["bash", str(link)],
                    check=True,
                    env=_sub_env(),
                    cwd=_data(),
                )
        else:
            print(f"already up to date at {local[:8]}", flush=True)

        if pulled:
            cfg = load_cfg()
            notify = cfg.get("notify") or {}

        new_head = git("rev-parse", "HEAD").stdout.strip()
        changed = changed_files(local, new_head) if pulled else []
        did_dns_sync = False
        if pulled and not args.stack and path_hit(
            changed,
            [
                "config/dns.yml",
                "config/opnsense.yml",
                "config/secrets/opnsense.yml",
                "scripts/sync-opnsense-dns.py",
            ],
        ):
            sync_opnsense_dns()
            did_dns_sync = True

        stacks_cfg = cfg.get("stacks") or {}
        to_deploy: list[str] = []
        pending_sha, pending_stacks = load_pending_record(cfg)
        if args.stack:
            to_deploy = list(args.stack)
        else:
            for name, spec in stacks_cfg.items():
                paths = (spec or {}).get("paths") or []
                if path_hit(changed, paths):
                    to_deploy.append(name)
            for name in pending_stacks:
                if name in stacks_cfg and name not in to_deploy:
                    print(f"retry pending deploy: {name}", flush=True)
                    to_deploy.append(name)

        if not to_deploy:
            if pulled:
                print("pull applied; no managed stack paths changed")
            if did_dns_sync and notify.get("on_success") == "telegram":
                msg = (
                    f"{hp.notify_prefix(cfg)}synced edge DNS from "
                    f"{new_head[:8]} ({git('log', '-1', '--format=%s').stdout.strip()})"
                )
                telegram_notify(cfg, msg)
            clear_pending(cfg)
            clear_last_failure(cfg)
            return 0

        same_sha_retry = (
            not pulled
            and not args.stack
            and pending_sha == new_head
            and set(to_deploy) <= set(pending_stacks)
        )
        save_pending(cfg, new_head, to_deploy)
        if same_sha_retry:
            print(
                f"skip pre-deploy restic (pending retry of {new_head[:8]})",
                flush=True,
            )
        else:
            pre_deploy_backup(cfg, to_deploy)

        deployed: list[str] = []
        remaining = list(to_deploy)
        for name in to_deploy:
            spec = stacks_cfg.get(name) or {}
            deploy_stack(name, spec)
            deployed.append(name)
            remaining = remaining[1:]
            if remaining:
                save_pending(cfg, new_head, remaining)
            else:
                clear_pending(cfg)

        msg = (
            f"{hp.notify_prefix(cfg)}deployed {', '.join(deployed)} from "
            f"{new_head[:8]} ({git('log', '-1', '--format=%s').stdout.strip()})"
        )
        if did_dns_sync:
            msg += " + synced edge DNS"
        print(msg, flush=True)
        if notify.get("on_success") == "telegram":
            telegram_notify(cfg, msg)
        clear_pending(cfg)
        clear_last_failure(cfg)
        return 0
    except Exception as exc:
        git_cfg = cfg.get("git") or {}
        attempts = int(git_cfg.get("fetch_retries") or 3)
        text = format_deploy_failure(exc, fetch_attempts=attempts)
        print(text, file=sys.stderr)
        notify_failure_once(cfg, text)
        if isinstance(exc, subprocess.CalledProcessError):
            cmd = exc.cmd if isinstance(exc.cmd, (list, tuple)) else []
            if (
                len(cmd) >= 4
                and cmd[0] == "git"
                and "fetch" in cmd
                and transient_git_fetch_kind(git_stderr(exc))
            ):
                return 0
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
