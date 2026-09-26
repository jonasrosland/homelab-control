#!/usr/bin/env python3
"""Retag and push self-built homelab images to Docker Hub.

Reads config/images.yml. Credentials: config/secrets/dockerhub.yml (SOPS).
Usage:
  scripts/push-homelab-image.py --stack auroratube
  scripts/push-homelab-image.py --image age
  scripts/push-homelab-image.py --all
"""
from __future__ import annotations

import argparse
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
IMAGES_CFG = ROOT / "config" / "images.yml"
BUILD_ENV = hp.generated_dir() / "llmster.build.env"

sys.path.insert(0, str(hp.CONTROL_ROOT / "scripts"))
from sops_secrets import load_yaml as load_secret_yaml  # noqa: E402


def load_images_cfg() -> dict:
    if not IMAGES_CFG.is_file():
        raise SystemExit(f"Missing {IMAGES_CFG}")
    cfg = yaml.safe_load(IMAGES_CFG.read_text()) or {}
    if not isinstance(cfg, dict):
        raise SystemExit(f"Expected mapping in {IMAGES_CFG}")
    return cfg


def remote_ref(namespace: str, local: str) -> str:
    """homelab/foo:tag → <namespace>/foo:tag (namespace from config/images.yml)."""
    if "/" not in local:
        raise SystemExit(f"Unexpected local image (need repo/name:tag): {local}")
    _, rest = local.split("/", 1)
    return f"{namespace}/{rest}"


def resolve_local(key: str, entry: dict) -> str:
    local = str(entry.get("local") or "").strip()
    if not local:
        raise SystemExit(f"images.{key}: missing local")
    if key == "llmster" and BUILD_ENV.is_file():
        for line in BUILD_ENV.read_text().splitlines():
            if line.startswith("LLMSTER_IMAGE="):
                resolved = line.split("=", 1)[1].strip()
                if resolved:
                    return resolved
    if key == "mayberry":
        mayberry_env = ROOT / ".generated" / "mayberry.build.env"
        if mayberry_env.is_file():
            for line in mayberry_env.read_text().splitlines():
                if line.startswith("MAYBERRY_IMAGE="):
                    resolved = line.split("=", 1)[1].strip()
                    if resolved:
                        return resolved
    return local


def docker_login(username: str, token: str) -> None:
    proc = subprocess.run(
        ["docker", "login", "-u", username, "--password-stdin"],
        input=token.encode(),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"").decode(errors="replace").strip()
        raise SystemExit(f"docker login failed: {err}")


def docker_logout() -> None:
    subprocess.run(["docker", "logout"], capture_output=True, check=False)


def image_exists_locally(ref: str) -> bool:
    proc = subprocess.run(
        ["docker", "image", "inspect", ref],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def push_one(local: str, remote: str) -> None:
    if not image_exists_locally(local):
        raise SystemExit(f"Local image missing (build first): {local}")
    subprocess.run(["docker", "tag", local, remote], check=True)
    print(f"push: {local} → {remote}", flush=True)
    subprocess.run(["docker", "push", remote], check=True)


def entries_to_push(cfg: dict, *, stack: str | None, image: str | None, all_: bool) -> list[tuple[str, str]]:
    images = cfg.get("images") or {}
    if not isinstance(images, dict):
        raise SystemExit("config/images.yml: images must be a mapping")
    namespace = str(cfg.get("namespace") or "").strip()
    if not namespace:
        raise SystemExit("config/images.yml: namespace required")

    keys: list[str]
    if all_:
        keys = [k for k, v in images.items() if isinstance(v, dict) and v.get("push", True)]
    elif stack:
        if stack not in images:
            raise SystemExit(f"No images.yml entry for stack {stack}")
        keys = [stack]
    elif image:
        if image not in images:
            raise SystemExit(f"No images.yml entry for image key {image}")
        keys = [image]
    else:
        raise SystemExit("Specify --stack, --image, or --all")

    out: list[tuple[str, str]] = []
    for key in keys:
        entry = images[key]
        if not isinstance(entry, dict):
            raise SystemExit(f"images.{key}: expected mapping")
        if not entry.get("push", True):
            print(f"push: skip {key} (push: false)", flush=True)
            continue
        local = resolve_local(key, entry)
        out.append((local, remote_ref(namespace, local)))
    return out


def load_credentials(cfg: dict) -> tuple[str, str]:
    secrets_rel = str(cfg.get("secrets") or "config/secrets/dockerhub.yml")
    name = Path(secrets_rel).name
    src = ROOT / secrets_rel
    if not src.is_file():
        raise SystemExit(
            f"Missing {src} — copy config/secrets.example/dockerhub.yml, "
            f"set token, then ./scripts/sops-encrypt.sh {secrets_rel}"
        )
    data = load_secret_yaml(name)
    username = str(data.get("username") or "").strip()
    token = str(data.get("token") or "").strip()
    if not username or not token or "REPLACE_WITH_" in token:
        raise SystemExit(f"Set username/token in {src}")
    return username, token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", help="Compose stack key in config/images.yml")
    parser.add_argument("--image", help="Image key (e.g. age) in config/images.yml")
    parser.add_argument("--all", action="store_true", help="Push every images.*.push: true")
    parser.add_argument(
        "--skip-missing-secret",
        action="store_true",
        help="Exit 0 if dockerhub secret is absent (deploy hook)",
    )
    args = parser.parse_args()
    if sum(bool(x) for x in (args.stack, args.image, args.all)) != 1:
        parser.error("Specify exactly one of --stack, --image, --all")

    cfg = load_images_cfg()
    try:
        username, token = load_credentials(cfg)
    except SystemExit as exc:
        if args.skip_missing_secret and (
            "Missing" in str(exc) or "REPLACE_WITH_" in str(exc) or "Set username" in str(exc)
        ):
            print(f"push: skip ({exc})", flush=True)
            return 0
        raise

    targets = entries_to_push(cfg, stack=args.stack, image=args.image, all_=args.all)
    if not targets:
        print("push: nothing to push", flush=True)
        return 0

    docker_login(username, token)
    try:
        for local, remote in targets:
            push_one(local, remote)
    finally:
        docker_logout()
    print(f"push: done ({len(targets)} image(s))", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
