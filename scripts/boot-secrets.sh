#!/usr/bin/env bash
# Scheduler boot job: delegate to data-plane manifest (config/homelab-scheduler.yml uses data-script).
set -euo pipefail
DATA="${HOMELAB_DATA_ROOT:-/data}"
exec bash "${DATA}/scripts/render-boot-secrets.sh"
