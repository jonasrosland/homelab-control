#!/usr/bin/env bash
# Run pinned age/age-keygen in Docker. Usage: age-run.sh age-keygen|age [args...]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_ROOT="$("${SCRIPT_DIR}/homelab-data-root.sh")"
CFG="${DATA_ROOT}/config/sops.yml"
cmd="${1:?usage: age-run.sh age-keygen|age [args...]}"
shift || true

if [[ ! -f "${CFG}" ]]; then
  echo "Missing ${CFG}" >&2
  exit 1
fi

image="$(python3 -c "import yaml; print(yaml.safe_load(open('${CFG}'))['age_image'])")"
entry="$cmd"
if [[ "$cmd" != "age" && "$cmd" != "age-keygen" ]]; then
  echo "usage: age-run.sh age-keygen|age [args...]" >&2
  exit 2
fi

mounts=()
if [[ -n "${AGE_OUT_DIR:-}" ]]; then
  mkdir -p "${AGE_OUT_DIR}"
  mounts+=(-v "${AGE_OUT_DIR}:/out")
fi

exec docker run --rm \
  -u "$(id -u):$(id -g)" \
  "${mounts[@]}" \
  --entrypoint "$entry" \
  "${image}" \
  "$@"
