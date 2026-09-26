#!/usr/bin/env bash
# Encrypt a file in place under config/secrets/ with PQ age (containerized sops).
# Usage: sops-encrypt.sh config/secrets/foo.yml
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_ROOT="$("${SCRIPT_DIR}/homelab-data-root.sh")"
target="${1:?usage: sops-encrypt.sh <path-under-data-repo>}"
if [[ "$target" != /* ]]; then
  abs="${DATA_ROOT}/${target}"
else
  abs="$target"
fi
rel="$(realpath --relative-to="${DATA_ROOT}" "$abs")"
if [[ ! -f "$abs" ]]; then
  echo "Missing $abs" >&2
  exit 1
fi
if grep -qE '^sops:|"sops":|sops_version' "$abs" 2>/dev/null || grep -q 'ENC\[AES256_GCM' "$abs" 2>/dev/null; then
  echo "Already SOPS-encrypted: $rel" >&2
  exit 0
fi
export SOPS_WORK_RW=1
exec "${SCRIPT_DIR}/sops-run.sh" -e -i "/work/${rel}"
