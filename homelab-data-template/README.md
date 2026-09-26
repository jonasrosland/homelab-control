# homelab-data template

Starter layout for the **data plane** used with [homelab-control](https://github.com/jonasrosland/homelab-control).

Copy these files into your private data repo:

- `config/homelab.example.yml` → `config/homelab.yml` (paths to data + control checkouts)
- `config/deploy.example.yml` → `config/deploy.yml` (stack path triggers)
- `stacks/glance/` — example compose stack

Add `config/secrets/` from your own SOPS templates, `restic/restic.yml`, and site-specific scripts under `scripts/` (renders, `link-stacks.sh`, OPNsense sync, etc.).

Set on the deploy host:

```bash
export HOMELAB_DATA_ROOT=/path/to/your/data/checkout
# Poller runs from homelab-control; see homelab-control README.
```
