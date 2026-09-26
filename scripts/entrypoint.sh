#!/usr/bin/env bash
# Dispatch argv to control scripts (default: deploy-from-git poller).
set -euo pipefail
CONTROL="${HOMELAB_CONTROL_ROOT:-/opt/homelab/control}"
SCRIPTS="${CONTROL}/scripts"

if [[ -n "${HOMELAB_DATA_ROOT:-}" && -d "${HOMELAB_DATA_ROOT}" ]]; then
  git config --global --add safe.directory "${HOMELAB_DATA_ROOT}" 2>/dev/null || true
fi

cmd="${1:-deploy-from-git}"
shift || true

resolve() {
  local name="$1"
  if [[ -f "${SCRIPTS}/${name}" ]]; then
    echo "${SCRIPTS}/${name}"
    return 0
  fi
  if [[ -f "${SCRIPTS}/${name}.py" ]]; then
    echo "${SCRIPTS}/${name}.py"
    return 0
  fi
  if [[ -f "${SCRIPTS}/${name}.sh" ]]; then
    echo "${SCRIPTS}/${name}.sh"
    return 0
  fi
  return 1
}

path="$(resolve "$cmd")" || {
  echo "unknown homelab-control command: $cmd" >&2
  exit 2
}

case "$path" in
  *.py) exec python3 "$path" "$@" ;;
  *) exec bash "$path" "$@" ;;
esac
