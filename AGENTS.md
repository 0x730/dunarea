# Danube agent entrypoint

This checkout is the Python/SQLite Monitor Dunărea (`0x730/dunarea`, branch
`main`). Start with [README.md](README.md), then [DEPLOY.md](DEPLOY.md) and
[ops/README.md](ops/README.md). Keep this documentation structure: there is no
local PM/card framework to import. Record documentation adoption in an `ops/`
dated evidence note and the existing `CHANGELOG.md` Unreleased section.

Before Forge, Hetzner, Cloudflare, deployment, recovery, or host maintenance,
read these canonical sources in the Ops checkout:

- [DevOps entrypoint](../ops/0x730/PM/runbooks/devops-entrypoint.md)
- [Project map: Danube](../ops/0x730/PM/runbooks/devops-project-map.md#danube)
- [Danube fleet manifest](../ops/packages/fleet/manifests/danube.json)

The shared entrypoint is at
`/home/dacrise/0x730/ops/0x730/PM/runbooks/devops-entrypoint.md` in Ubuntu-24.04.
If that checkout is unavailable, resolve its execution boundary; do not replace
it with memory or a copied handbook.

Reuse `~/.forge/fc <path> [curl-args...]`, `~/.cloudflare/cf`, and the scripts
indexed in `ops/README.md`. Before adding operational code, name the existing
file/function/command, its side effects, and the exact missing capability.
Forge uses `https://forge.laravel.com/api`, `/orgs/<slug>/...`, and resource
`data`/`attributes`; there is no `/v2` URL segment. Old v1 examples are history.
Check the exact operation/schema in the current official reference when it is
not covered by the existing implementation. Inspect actual flags: wrappers
have no `--write` gate, pruning requires `--apply`, and monitors write status
even without alert flags. `--test-alert` sends a real message.

Forge server `949568` is shared with Portfolio and Swing; it is not a Hetzner
server ID. No hcloud context is declared for this host. Danube runs as
`dunarea` in a Forge background process; Portfolio/Swing use PM2. Preserve the
single system logrotate owner and existing Forge backup/freshness/hygiene jobs.
Danube owns the `dunarea.info` edge policy; Portfolio owns the `0x730.com`
sender domain. Runtime mail credentials are separate from operator credentials.

Read provider state before mutation and bind scope to exact resources and all
affected owners. For privileged work reuse bounded temporary Forge Recipes
with polling, host readback, and cleanup; recurring work uses existing jobs.
Keep review, commit/push, explicit Forge deploy, returned-ID completion, public
SHA/readiness, recipient receipt, and restore proof separate. Quick Deploy is
off for Danube: follow `DEPLOY.md`, including `ops/verify_deploy.sh` on the host.

Preserve ongoing work and deferred decisions. Documentation adoption adds no
commit/push/deploy or infrastructure authority. Validate local changes with
`python3 -m unittest discover -s tests -v`, `git diff --check`, and checks of
changed documentation links/examples. There is no production build step or
hosted CI gate here. Do not run live alert, backup, prune, or recovery commands
merely to validate documentation. Never print or retain credentials, recipients,
client IPs, tokenized hooks, or raw private configuration.
