#!/usr/bin/env python3
"""restic backup/check/init — configs and SQL dumps only. Reads restic/restic.yml."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(1)

from homelab_tz import apply as apply_tz
import homelab_paths as hp

ROOT = hp.data_root()
CFG_PATH = ROOT / "restic" / "restic.yml"


def load_cfg() -> dict:
    return yaml.safe_load(CFG_PATH.read_text())


def resolve(p: str | Path) -> Path:
    return hp.resolve_data(p)


def rclone_serve_args(cfg: dict, config_path: Path) -> str:
    rc = cfg.get("rclone") or {}
    tps = int(rc.get("tpslimit") or 8)
    burst = int(rc.get("tpslimit_burst") or 8)
    chunk = rc.get("drive_chunk_size") or "64M"
    return (
        "serve restic --stdio --b2-hard-delete "
        f"--tpslimit {tps} --tpslimit-burst {burst} "
        f"--drive-chunk-size {chunk} --config {config_path}"
    )


def restic_binary(cfg: dict) -> Path:
    configured = cfg.get("restic_bin")
    if configured:
        path = Path(str(configured))
        if path.is_file():
            return path
    found = shutil.which("restic")
    if found:
        return Path(found)
    return Path("/usr/local/bin/restic")


def restic_cmd(cfg: dict, *args: str) -> list[str]:
    binary = restic_binary(cfg)
    sys.path.insert(0, str(ROOT / "scripts"))
    from sops_secrets import decrypt_to

    rclone_name = Path(cfg["rclone_config"]).name
    password_name = Path(cfg["password_file"]).name
    rclone_conf = decrypt_to(rclone_name)
    password = decrypt_to(password_name)
    rc = cfg.get("rclone") or {}
    timeout = rc.get("timeout") or "5m"
    return [
        str(binary),
        "--retry-lock",
        "5m",
        "--repo",
        cfg["repository"],
        "--password-file",
        str(password),
        "--option",
        f"rclone.timeout={timeout}",
        "--option",
        f"rclone.args={rclone_serve_args(cfg, rclone_conf)}",
        *args,
    ]


def backup_paths(cfg: dict, include_heavy: bool) -> list[str]:
    backup = cfg["backup"]
    paths = list(backup.get("paths") or [])
    if include_heavy:
        paths.extend(backup.get("heavy_paths") or [])
    return paths


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True, file=sys.stderr)
    return subprocess.run(cmd, **kwargs)


def discover_db_containers(engines: list[str]) -> list[tuple[str, str]]:
    out = subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"],
        text=True,
    )
    found: list[tuple[str, str]] = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        name, image = line.split("\t", 1)
        image_l = image.lower()
        if "postgres" in image_l and "postgres" in engines:
            found.append((name, "postgres"))
        elif ("mariadb" in image_l or "/mysql" in image_l or image_l.startswith("mysql")) and (
            "mariadb" in engines or "mysql" in engines
        ):
            found.append((name, "mysql"))
    return found


SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "library",
    "books",
    "audiobooks",
    "podcasts",
    "downloads",
    "media",
    "models",
    "MediaCover",
    "logs",
    "cache",
    ".cache",
    "thumbs",
    "youtube",
    "comics",
    "manga",
    "roms",
    "music",
    "movies",
    "photos",
    "pictures",
    "uploads",
    "storage",
    "processed_books",
    "unused",
}

KEEP_NAMES = {
    "docker-compose.yml",
    "compose.yml",
    "compose.yaml",
    ".env",
}

KEEP_SUFFIXES = {
    ".yml",
    ".yaml",
    ".json",
    ".xml",
    ".toml",
    ".conf",
    ".ini",
    ".env",
    ".sqlite",
    ".db",
    ".sql",
}

MAX_STACK_FILE = 200 * 1024 * 1024


def collect_stack_configs(cfg: dict) -> Path | None:
    backup = cfg["backup"]
    root = backup.get("stack_root")
    dest_rel = backup.get("stack_copy_dir")
    if not root or not dest_rel:
        return None
    src_root = hp.host_path_to_runtime(root)
    dest_root = resolve(dest_rel)
    if dest_root.exists():
        shutil.rmtree(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0
    if not src_root.is_dir():
        print(f"skip missing stack_root {src_root}", flush=True)
        return dest_root
    allow_subdirs = {"config", "configs", "database", "db", "db-data", "db_data"}
    for stack in sorted(src_root.iterdir()):
        if not stack.is_dir() or stack.name in SKIP_DIR_NAMES:
            continue
        candidates: list[Path] = [stack]
        for child in sorted(stack.iterdir()):
            if child.is_dir() and child.name in allow_subdirs:
                candidates.append(child)
        for base in candidates:
            for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
                if Path(dirpath) == stack:
                    dirnames.clear()
                else:
                    depth = Path(dirpath).relative_to(base).parts
                    if len(depth) > 2:
                        dirnames.clear()
                        continue
                    dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
                rel_dir = Path(dirpath).relative_to(src_root)
                for name in filenames:
                    src = Path(dirpath) / name
                    if name not in KEEP_NAMES and src.suffix.lower() not in KEEP_SUFFIXES:
                        skipped += 1
                        continue
                    try:
                        size = src.stat().st_size
                    except OSError:
                        skipped += 1
                        continue
                    if size > MAX_STACK_FILE:
                        print(f"skip large {src} ({size} bytes)", flush=True)
                        skipped += 1
                        continue
                    dest = dest_root / rel_dir / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(src, dest, follow_symlinks=False)
                        copied += 1
                    except OSError as exc:
                        print(f"skip {src}: {exc}", flush=True)
                        skipped += 1
    print(f"Collected {copied} stack config files ({skipped} skipped) → {dest_root}", flush=True)
    return dest_root


def dump_databases(cfg: dict) -> Path:
    backup = cfg["backup"]
    dump_dir = resolve(backup["dump_dir"])
    if dump_dir.exists():
        shutil.rmtree(dump_dir)
    dump_dir.mkdir(parents=True, exist_ok=True)
    engines = backup.get("databases", {}).get("engines", ["postgres", "mariadb", "mysql"])
    containers = discover_db_containers(engines)
    if not containers:
        print("No running SQL containers to dump", flush=True)
        return dump_dir
    for name, engine in containers:
        dest = dump_dir / f"{name}.sql"
        print(f"Dumping {engine} from {name} → {dest}", flush=True)
        if engine == "postgres":
            cmd = [
                "docker",
                "exec",
                name,
                "sh",
                "-c",
                'exec pg_dumpall -U "${POSTGRES_USER:-${POSTGRESQL_USER:-postgres}}"',
            ]
        else:
            cmd = [
                "docker",
                "exec",
                name,
                "sh",
                "-c",
                'exec mysqldump --all-databases --single-transaction --routines '
                '-u"${MYSQL_USER:-root}" '
                '-p"${MYSQL_ROOT_PASSWORD:-${MYSQL_PASSWORD:-}}"',
            ]
        with dest.open("wb") as fh:
            proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            dest.unlink(missing_ok=True)
            raise RuntimeError(
                f"Dump failed for {name}: {proc.stderr.decode('utf-8', 'replace').strip()}"
            )
        print(f"  {dest.stat().st_size} bytes", flush=True)
    return dump_dir


def telegram_notify(cfg: dict, text: str) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from sops_secrets import load_yaml as load_secret_yaml
    from sops_secrets import secrets_path

    tg_name = Path(cfg["notify"]["telegram_config"]).name
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


def cmd_init(cfg: dict) -> int:
    return run(restic_cmd(cfg, "init")).returncode


def fix_scrutiny_perms() -> None:
    script = ROOT / "scripts" / "fix-scrutiny-perms.sh"
    if not script.is_file():
        return
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    if out.strip():
        print(out.rstrip(), flush=True)
    if proc.returncode != 0:
        print("fix-scrutiny-perms failed; restic may skip influxd.bolt", flush=True)


def cmd_prepare(cfg: dict) -> int:
    """SQL dumps + stack config copy. Backrest then snapshots the files."""
    fix_scrutiny_perms()
    dump_databases(cfg)
    collect_stack_configs(cfg)
    return 0


def cmd_backup(
    cfg: dict,
    forget: bool = True,
    include_heavy: bool = True,
    extra_tags: list[str] | None = None,
) -> int:
    code = cmd_prepare(cfg)
    if code != 0:
        return code
    args: list[str] = ["backup", "--one-file-system=false"]
    pack = int((cfg.get("backup") or {}).get("pack_size_mb") or 0)
    if pack:
        args.extend(["--pack-size", str(pack)])
    tags = [str(t) for t in ((cfg.get("backup") or {}).get("tags") or [])]
    for t in extra_tags or []:
        if t and t not in tags:
            tags.append(t)
    for tag in tags:
        args.extend(["--tag", tag])
    for pattern in cfg["backup"].get("exclude") or []:
        args.extend(["--exclude", str(pattern)])
    for path in backup_paths(cfg, include_heavy=include_heavy):
        p = hp.host_path_to_runtime(path)
        if p.exists():
            args.append(str(p))
        else:
            print(f"skip missing path {p}", flush=True)
    if not include_heavy:
        print("skip heavy_paths (pre-deploy)", flush=True)
    proc = run(restic_cmd(cfg, *args))
    if proc.returncode != 0:
        return proc.returncode
    if not forget:
        print("skip forget/prune (--no-forget)", flush=True)
        return 0
    forget_args = [
        "forget",
        "--prune",
        "--keep-daily",
        str(cfg["retention"]["keep_daily"]),
        "--keep-weekly",
        str(cfg["retention"]["keep_weekly"]),
        "--keep-monthly",
        str(cfg["retention"]["keep_monthly"]),
        "--keep-yearly",
        str(cfg["retention"]["keep_yearly"]),
    ]
    return run(restic_cmd(cfg, *forget_args)).returncode


def cmd_check(cfg: dict) -> int:
    return run(restic_cmd(cfg, "check")).returncode


def cmd_snapshots(cfg: dict, *, as_json: bool = False, compact: bool = False) -> int:
    args = ["snapshots"]
    if as_json:
        args.append("--json")
    if compact:
        args.append("--compact")
    return run(restic_cmd(cfg, *args)).returncode


def main() -> int:
    apply_tz(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "backup", "check", "snapshots", "prepare"])
    parser.add_argument(
        "--no-forget",
        action="store_true",
        help="Skip forget/prune (pre-deploy backups; nightly still prunes)",
    )
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Extra restic --tag (repeatable). trigger:manual for Telegram Allow.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON output (snapshots only; used by ops-read age checks)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Compact table (snapshots only)",
    )
    args = parser.parse_args()
    cfg = load_cfg()
    try:
        if args.action == "init":
            code = cmd_init(cfg)
        elif args.action == "prepare":
            code = cmd_prepare(cfg)
        elif args.action == "backup":
            extra = list(args.tag or [])
            # Pre-deploy / upgrade path: tag so Digest ignores these for nightly age.
            if args.no_forget and not any(str(t).startswith("trigger:") for t in extra):
                extra.append("trigger:pre-deploy")
            code = cmd_backup(
                cfg,
                forget=not args.no_forget,
                include_heavy=not args.no_forget,
                extra_tags=extra,
            )
        elif args.action == "check":
            code = cmd_check(cfg)
        else:
            code = cmd_snapshots(cfg, as_json=args.json, compact=args.compact)
    except Exception as exc:
        if cfg.get("notify", {}).get("on_failure") == "telegram":
            telegram_notify(
                cfg, f"{hp.notify_prefix(cfg)}restic {args.action} failed: {exc}"
            )
        raise
    if code != 0 and cfg.get("notify", {}).get("on_failure") == "telegram":
        telegram_notify(cfg, f"{hp.notify_prefix(cfg)}restic {args.action} exited {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
