#!/usr/bin/env python3
"""Run homelab jobs on interval or daily wall-clock (data plane config/homelab-scheduler.yml)."""
from __future__ import annotations

import json
import os
import random
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import yaml
except ImportError:
    print("PyYAML required", file=sys.stderr)
    raise SystemExit(1)

CONTROL = Path(os.environ.get("HOMELAB_CONTROL_ROOT", "/opt/homelab/control"))
DATA = Path(os.environ.get("HOMELAB_DATA_ROOT", "/data"))
SCRIPTS = CONTROL / "scripts"
CONFIG = DATA / "config" / "homelab-scheduler.yml"
STATE = DATA / ".generated" / "scheduler-state.json"
ALIVE = Path("/tmp/scheduler-alive")
TICK_SECONDS = 1

_stop = False


def _handle_sig(_signum: int, _frame: object) -> None:
    global _stop
    _stop = True


def load_config() -> dict:
    if not CONFIG.is_file():
        raise SystemExit(f"Missing scheduler config: {CONFIG}")
    raw = yaml.safe_load(CONFIG.read_text()) or {}
    return raw if isinstance(raw, dict) else {}


def tzinfo(cfg: dict) -> ZoneInfo:
    name = (
        str(cfg.get("timezone") or "").strip()
        or os.environ.get("TZ")
        or "UTC"
    )
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def load_state() -> dict:
    if not STATE.is_file():
        return {}
    try:
        raw = json.loads(STATE.read_text())
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def resolve_command(job: dict) -> list[str]:
    kind = str(job.get("kind") or "control").strip()
    if kind == "control":
        cmd = str(job.get("command") or "").strip()
        if not cmd:
            raise ValueError("control job needs command")
        path = SCRIPTS / cmd
        if path.is_file() and path.suffix == ".py":
            return ["python3", str(path)]
        if path.is_file():
            return ["bash", str(path)]
        py = SCRIPTS / f"{cmd}.py"
        sh = SCRIPTS / f"{cmd}.sh"
        if py.is_file():
            return ["python3", str(py)]
        if sh.is_file():
            return ["bash", str(sh)]
        raise ValueError(f"unknown control command: {cmd}")

    if kind == "data-script":
        rel = str(job.get("script") or "").strip()
        if not rel:
            raise ValueError("data-script job needs script")
        path = DATA / rel
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix == ".py":
            return ["python3", str(path)]
        return ["bash", str(path)]

    raise ValueError(f"unknown job kind: {kind}")


def run_job(name: str, job: dict) -> int:
    args = job.get("args") or []
    if not isinstance(args, list):
        args = []
    cmd = resolve_command(job) + [str(a) for a in args]
    print(f"scheduler: start {name}: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=DATA)
    print(f"scheduler: end {name} exit={proc.returncode}", flush=True)
    return int(proc.returncode)


def parse_daily_at(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid daily_at: {value}")
    return int(parts[0]), int(parts[1])


def due_interval(job_state: dict, interval: int, now: float) -> bool:
    last = job_state.get("last_run_epoch")
    if last is None:
        return True
    return (now - float(last)) >= interval


def persist_job(name: str, job_state: dict, state: dict) -> None:
    jobs = state.setdefault("jobs", {})
    if not isinstance(jobs, dict):
        jobs = {}
        state["jobs"] = jobs
    jobs[name] = job_state
    save_state(state)


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    cfg = load_config()
    tz = tzinfo(cfg)
    jobs_cfg = cfg.get("jobs") or {}
    if not isinstance(jobs_cfg, dict) or not jobs_cfg:
        raise SystemExit("No jobs in homelab-scheduler.yml")

    state = load_state()
    jobs_state = state.setdefault("jobs", {})
    if not isinstance(jobs_state, dict):
        jobs_state = {}
        state["jobs"] = jobs_state

    for name, job in jobs_cfg.items():
        if not isinstance(job, dict):
            continue
        if job.get("run_on_start"):
            js = jobs_state.setdefault(name, {})
            run_job(name, job)
            js["last_run_epoch"] = time.time()
            js["last_run_date"] = datetime.now(tz).date().isoformat()
            persist_job(name, js, state)

    print(f"scheduler: running ({len(jobs_cfg)} jobs, tz={tz})", flush=True)

    while not _stop:
        ALIVE.write_text(str(time.time()))
        now_epoch = time.time()
        now_dt = datetime.now(tz)
        state = load_state()
        jobs_state = state.setdefault("jobs", {})
        if not isinstance(jobs_state, dict):
            jobs_state = {}
            state["jobs"] = jobs_state

        for name, job in jobs_cfg.items():
            if not isinstance(job, dict) or job.get("run_on_start"):
                continue
            js = jobs_state.setdefault(name, {})

            due = False
            if job.get("interval_seconds") is not None:
                due = due_interval(js, int(job["interval_seconds"]), now_epoch)
            elif job.get("daily_at"):
                delay = int(job.get("random_delay_seconds") or 0)
                today = now_dt.date().isoformat()
                if delay > 0 and js.get("delay_date") != today:
                    js["delay_seconds"] = random.randint(0, delay)
                    js["delay_date"] = today
                    persist_job(name, js, state)
                hour, minute = parse_daily_at(str(job["daily_at"]))
                target = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
                target += timedelta(seconds=int(js.get("delay_seconds") or 0))
                if js.get("last_run_date") != today and now_dt >= target:
                    due = True

            if not due:
                continue

            run_job(name, job)
            js["last_run_epoch"] = now_epoch
            js["last_run_date"] = now_dt.date().isoformat()
            persist_job(name, js, state)

        for _ in range(TICK_SECONDS):
            if _stop:
                break
            time.sleep(1)

    print("scheduler: stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
