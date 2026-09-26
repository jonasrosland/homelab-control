#!/usr/bin/env bash
# Pull and redeploy one stack from git-managed compose under services_root.
set -euo pipefail

STACK="${1:?usage: deploy-stack.sh <stack-name> [--force-recreate]}"
shift || true
FORCE_RECREATE=0
if [[ "${1:-}" == "--force-recreate" ]]; then
  FORCE_RECREATE=1
fi

CONTROL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_ROOT="${HOMELAB_DATA_ROOT:-$CONTROL_ROOT}"
REPO_ROOT="$DATA_ROOT"
export HOMELAB_DATA_ROOT="$DATA_ROOT"
if [[ -z "${SERVICES_ROOT:-}" ]]; then
  SERVICES_ROOT="$(PYTHONPATH="${CONTROL_ROOT}/scripts" python3 -c "import homelab_paths as hp; print(hp.services_root())" 2>/dev/null || true)"
fi
SERVICES_ROOT="${SERVICES_ROOT:-/var/lib/homelab/services}"
REPO_STACK="${REPO_ROOT}/stacks/${STACK}"
DIR="${SERVICES_ROOT}/${STACK}"

# Deploy from services_root so relative volumes and project names stay stable.
if [[ -f "${DIR}/docker-compose.yml" ]]; then
  COMPOSE=(docker compose -f "${DIR}/docker-compose.yml")
elif [[ -f "${DIR}/compose.yml" ]]; then
  COMPOSE=(docker compose -f "${DIR}/compose.yml")
elif [[ -f "${REPO_STACK}/docker-compose.yml" ]]; then
  DIR="${REPO_STACK}"
  COMPOSE=(docker compose -f "${REPO_STACK}/docker-compose.yml")
elif [[ -f "${REPO_STACK}/compose.yml" ]]; then
  DIR="${REPO_STACK}"
  COMPOSE=(docker compose -f "${REPO_STACK}/compose.yml")
else
  echo "Unknown stack: $STACK" >&2
  exit 1
fi
if [[ "$STACK" == "litellm" ]]; then
  python3 "${REPO_ROOT}/scripts/render-litellm-env.py"
fi
if [[ "$STACK" == "llmster" ]]; then
  python3 "${REPO_ROOT}/scripts/render-llmster-build.py"
  set -a
  # shellcheck source=/dev/null
  source "${REPO_ROOT}/.generated/llmster.build.env"
  set +a
  bash "${REPO_ROOT}/scripts/install-llmster-update-timer.sh"
fi
if [[ "$STACK" == "mayberry" ]]; then
  python3 "${REPO_ROOT}/scripts/render-mayberry-build.py"
  set -a
  # shellcheck source=/dev/null
  source "${REPO_ROOT}/.generated/mayberry.build.env"
  set +a
fi
if [[ "$STACK" == "sre" ]]; then
  # Trial bot needs config/secrets/sre.yml. Skip until the token exists.
  if grep -qE '^telegram_source:[[:space:]]*trial' "${REPO_ROOT}/config/sre.yml" 2>/dev/null; then
    if [[ ! -f "${REPO_ROOT}/config/secrets/sre.yml" ]]; then
      echo "sre: trial token not configured yet (copy config/secrets.example/sre.yml); skip deploy" >&2
      exit 0
    fi
  fi
  python3 "${REPO_ROOT}/scripts/render-sre-env.py"
fi
if [[ "$STACK" == "transmission" ]]; then
  python3 "${REPO_ROOT}/scripts/render-transmission-env.py"
fi
if [[ "$STACK" == "invidious" ]]; then
  python3 "${REPO_ROOT}/scripts/render-invidious-env.py"
  # Compose interpolates ${HMAC_KEY} etc from --env-file (service env_file is container-only).
  COMPOSE+=(--env-file /run/homelab/invidious.env)
fi
if [[ "$STACK" == "unpackerr" ]]; then
  python3 "${REPO_ROOT}/scripts/render-unpackerr-env.py"
fi
if [[ "$STACK" == "recyclarr" ]]; then
  mkdir -p "${SERVICES_ROOT}/recyclarr/config"
  # Recyclarr runs as UID 1000; reclaim root-owned leftover dirs without host sudo.
  if [[ "$(stat -c '%u' "${SERVICES_ROOT}/recyclarr/config" 2>/dev/null || echo 0)" != "1000" ]]; then
    docker run --rm -v "${SERVICES_ROOT}/recyclarr/config:/config" alpine:3.20 \
      chown -R 1000:1000 /config
  fi
  python3 "${REPO_ROOT}/scripts/render-recyclarr-config.py"
