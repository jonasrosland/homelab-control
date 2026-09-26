# Why did I build this?

The deploy pipeline’s deploy path is custom on purpose: **git on `main` is the source of truth**, deploy host pulls and applies only what changed, and a pile of **homelab-specific policy** runs before and after `docker compose up`. This note records why that exists and how it compares to off-the-shelf tools (Komodo, configuration management, and “Compose GitOps” projects).

For how it works day to day, see [README.md](./README.md), [AGENTS.md](./AGENTS.md), `config/deploy.yml`, and the blog series [part 1 — git and deploy](./blog/docker-automation-part-1-git-and-deploy.md).

## What we actually built

| Piece | Role |
|-------|------|
| `scripts/deploy-from-git.py` | Poll `origin/main`, map changed paths → stacks, pre-deploy restic, dirty-tree guard, pending retries, Telegram on failure |
| `config/deploy.yml` | Per-stack path triggers, `--force-recreate`, health wait overrides, custom `deploy_script` (e.g. Neptune) |
| `scripts/deploy-stack.sh` | Per-stack render → pull/build → `compose up` → health wait → post-up hooks |
| `scripts/link-stacks.sh` | Symlink `stacks/*` into `/var/lib/homelab/services` |
| SOPS + `render-*-env.py` | Decrypt into `/run/homelab/` (tmpfs), not long-lived `.env` under the repo |
| `scripts/sync-opnsense-dns.py` | Push DNS/Caddy-related state when config changes |

The **goal** is idempotent steady state: desired config in git → apply until deploy host matches. The **implementation** is an **event-driven orchestrator** (new commit + path rules), not a full node catalog that reconciles every N minutes.

## Komodo (skipped for now)

