# Agent guide — homelab-control

Control-plane repo only. Site stacks and secrets are in the **data plane** (your private repo).

## Paths

- `HOMELAB_DATA_ROOT` — git checkout with `config/deploy.yml` and `stacks/` (required when control and data are separate).
- Control scripts live under this repo; `scripts/homelab_paths.py` resolves both roots.

## Do

- Change deploy engine, health wait, SOPS framework, and generic docs here.
- Keep data-plane examples in `homelab-data-template/`.
- Test with `HOMELAB_DATA_ROOT=/path/to/data python3 scripts/deploy-from-git.py --stack …`.

## Do not

- Commit age private keys or plaintext secrets.
- Put site DNS, LAN IPs, hostnames, or domain policy in this repo (data plane).

## Data repo still owns

- `config/deploy.yml`, per-stack YAML, SOPS files
- `scripts/link-stacks.sh`, edge DNS sync, `render-*-env.py`, remote-host helpers
- `restic/restic.yml`

After editing control plane, bump data repo’s `homelab.yml` `git.control_ref` when split (future).
