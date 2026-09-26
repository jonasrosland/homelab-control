#!/usr/bin/env bash
# Encrypt a file in place under config/secrets/ with PQ age (containerized sops).
# Usage: sops-encrypt.sh config/secrets/foo.yml
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
target="${1:?usage: sops-encrypt.sh <path-under-repo>}"
# Resolve to path relative to ROOT
if [[ "$target" != /* ]]; then
  abs="${ROOT}/${target}"
else
  abs="$target"
fi
rel="$(realpath --relative-to="$ROOT" "$abs")"
if [[ ! -f "$abs" ]]; then
  echo "Missing $abs" >&2
  exit 1
fi
# Skip if already encrypted
if grep -qE '^sops:|"sops":|sops_version' "$abs" 2>/dev/null || grep -q 'ENC\[AES256_GCM' "$abs" 2>/dev/null; then
  echo "Already SOPS-encrypted: $rel" >&2
  exit 0
fi
export SOPS_WORK_RW=1
# Encrypt in place (sops -e -i)
exec "${ROOT}/scripts/sops-run.sh" -e -i "/work/${rel}"
