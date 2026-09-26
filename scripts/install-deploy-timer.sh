#!/usr/bin/env bash
# Install the Phase 3 deploy poller (user systemd, no sudo).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${HOME}/.config/systemd/user"
mkdir -p "$UNIT_DIR"

for unit in deploy-from-git.service deploy-from-git.timer; do
  ln -sfn "${REPO_ROOT}/systemd/${unit}" "${UNIT_DIR}/${unit}"
  echo "linked ${UNIT_DIR}/${unit}"
done

systemctl --user daemon-reload
systemctl --user enable --now deploy-from-git.timer
systemctl --user list-timers 'deploy-from-git*' --no-pager || true

if command -v loginctl >/dev/null && loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q Linger=no; then
  echo "User lingering is off. Timers pause when you log out."
  echo "Once (needs sudo): sudo loginctl enable-linger $USER"
fi
