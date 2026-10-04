# DEP-002 — Boot secrets (control plane)

**Status:** implemented in homelab-control.

## Requirements

1. After reboot (or on demand), refill `paths.secrets_runtime` (default `/run/homelab/`).
2. Decrypt all `config/secrets/*` via `render-all-secrets.sh` (SOPS tooling in control).
3. Run each path in `config/boot-secrets.yml` → `render_scripts` (and optional `render_scripts_extra`) under the data checkout.
4. Git changes under `config/secrets/` MUST trigger the same entrypoint before site deploy (SEC-HYB-1).

## Reusability

The control plane MUST NOT import site-specific Python (stack registries, compose scanners, etc.) from a private data repo. It only reads **declared YAML** under `$HOMELAB_DATA_ROOT/config/`. Third parties can use homelab-control with `homelab-data-template/` and their own `boot-secrets.yml` without access to any other operator’s repository.

## Contract (data plane)

| Field | Purpose |
|-------|---------|
| `render_scripts` | Ordered list of `scripts/render-*.py` (and similar) relative to data root |
| `render_scripts_extra` | Optional scripts not tied to a stack manifest |
| `warn_on_fail` | Scripts that may fail without failing the boot job |

| Entrypoint | Location |
|------------|----------|
| Scheduler job | `config/homelab-scheduler.yml` → `kind: control`, `command: boot-secrets` |
| CLI | `engine boot-secrets` or `scripts/boot-secrets.sh` |

Site repos that derive `render_scripts` from `x-homelab` SHOULD keep the committed YAML in sync via CI (see homelab-config `scripts/sync-boot-secrets-manifest.py`).
