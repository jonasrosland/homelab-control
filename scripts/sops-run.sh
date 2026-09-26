#!/usr/bin/env bash
# Run digest-pinned sops in Docker. Usage: sops-run.sh [sops args...]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="${ROOT}/config/sops.yml"
AGE_KEY_DEFAULT="/var/lib/homelab/secrets/age.key"

image="$(python3 -c "import yaml; print(yaml.safe_load(open('${CFG}'))['sops_image'])")"
age_key="${SOPS_AGE_KEY_FILE:-$(python3 -c "import yaml; print(yaml.safe_load(open('${CFG}')).get('age_key_file','${AGE_KEY_DEFAULT}'))")}"

mounts=(-v "${ROOT}:/work:ro")
env_args=()
# Writable override for encrypt in-place under work when caller mounts rw via SOPS_WORK_RW=1
if [[ "${SOPS_WORK_RW:-}" == "1" ]]; then
  mounts=(-v "${ROOT}:/work")
fi
if [[ -f "${age_key}" ]]; then
  mounts+=(-v "${age_key}:/age.key:ro")
  env_args+=(-e "SOPS_AGE_KEY_FILE=/age.key")
fi
# Allow writing decrypted output to host tmpfs
if [[ -n "${SOPS_OUT_DIR:-}" ]]; then
  mkdir -p "${SOPS_OUT_DIR}"
  mounts+=(-v "${SOPS_OUT_DIR}:/out")
fi

exec docker run --rm \
  -u "$(id -u):$(id -g)" \
  -w /work \
  "${mounts[@]}" \
  "${env_args[@]}" \
  "${image}" \
  "$@"
