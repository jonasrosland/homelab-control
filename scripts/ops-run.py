#!/usr/bin/env python3
"""Run pending host ops jobs queued from Telegram investigation Allow."""
from __future__ import annotations

import json
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
JOBS = hp.generated_dir() / "upgrade-jobs"
DEPLOY = ROOT / "config" / "deploy.yml"


def telegram_notify(text: str) -> None:
    try:
        sys.path.insert(0, str(hp.CONTROL_ROOT / "scripts"))
        from sops_secrets import load_yaml as load_secret_yaml
        from sops_secrets import secrets_path

        raw = yaml.safe_load((ROOT / "config" / "deploy.yml").read_text()) or {}
        tg_name = Path(
            raw.get("notify", {}).get("telegram_config") or "config/secrets/telegram.yml"
        ).name
        if not secrets_path(tg_name).is_file():
            return
        secrets = load_secret_yaml(tg_name)
        notif = secrets.get("telegram") or (secrets.get("notif") or {}).get("telegram") or {}
        token = notif.get("token")
        chats = notif.get("chatIDs") or notif.get("chat_ids") or []
        if not token or not chats:
            return
        import urllib.request

        for chat in chats:
            body = json.dumps({"chat_id": str(chat), "text": text[:3500]}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=20).read()
    except Exception as exc:
        print(f"telegram: {exc}", file=sys.stderr)


def process() -> int:
    JOBS.mkdir(parents=True, exist_ok=True)
    pending = sorted(JOBS.glob("pending-restic-*.json"))
    if not pending:
        return 0
    code = 0
    for path in pending:
        running = path.with_name(path.name.replace("pending-", "running-", 1))
        path.rename(running)
        try:
            print(f"+ restic backup --no-forget ({path.name})", flush=True)
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts" / "restic-run.py"),
                    "backup",
                    "--no-forget",
                    "--tag",
                    "trigger:manual",
                ],
                check=True,
                cwd=str(ROOT),
            )
            running.rename(running.with_name(running.name.replace("running-", "done-", 1)))
            telegram_notify(f"{hp.notify_prefix()}restic backup (from Telegram Allow) finished.")
        except Exception as exc:
            code = 1
            failed = running.with_name(running.name.replace("running-", "failed-", 1))
            running.rename(failed)
            telegram_notify(
                f"{hp.notify_prefix()}restic backup from Telegram failed: {exc}"[:3500]
            )
    return code


if __name__ == "__main__":
    raise SystemExit(process())
