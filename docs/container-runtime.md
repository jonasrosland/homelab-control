# Container runtime

Run the control plane as a **single Docker image** so the host only needs Docker (no pinned Python/bash/git).

## Image contents

- Python 3.12 + PyYAML
- git, OpenSSH client, bash, restic, rclone
- Docker CLI + Compose v2 plugin (talks to the host via `/var/run/docker.sock`)
- Control scripts under `/opt/homelab/control/scripts`

## Build and publish

```bash
docker build -t homelab/control:local .
docker tag homelab/control:local jonasrosland/homelab-control:main
docker push jonasrosland/homelab-control:main
```

Pin the tag or digest in the **data plane** `config/homelab.yml`:

```yaml
control:
  mode: container
  image: jonasrosland/homelab-control:main
```

## Data plane wiring

The data repo ships `stacks/homelab-control/docker-compose.yml` and `scripts/homelab-control-run.sh`, which:

1. Renders `stacks/homelab-control/.generated.env` from `config/homelab.yml`, `config/homelab-container.yml`, and `config/saturn.yml`
2. Runs `docker compose run --rm engine <command>` (default `deploy-from-git`)

systemd on the deploy host calls `homelab-control-run.sh deploy-from-git` — no git clone of homelab-control on the host.

## Git mode (legacy)

Set `control.mode: git` and `paths.control_root` to a host checkout. Host Python/bash required.
