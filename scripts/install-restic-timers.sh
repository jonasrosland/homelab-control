#!/usr/bin/env bash
# Restic timers: dump/copy before Backrest's 03:00 plan. Nightly restic and
# weekly check now live in Backrest (config/backrest.yml).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${HOME}/.config/systemd/user"
mkdir -p "$UNIT_DIR"

for unit in restic-prepare.service restic-prepare.timer \
            restic-backup.service restic-backup.timer \
            restic-check.service restic-check.timer; do
  ln -sfn "${REPO_ROOT}/systemd/${unit}" "${UNIT_DIR}/${unit}"
  echo "linked ${UNIT_DIR}/${unit}"
done

systemctl --user daemon-reload
systemctl --user enable --now restic-prepare.timer
systemctl --user disable --now restic-backup.timer restic-check.timer || true
systemctl --user list-timers 'restic-*' --no-pager || true

if command -v loginctl >/dev/null && loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q Linger=no; then
  echo "User lingering is off. Timers pause when you log out."
  echo "Once (needs sudo): sudo loginctl enable-linger $USER"
fi
