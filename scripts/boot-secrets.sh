#!/usr/bin/env bash
# Boot pass: ensure runtime dir, decrypt SOPS, run config/boot-secrets.yml renders (SEC-RUN).
set -euo pipefail
CONTROL="${HOMELAB_CONTROL_ROOT:-/opt/homelab/control}"
SCRIPTS="${CONTROL}/scripts"

RUNTIME="${HOMELAB_SECRETS_RUNTIME:-/run/homelab}"
ensure_runtime_dir() {
  mkdir -p "${RUNTIME}"
  if [[ -w "${RUNTIME}" ]]; then
    chmod 700 "${RUNTIME}" 2>/dev/null || true
    return 0
  fi
  local host="${HOMELAB_SECRETS_RUNTIME_HOST:-}"
  if [[ -z "${host}" ]] || [[ ! -S /var/run/docker.sock ]]; then
    echo "warn: ${RUNTIME} not writable and cannot fix via docker" >&2
    return 0
  fi
  docker run --rm -v "${host}:/target" alpine:3.21 \
    sh -c 'mkdir -p /target && chown 1000:1000 /target && chmod 700 /target'
}

ensure_runtime_dir
bash "${SCRIPTS}/render-all-secrets.sh"
exec python3 "${SCRIPTS}/run-boot-secrets-renders.py"
