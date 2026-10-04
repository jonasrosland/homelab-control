# homelab-data template

Starter layout for the **data plane** used with [homelab-control](https://github.com/jonasrosland/homelab-control).

Copy these files into your private data repo:

- `config/homelab.example.yml` → `config/homelab.yml` (paths to data + control checkouts)
- `config/deploy.example.yml` → `config/deploy.yml` (stack path triggers)
- `stacks/glance/` — example compose stack

Add `config/secrets/` from SOPS templates (see `config/secrets.example/`).

**Backrest (optional):** copy `stacks/backrest/`, deploy, open the UI, create a restic repo matching `integrations.backrest.repo_id`, author backup plans (`homelab`, `homelab-predeploy`, `homelab-manual`). Back up `{services_root}/backrest/config/` — `config.json` is policy SSOT ([DEP-003](../docs/DEP-003-backrest-integration.md)).

Site-specific scripts (renders, DNS sync, etc.) stay in your data repo; homelab-control derives Backrest API URLs from compose `x-homelab`.

Set on the deploy host:

```bash
export HOMELAB_DATA_ROOT=/path/to/your/data/checkout
# Poller runs from homelab-control; see homelab-control README.
```