fi
if [[ "$STACK" == "scrutiny" ]]; then
  python3 "${REPO_ROOT}/scripts/render-scrutiny-config.py"
fi
if [[ "$STACK" == "backrest" ]]; then
  python3 "${REPO_ROOT}/scripts/render-backrest-config.py"
fi
if [[ "$STACK" == "sre" ]]; then
  mkdir -p "${SERVICES_ROOT}/sre/data"
fi

# Local-build stacks keep Dockerfile in git, not under services/.
if [[ ! -f "${DIR}/Dockerfile" && -f "${REPO_STACK}/Dockerfile" ]]; then
  DIR="${REPO_STACK}"
  if [[ -f "${DIR}/docker-compose.yml" ]]; then
    COMPOSE=(docker compose -f "${DIR}/docker-compose.yml")
  else
    COMPOSE=(docker compose -f "${DIR}/compose.yml")
  fi
fi

cd "$DIR"
if [[ "$STACK" == "sre" ]]; then
  "${COMPOSE[@]}" pull socket-proxy
  "${COMPOSE[@]}" build --pull sre
elif [[ "$STACK" == "llmster" ]]; then
  "${COMPOSE[@]}" build --pull lmstudio
elif [[ "$STACK" == "auroratube" ]]; then
  "${COMPOSE[@]}" build --pull kidtube
elif [[ "$STACK" == "whattoplay" ]]; then
  WTP="${SERVICES_ROOT}/whattoplay/whattoplay"
  if [[ -d "${WTP}/.git" ]]; then
    git -C "${WTP}" pull --ff-only || true
  fi
  # Dev override bind-mounts the tree over the image — keep it out of production.
  if [[ -f "${WTP}/docker-compose.override.yml" ]]; then
    mv -f "${WTP}/docker-compose.override.yml" "${WTP}/docker-compose.override.yml.bak.prod"
  fi
  "${COMPOSE[@]}" build --pull web
elif [[ "$STACK" == "mayberry" ]]; then
  "${COMPOSE[@]}" build --pull mayberry
elif [[ "$STACK" == "loadvid" ]]; then
  "${COMPOSE[@]}" build --pull loadvid
else
  "${COMPOSE[@]}" pull
fi
# Publish self-built images to Docker Hub when config/images.yml lists the stack.
case "$STACK" in
  mayberry|sre|llmster)
    python3 "${CONTROL_ROOT}/scripts/push-homelab-image.py" --stack "$STACK" --skip-missing-secret
    ;;
esac
UP=(up -d --remove-orphans)
if [[ "$FORCE_RECREATE" == "1" ]]; then
  UP+=(--force-recreate --no-deps)
fi
"${COMPOSE[@]}" "${UP[@]}"
# Optional per-stack health wait override from config/deploy.yml
HEALTH_TIMEOUT="$(python3 - <<PY
import yaml
from pathlib import Path
cfg = yaml.safe_load(Path("${REPO_ROOT}/config/deploy.yml").read_text()) or {}
stack = (cfg.get("stacks") or {}).get("${STACK}") or {}
global_t = float((cfg.get("health") or {}).get("timeout_seconds") or 120)
t = stack.get("health_timeout_seconds")
print(int(t) if t is not None else int(global_t))
PY
)"
python3 "${CONTROL_ROOT}/scripts/wait-stack-healthy.py" --stack "$STACK" --timeout "${HEALTH_TIMEOUT}"
if [[ "$STACK" == "scrutiny" ]]; then
  "${REPO_ROOT}/scripts/fix-scrutiny-perms.sh"
fi
if [[ "$STACK" == "backrest" ]]; then
  bash "${REPO_ROOT}/scripts/install-restic-timers.sh"
fi
if [[ "$STACK" == "recyclarr" ]]; then
  # Cron container stays up; run an immediate sync after deploy.
  "${COMPOSE[@]}" exec -T recyclarr recyclarr sync \
    || "${COMPOSE[@]}" run --rm --entrypoint recyclarr recyclarr sync \
    || true
fi
if [[ "$STACK" == "cleanuparr" ]]; then
  # Idempotent: queue cleaner delete_private + Pushover (same destination as Radarr).
  python3 "${REPO_ROOT}/scripts/apply-cleanuparr-config.py"
fi
echo "Deployed $STACK"
