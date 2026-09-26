#!/usr/bin/env bash
# Install the host evidence reader for the Rosland SRE bot (user systemd, no sudo).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${HOME}/.config/systemd/user"
mkdir -p "$UNIT_DIR"

ln -sfn "${REPO_ROOT}/systemd/ops-read.service" "${UNIT_DIR}/ops-read.service"
echo "linked ${UNIT_DIR}/ops-read.service"

systemctl --user daemon-reload
systemctl --user enable --now ops-read.service
systemctl --user --no-pager --full status ops-read.service || true
