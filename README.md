# homelab-control

Shareable **control plane** for git-driven Docker Compose deploys: poller, stack driver, SOPS helpers, health wait, restic runner, upgrade/ops job runners, and systemd units.

Your stacks, secrets, DNS, and paths live in a separate **data plane** repo (private).

## Repositories

| Repo | Role |
|------|------|
| **homelab-control** (this) | Deploy engine, shared scripts, docs |
| **homelab-data** (yours) | `config/deploy.yml`, `stacks/`, SOPS secrets, site scripts |

See [docs/control-data-plane.md](docs/control-data-plane.md) and [homelab-data-template/](homelab-data-template/).

## Requirements

- Linux host with Docker Compose v2
- Python 3 + PyYAML
- Data checkout with `config/deploy.yml` and `stacks/`

## Split-repo layout

```bash
git clone git@github.com:jonasrosland/homelab-control.git /opt/homelab/control
git clone git@github.com:YOU/homelab-data.git /opt/homelab/data

cd /opt/homelab/control
# Edit systemd/*.service paths if your clones differ, then:
bash scripts/install-deploy-timer.sh
```

`systemd/deploy-from-git.service` sets:

- `WorkingDirectory` → control checkout
- `HOMELAB_DATA_ROOT` → data checkout

The poller **pulls git only in the data repo**; it runs scripts from control.

## Monorepo / dev

If `config/deploy.yml` exists inside the control checkout (legacy single repo), scripts use that tree as the data root. For split operation, set:

```bash
export HOMELAB_DATA_ROOT=/opt/homelab/data
```

Manual run:

```bash
HOMELAB_DATA_ROOT=/path/to/data python3 scripts/deploy-from-git.py
HOMELAB_DATA_ROOT=/path/to/data bash scripts/deploy-stack.sh glance
```

Telegram and log prefixes use `notify.host_label` in `config/homelab.yml` or `HOMELAB_NOTIFY_LABEL`.

## Docs

- [Control vs data plane](docs/control-data-plane.md)
- [Deploy observability](docs/deploy-observability.md)
- [Why build this](docs/whydidibuildthis.md)

## Status

**Phase 1:** dual-root paths via `scripts/homelab_paths.py`. Site-specific render hooks stay in the data repo (`deploy-stack.sh` calls `data/scripts/render-*.py` when present).
