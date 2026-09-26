#!/usr/bin/env bash
# Run digest-pinned sops in Docker. Usage: sops-run.sh [sops args...]
# Mounts the data-plane checkout as /work (secrets and config/sops.yml live there).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_ROOT="$("${SCRIPT_DIR}/homelab-data-root.sh")"
CFG="${DATA_ROOT}/config/sops.yml"
AGE_KEY_DEFAULT="/var/lib/homelab/secrets/age.key"

if [[ ! -f "${CFG}" ]]; then
  echo "Missing ${CFG} (set HOMELAB_DATA_ROOT to your data checkout)" >&2
  exit 1
fi

image="$(python3 -c "import yaml; print(yaml.safe_load(open('${CFG}'))['sops_image'])")"
age_key="${SOPS_AGE_KEY_FILE:-$(python3 -c "import yaml; print(yaml.safe_load(open('${CFG}')).get('age_key_file','${AGE_KEY_DEFAULT}'))")}"

mounts=(-v "${DATA_ROOT}:/work:ro")
env_args=()
if [[ "${SOPS_WORK_RW:-}" == "1" ]]; then
  mounts=(-v "${DATA_ROOT}:/work")
fi
if [[ -f "${age_key}" ]]; then
  mounts+=(-v "${age_key}:/age.key:ro")
  env_args+=(-e "SOPS_AGE_KEY_FILE=/age.key")
fi
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
