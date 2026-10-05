# DEP-004 — Generic deploy-stack driver

**Status:** implemented in homelab-config data checkout (`scripts/deploy_stack_driver.py`); shipped in the control image via `/data` bind mount.

## MUST

1. **Single shell entry:** `scripts/deploy-stack.sh` orchestrates render → hooks → `deploy.image` → `compose up` → health → hooks. Site-specific branches live in **`deploy_stack_driver.py`** and **`x-homelab.deploy`**, not inline shell stack names.
2. **Special drivers:** `deploy.driver: homelab-control-scheduler` (or stack key `homelab-control`) runs scheduler redeploy only — no generic compose pull/up.
3. **Compose layout:** resolves `service_dir`, `work_dir`, and `compose_file` for repo-build stacks.
4. **Nested git checkouts:** `git-sync-nested-checkout.sh` (GitHub HTTPS → SSH using mounted `~/.ssh`).

## SSOT (data plane)

Driver / hooks: `stacks/*/docker-compose.yml`. Path roots: `config/homelab.yml`. Control image pin: `config/homelab.yml` + site `site-profile.md`.

Normative copy with requirement IDs: homelab-config `docs/design/homelab-control/DEP-004-deploy-stack-driver.md`.