[Komodo](https://github.com/moghtech/komodo) (Core + Periphery) is a strong **compose control plane**: Git-linked stacks, webhooks, multi-host agents, UI.

It could replace **polling** and **manual compose** on the deploy host, but not the whole pipeline without keeping our scripts anyway:

- Path → stack mapping lives in `config/deploy.yml`, not in Komodo’s stack model.
- Pre-deploy **restic**, **SOPS → `/run/homelab/`**, per-stack build/push/health/hooks, OPNsense sync, and **Neptune over SSH** are all custom.
- Repo norms (**clean deploy host clone**, edit from your dev machine only) conflict with UI-driven compose edits unless Komodo only triggers `git pull` + existing scripts.

**Practical stance:** Komodo as a front-end is possible; **replacing** `deploy-stack.sh` wholesale is not. Running **both** a poller and Komodo redeploy without coordination would be two masters.

## Puppet, Ansible, and “real” configuration management

Configuration management (Puppet, Ansible, Chef, Salt) excels at **node baseline**: packages, files, systemd, “ensure this service enabled.”

Our pipeline is narrower and more **Docker- and git-centric**:

| Homelab pattern | CM analogue |
|-----------------|-------------|
| Symlinks under `/var/lib/homelab/services` | `file` / `state: link` |
| Rendered secrets on tmpfs | Templates or `exec` with checksum guards (secrets stay awkward in catalog) |
| `docker compose up -d` | Community modules or guarded `command` |
| systemd deploy/restic timers | `systemd` unit resources |

**Path-based partial deploy** (“only jellyfin if `config/jellyfin.yml` changed”) is **not** a first-class CM feature; you still need git diff logic or you redeploy everything on every commit.

Ansible often fits homelabs better than Puppet (agentless playbooks ≈ structured `deploy-stack.sh`). NixOS is idempotent by design but a different OS commitment.

**Takeaway:** We are not accidentally reinventing Puppet; we built a **git-native, compose-first deploy pipeline** with idempotent *steps* inside imperative scripts. CM could formalize host baseline; it would not replace `deploy-from-git.py` + `deploy.yml` without reimplementing that policy elsewhere.

## Similar tools (Compose GitOps)

There is an active niche: **“Flux/Argo for Docker Compose”** — watch git, reconcile with `docker compose`, sometimes only changed projects. None match this pipeline line-for-line; several overlap the core loop.

### Closest to this repo

| Tool | Why it feels familiar | Gaps vs our pipeline |
|------|------------------------|----------------------|
| [compose-sync](https://github.com/aottr/compose-sync) | `stacks/` + **`inventory.yml`** (host → stacks) + **git diff → deploy only changed stacks** | No SOPS/tmpfs render, pre-restic, per-stack hooks, OPNsense/Neptune |
| [gitcompose](https://github.com/foosinn/gitcompose) | Monorepo subdirs, **deploy changed dirs**, poll/webhook; **`sops -d` preprocessor**; optional `files.yaml` | No central path map (`config/` → stack X); no health gate / dirty-tree / Telegram |
| [Doco-CD](https://doco.cd/) ([kimdre/doco-cd](https://github.com/kimdre/doco-cd)) | Mature GitOps CD; **monorepo via multi-doc `.doco-cd.yml`**; **SOPS at deploy**; poll/webhook; cron, metrics | Stack-specific logic moves into many deploy docs or side scripts; one daemon per host unless you add SSH |
| [ConOps](https://github.com/anuragxxd/conops) | Git loop, **self-heal / drift**, deploy keys | Less monorepo path wiring; thinner secrets story |
| [docker-git-deploy](https://github.com/linksawakening/docker-git-deploy) | Pull timer, **`compose up -d --wait`**, deployment-repo pattern | Often one stack per repo; host `.env` secrets |
| [deeplo](https://github.com/jancernik/deeplo) | **Path-aware monorepo**, poll/webhook, **agentless SSH** | Remote compose only; hooks stay custom |
| [SID](https://github.com/codex-group-wa/SID) | Webhook → **only dirs whose compose changed** | Minimal; no backup gate or health orchestration |
| [WireOps](https://github.com/wireops/wireops) | “Flux for compose”: **multi-host workers**, **SOPS+age**, webhooks, rollback | Early project; long-tail hooks still yours |

**compose-sync** and **gitcompose** are closest in spirit to `deploy-from-git.py` + path-aware deploy. **Doco-CD** is the most production-shaped if you want maintained GitOps + SOPS + monorepo. **WireOps** targets multi-host + secrets + notifications but is newer.

### Same problem, different layer

| Tool | Overlap | Mismatch |
|------|---------|----------|
| [YggOps](https://github.com/plaffitt/yggops) | Git pull + compose plugin, webhooks, extensible plugins | Restic/OPNsense as custom plugins |
| Portainer / Coolify Git | Compose from git, poll/webhook | **Monorepo pain**: same-repo polling can redeploy stacks on unrelated commits; Coolify is one resource per app, not one central path map |
| CI (Woodpecker, GHA, …) | Push → SSH → run **`deploy-from-git.py`** | Keeps all custom logic; swaps timer for webhook |

### What generic tools rarely ship

That is why this repo still feels “homegrown” but not odd:

1. **Central path → stack map** in `config/deploy.yml` (including `config/` and `scripts/` triggers).
2. **Pre-deploy restic** as a blocking gate.
3. **SOPS → `/run/homelab/`** via stack-specific render scripts (not only decrypting a compose `env_file`).
4. **Per-stack hooks**: build, push to Docker Hub, health timeouts, Recyclarr/Cleanuparr, OPNsense API, Neptune PowerShell.
5. **Dirty working tree on the deploy host = stop pulling** (Telegram alert).

Generic Compose GitOps covers **git → compose reconcile**. The deploy pipeline adds **policy and secrets** on top.

## If we were shopping later (without migrating now)

- **Minimal change:** keep the poller; optional **GitHub webhook** on the deploy host that runs the same script (faster than a 1-minute timer).
- **Replace poller only:** evaluate **gitcompose** or **compose-sync** (structure already matches `stacks/`).
- **Replace poller + SOPS integration + UI:** evaluate **Doco-CD** on the deploy host (size of monorepo `.doco-cd.yml` is the cost).
- **Multi-host dashboard:** watch **WireOps** or **deeplo**; expect to **call existing scripts** as pre/post deploy hooks.

## Bottom line

Something **is** out there that resembles this setup — especially **compose-sync**, **gitcompose**, and **Doco-CD**. What we have is those ideas plus an **integrated policy layer** (backups, secrets, path map, hooks, multi-host exceptions) that those tools assume you bolt on via hooks, CI, or sidecar scripts.

Staying on the custom pipeline is a reasonable choice until a maintained tool covers enough of that layer that maintaining `deploy-stack.sh` stops paying for itself.

## Failure modes and observability

Bash/Python deploy scripts are a valid concern if errors fail silently. In practice the **poller and compose path fail loud** (journal, pending retries, Telegram dedupe, health wait), but **post-up hooks and path-map gaps** can look like success.

Full detail: **[docs/deploy-observability.md](./docs/deploy-observability.md)** — guarded stages, soft-failure table, deploy host diagnosis commands, hardening options, and what mature GitOps tools actually fix vs not.

## Control plane vs data plane

To share the deploy engine separately from Rosland-specific stacks and secrets, see **[docs/control-data-plane.md](./docs/control-data-plane.md)** and **`config/homelab.example.yml`** (site manifest template).
