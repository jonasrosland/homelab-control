#!/usr/bin/env bash
# Run digest-pinned sops in Docker. Usage: sops-run.sh [sops args...]
# Mounts the data-plane checkout as /work (secrets and config/sops.yml live there).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_LOGICAL="${HOMELAB_DATA_ROOT:-$("${SCRIPT_DIR}/homelab-data-root.sh")}"
DATA_MOUNT="${HOMELAB_DATA_HOST:-${DATA_LOGICAL}}"
CFG="${DATA_LOGICAL}/config/sops.yml"
AGE_KEY_DEFAULT="/var/lib/homelab/secrets/age.key"

if [[ ! -f "${CFG}" ]]; then
  echo "Missing ${CFG} (set HOMELAB_DATA_ROOT to your data checkout)" >&2
  exit 1
fi

image="$(python3 -c "import yaml; print(yaml.safe_load(open('${CFG}'))['sops_image'])")"
age_key_logical="${SOPS_AGE_KEY_FILE:-$(python3 -c "import yaml; print(yaml.safe_load(open('${CFG}')).get('age_key_file','${AGE_KEY_DEFAULT}'))")}"
age_key_mount="${HOMELAB_AGE_KEY_HOST:-${age_key_logical}}"

mounts=(-v "${DATA_MOUNT}:/work:ro")
env_args=()
if [[ "${SOPS_WORK_RW:-}" == "1" ]]; then
  mounts=(-v "${DATA_MOUNT}:/work")
fi
if [[ -f "${age_key_logical}" ]]; then
  mounts+=(-v "${age_key_mount}:/age.key:ro")
  env_args+=(-e "SOPS_AGE_KEY_FILE=/age.key")
fi
if [[ -n "${SOPS_OUT_DIR:-}" ]]; then
  out_mount="${SOPS_OUT_DIR}"
  if [[ "${SOPS_OUT_DIR}" == /run/homelab* && -n "${HOMELAB_SECRETS_RUNTIME_HOST:-}" ]]; then
    out_mount="${HOMELAB_SECRETS_RUNTIME_HOST}"
  fi
  mkdir -p "${out_mount}"
  mounts+=(-v "${out_mount}:/out")
fi

exec docker run --rm \
  -u "$(id -u):$(id -g)" \
  -w /work \
  "${mounts[@]}" \
  "${env_args[@]}" \
  "${image}" \
  "$@"
