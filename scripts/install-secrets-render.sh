#!/usr/bin/env bash
# Install tmpfiles.d + systemd oneshot for /run/homelab secret re-render after reboot.
# Uses Docker+nsenter so no interactive sudo is required on the deploy host.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="${ROOT}/systemd/homelab-secrets-render.service"

docker run --rm \
  -v /run:/host-run \
  -v /etc/tmpfiles.d:/etc/tmpfiles.d \
  -v /etc/systemd/system:/etc/systemd/system \
  -v "${UNIT_SRC}:/unit:ro" \
  alpine:3.21 \
  sh -c '
    mkdir -p /host-run/homelab
    chown 1000:1000 /host-run/homelab
    chmod 700 /host-run/homelab
    echo "d /run/homelab 0700 jonas jonas -" > /etc/tmpfiles.d/homelab.conf
    chmod 644 /etc/tmpfiles.d/homelab.conf
    cp /unit /etc/systemd/system/homelab-secrets-render.service
    chmod 644 /etc/systemd/system/homelab-secrets-render.service
  '

host_systemctl() {
  docker run --rm --privileged --pid=host alpine:3.21 \
    nsenter -t 1 -m -u -i -n systemctl "$@"
}

host_systemctl daemon-reload
host_systemctl enable homelab-secrets-render.service
host_systemctl start homelab-secrets-render.service || true
host_systemctl --no-pager --full status homelab-secrets-render.service || true
echo "Installed homelab-secrets-render.service"
