#!/usr/bin/env bash
# Decrypt SOPS secrets and render stack env files (container scheduler boot job).
set -euo pipefail
CONTROL="${HOMELAB_CONTROL_ROOT:-/opt/homelab/control}"
DATA="${HOMELAB_DATA_ROOT:-/data}"
RUNTIME="/run/homelab"

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

bash "${CONTROL}/scripts/render-all-secrets.sh"

run_optional() {
  local script="$1"
  if [[ -f "$script" ]]; then
    python3 "$script" || echo "warn: $script failed (secret may not be migrated yet)" >&2
  fi
}

run_optional "${DATA}/scripts/render-litellm-env.py"
run_optional "${DATA}/scripts/render-transmission-env.py"
run_optional "${DATA}/scripts/render-invidious-env.py"
run_optional "${DATA}/scripts/render-unpackerr-env.py"
run_optional "${DATA}/scripts/render-recyclarr-config.py"
run_optional "${DATA}/scripts/render-sre-env.py"
run_optional "${DATA}/scripts/render-backrest-config.py"
