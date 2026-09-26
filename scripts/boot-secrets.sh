#!/usr/bin/env bash
# Decrypt SOPS secrets and render stack env files (container scheduler boot job).
set -euo pipefail
CONTROL="${HOMELAB_CONTROL_ROOT:-/opt/homelab/control}"
DATA="${HOMELAB_DATA_ROOT:-/data}"

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
