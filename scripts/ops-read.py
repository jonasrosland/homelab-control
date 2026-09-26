#!/usr/bin/env python3
"""Read-only journal + restic + deploy status for Rosland SRE bot. Unix socket, no secrets."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(1)

from homelab_tz import apply as apply_tz, timezone as host_timezone
import homelab_paths as hp

ROOT = hp.data_root()
CFG = ROOT / "config" / "ops-read.yml"
DEPLOY_CFG = ROOT / "config" / "deploy.yml"


def load_cfg() -> dict:
    return yaml.safe_load(CFG.read_text()) or {}


class UnixHTTPServer(HTTPServer):
    address_family = socket.AF_UNIX

    def server_bind(self) -> None:
        path = str(self.server_address)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self.socket.bind(path)
        os.chmod(path, 0o666)
        self.server_address = path


def redact(text: str) -> str:
    out = []
    for line in text.splitlines():
        if "password-file" in line.lower() or "rclone.conf" in line or "ghp_" in line:
            continue
        if "Authorization:" in line or "Bearer " in line:
            continue
        out.append(line)
    return "\n".join(out)[-12000:]


def shorten_paths(text: str) -> str:
    """Keep Telegram/digest readable — shorten absolute paths from homelab.yml."""
    try:
        svc = str(hp.services_root())
        data = str(hp.data_root())
    except RuntimeError:
        return text
    text = text.replace(svc.rstrip("/") + "/", "services/")
    text = text.replace(data.rstrip("/"), "git")
    return text


def journal(unit: str, lines: int) -> str:
    env = os.environ.copy()
    env["TZ"] = host_timezone(ROOT)
    proc = subprocess.run(
        [
            "journalctl",
            "--user",
            "-u",
            unit,
            "-n",
            str(lines),
            "--no-pager",
            "--output",
            "short-iso",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    body = proc.stdout or proc.stderr
    return shorten_paths(redact(body)) or f"(empty journal for {unit}, exit {proc.returncode})"


def _restic_run(*extra: str, timeout: int = 90) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["TZ"] = host_timezone(ROOT)
    return subprocess.run(
        ["python3", str(ROOT / "scripts" / "restic-run.py"), "snapshots", *extra],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(ROOT),
        env=env,
    )


def restic_snapshots(limit: int) -> str:
    proc = _restic_run("--compact")
    body = shorten_paths(redact((proc.stdout or "") + (proc.stderr or "")))
    lines = [ln for ln in body.splitlines() if ln.strip()]
    return "\n".join(lines[-max(limit * 2, 16) :]) or f"restic snapshots exit {proc.returncode}"


def _snap_kind(tags: list[str], when: datetime, cfg: dict) -> str:
    """Classify snapshot: nightly (Backrest), pre-deploy, or manual."""
    tagset = {t.lower() for t in tags}
    if "trigger:manual" in tagset:
        return "manual"
    if "trigger:pre-deploy" in tagset:
        return "pre-deploy"
    # Legacy untagged CLI snaps: only count as nightly inside the schedule window.
    start = int(cfg.get("restic_nightly_hour_start") or 2)
    end = int(cfg.get("restic_nightly_hour_end") or 5)
    if start <= when.hour < end:
        return "nightly"
    # Untagged outside the window → treat as pre-deploy noise for health.
    return "pre-deploy"


def _last_check_line() -> str:
    """Best-effort last restic-check / Backrest check journal line."""
    body = journal("restic-check.service", 12)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return "(no restic-check.service journal yet — Backrest runs check Sundays 04:00)"
    return lines[-1][:200]


def restic_age() -> dict:
    """Nightly backup health from restic --json (ignores pre-deploy/manual triggers)."""
    cfg = load_cfg()
    proc = _restic_run("--json", timeout=100)
    combined = (proc.stdout or "") + (proc.stderr or "")
    raw = (proc.stdout or "").strip()
    if not raw:
        return {
            "ok": False,
            "error": "empty restic snapshots --json",
            "raw_tail": shorten_paths(redact(combined))[-500:],
        }

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "could not parse restic snapshots --json",
            "raw_tail": shorten_paths(redact(combined))[-500:],
        }

    if not isinstance(data, list) or not data:
        return {
            "ok": False,
            "error": "no snapshots in repository",
            "raw_tail": "",
        }

    tz_name = host_timezone(ROOT)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc

    parsed: list[tuple[datetime, str, list[str]]] = []
    for snap in data:
        t = (snap or {}).get("time")
        if not t:
            continue
        try:
            when = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(tz)
        except ValueError:
            continue
        tags = [str(x) for x in ((snap or {}).get("tags") or [])]
        kind = _snap_kind(tags, when, cfg)
        parsed.append((when, kind, tags))

    if not parsed:
        return {
            "ok": False,
            "error": "snapshots JSON had no parseable times",
            "raw_tail": shorten_paths(raw)[-500:],
        }

    any_latest = max(parsed, key=lambda x: x[0])
    nightly = [p for p in parsed if p[1] == "nightly"]
    pre = [p for p in parsed if p[1] == "pre-deploy"]
    manual = [p for p in parsed if p[1] == "manual"]

    warn_hours = float(cfg.get("restic_stale_hours") or 36)
    now = datetime.now(tz)
    check_line = _last_check_line()

    if not nightly:
        return {
            "ok": True,
            "timezone": tz_name,
            "stale": True,
            "stale_hours": warn_hours,
            "snapshot_count_seen": len(parsed),
            "nightly_count": 0,
            "predeploy_count": len(pre),
            "manual_count": len(manual),
            "any_latest": any_latest[0].strftime("%Y-%m-%d %H:%M:%S"),
            "any_kind": any_latest[1],
            "latest": None,
            "age_hours": None,
            "check_journal": check_line,
            "note": "No scheduled/nightly snapshot found yet (Backrest 03:00). Pre-deploy snaps do not count.",
        }

    latest_dt = max(nightly, key=lambda x: x[0])[0]
    age_hours = (now - latest_dt).total_seconds() / 3600.0
    return {
        "ok": True,
        "latest": latest_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": tz_name,
        "age_hours": round(age_hours, 1),
        "stale": age_hours > warn_hours,
        "stale_hours": warn_hours,
        "snapshot_count_seen": len(parsed),
        "nightly_count": len(nightly),
        "predeploy_count": len(pre),
        "manual_count": len(manual),
        "any_latest": any_latest[0].strftime("%Y-%m-%d %H:%M:%S"),
        "any_kind": any_latest[1],
        "check_journal": check_line,
    }


def git(*args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return (proc.stdout or proc.stderr or "").strip()


def transmission_vpn() -> dict:
    script = ROOT / "scripts" / "check-transmission-vpn.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"ok": False, "error": (proc.stderr or proc.stdout or "bad json").strip()}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "unexpected vpn check payload"}
    payload.setdefault("ok", proc.returncode == 0)
    storage = (load_cfg().get("transmission") or {}).get("storage_path")
    if storage:
        try:
            usage = subprocess.check_output(
                ["df", "-BG", "--output=avail", str(storage)],
                text=True,
            ).strip().splitlines()[-1]
            avail_g = int(usage.replace("G", "").strip())
            payload["storage_avail_gb"] = avail_g
            payload["storage_low"] = avail_g < 50
        except Exception:
            pass
    return payload


def neptune_llmster_version() -> dict:
    script = ROOT / "scripts" / "neptune-llmster-version.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        check=True,
        text=True,
        capture_output=True,
        timeout=45,
    )
    return json.loads(proc.stdout)


def deploy_status() -> dict:
    deploy = yaml.safe_load(DEPLOY_CFG.read_text()) if DEPLOY_CFG.is_file() else {}
    deploy = deploy or {}
    lock_rel = deploy.get("lock_file") or ".generated/deploy.lock"
    lock_path = ROOT / lock_rel if not Path(lock_rel).is_absolute() else Path(lock_rel)
    failure_path = lock_path.with_name("deploy-last-failure.txt")
    pending_path = lock_path.with_name("deploy-pending.json")

    failure = ""
    if failure_path.is_file():
        failure = failure_path.read_text(errors="replace").strip()[:2000]

    pending_stacks: list[str] = []
    pending_sha = ""
    if pending_path.is_file():
        try:
            raw = json.loads(pending_path.read_text()) or {}
            pending_stacks = [str(s) for s in (raw.get("stacks") or []) if s]
            pending_sha = str(raw.get("sha") or "")[:12]
        except json.JSONDecodeError:
            pending_stacks = []

    head = git("rev-parse", "--short", "HEAD")
    subject = git("log", "-1", "--format=%s")
    when = git("log", "-1", "--format=%ci")
    dirty = bool(git("status", "--porcelain"))

    journal_tail = journal("deploy-from-git.service", 20)
    return {
        "ok": True,
        "head": head,
        "subject": subject,
        "committed_at": when,
        "dirty": dirty,
        "pending_stacks": pending_stacks,
        "pending_sha": pending_sha,
        "last_failure": failure,
        "journal_tail": journal_tail[-1500:],
    }


class Handler(BaseHTTPRequestHandler):
    cfg: dict = {}

    def address_string(self) -> str:
        addr = self.client_address
        if isinstance(addr, tuple) and addr:
            return str(addr[0])
        return str(addr) or "unix"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: str, ctype: str = "text/plain; charset=utf-8") -> None:
        data = body.encode("utf-8", "replace")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, indent=2) + "\n", "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(200, "ok\n")
            return
        if parsed.path == "/journal":
            unit = (parse_qs(parsed.query).get("unit") or [""])[0]
            allowed = list(self.cfg.get("journal_units") or [])
            if unit not in allowed:
                self._send(400, f"unit not allowed. want one of: {', '.join(allowed)}\n")
                return
            lines = int(self.cfg.get("journal_lines") or 80)
            self._send(200, journal(unit, lines) + "\n")
            return
        if parsed.path == "/restic/snapshots":
            limit = int(self.cfg.get("restic_snapshot_limit") or 8)
            self._send(200, restic_snapshots(limit) + "\n")
            return
        if parsed.path == "/restic/age":
            try:
                self._send_json(200, restic_age())
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/deploy/status":
            try:
                self._send_json(200, deploy_status())
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/neptune/llmster-version":
            try:
                self._send_json(200, neptune_llmster_version())
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/transmission/vpn":
            try:
                self._send_json(200, transmission_vpn())
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send(404, "not found\n")


def main() -> int:
    apply_tz(ROOT)
    cfg = load_cfg()
    sock = cfg.get("socket") or ".generated/ops-read.sock"
    path = ROOT / sock if not Path(sock).is_absolute() else Path(sock)
    Handler.cfg = cfg
    server = UnixHTTPServer(str(path), Handler)
    print(f"ops-read listening on unix:{path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
