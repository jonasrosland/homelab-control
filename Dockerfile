# homelab-control engine — git pull, compose deploy, SOPS helpers, restic runner.
# Build: docker build -t homelab/control:local .
# Push:  see config/images.example.yml

FROM docker:27.4.1-cli AS dockercli

FROM python:3.12-slim-bookworm

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    git \
    openssh-client \
    rclone \
  && rm -rf /var/lib/apt/lists/*

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose \
  /usr/local/libexec/docker/cli-plugins/docker-compose

RUN pip install --no-cache-dir pyyaml jsonschema==4.23.0 \
  && mkdir -p /home/runner \
  && echo "runner:x:1000:1000:runner:/home/runner:/bin/bash" >> /etc/passwd \
  && chown -R 1000:1000 /home/runner

ENV HOMELAB_CONTROL_ROOT=/opt/homelab/control \
    HOMELAB_CONTROL_CONTAINER=1 \
    PATH="/opt/homelab/control/scripts:${PATH}"

COPY scripts /opt/homelab/control/scripts
RUN chmod +x /opt/homelab/control/scripts/*.sh 2>/dev/null || true

WORKDIR /data

ENTRYPOINT ["/opt/homelab/control/scripts/entrypoint.sh"]
CMD ["deploy-from-git"]
