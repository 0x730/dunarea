# Danube DevOps documentation adoption

Date: 2026-09-06. Owning checkout: `/home/dacrise/0x730/danube`, branch `main`.
Baseline revision: `c7522ba934897eebe163f53a82e31e93c903e360`; initially clean.
Scope: documentation adoption; no commit, push, deployment, provider mutation,
host maintenance, alert sending, backup, or recovery execution.

## Sources and project-specific decisions

- [Shared session prompt](../../ops/0x730/spec/prompts/project-devops-session.md)
  and [Danube adoption instructions](../../ops/0x730/PM/audits/runs/2026-09-06-devops-operating-reference/ADOPTION.md#danube).
- [Shared entrypoint](../../ops/0x730/PM/runbooks/devops-entrypoint.md),
  [project map](../../ops/0x730/PM/runbooks/devops-project-map.md#danube), and
  [manifest](../../ops/packages/fleet/manifests/danube.json).
- Local contracts: [DEPLOY.md](../DEPLOY.md), [ops index](README.md),
  [edge policy](cloudflare-edge-policy.md), and
  [historical hygiene evidence](runtime-hygiene-evidence-2026-09-01.md).
- Inspected implementations: `backup.py`, `offsite_backup.py`,
  `prune_releases.py`, `runtime_hygiene.py`, `source_freshness.py`,
  `write_build_revision.py`, `verify_deploy.sh`, and the installed `fc`/`cf`
  wrapper structure. Credential files were not read.

Kept README → DEPLOY → ops discovery, Python/SQLite, no production build step,
and the existing unittest gate. Added root [AGENTS.md](../AGENTS.md), without
a second PM layout or duplicated transport. Updated the existing changelog
and runbooks. Shared-host, process-manager, zone/account/runtime-mail, and
storage-prefix ownership remain distinct. Existing tools cover this adoption;
no new operational capability is needed.

## Read-only provider evidence

`~/.forge/fc /orgs/daniel-vladescu-nud/servers/949568/sites --fail --max-time 20`
with allowlisted fields returned both site records, `per_page=30` and
`next_cursor=null`:

| Site | User | Quick Deploy | Zero downtime | Provider retention |
| --- | --- | --- | --- | --- |
| Danube `3331936` | `dunarea` | false | true | 1 |
| Portfolio `3235234` | `forge` | true | true | 1 |

Danube repository/branch matched `0x730/dunarea`, `main`. The dedicated
`/orgs/daniel-vladescu-nud/servers/949568/sites/3331936/deployments/script`
resource returned type `deploymentScripts`, `attributes.content` and
`auto_source=false`. Its content matches the documented sequence: create,
write revision, unittest, activate, compare active HEAD/revision, restart
`daemon-1006295:*`, and run the existing pruner with `--apply`.

The provider lookup initially hit sandbox DNS/jq restrictions; the same
bounded read was retried with escalation. GET on the individual site path
returned HTTP 405; the documented collection GET succeeded. The dedicated
script field is `content`, not `script`. Future sessions should reuse these
verified collection/resource paths and inspect schemas rather than infer them.
The [official API introduction](https://laravel.com/forge/docs/api-reference/introduction)
and [deployment operation](https://laravel.com/forge/docs/api-reference/deployments/create-deployment)
were also checked for base URL, org-scoped route, async acceptance and response shape.

## Validation

- `python3 -m unittest discover -s tests -v`: **145 passed**, 7.698 seconds.
  The initial run caught the existing documentation contract requiring the
  generic org-scoped POST schema; retained that schema alongside the concrete
  Danube path and reran successfully. No tests or runtime code were changed.
- `git diff --check`: passed.
- Local Markdown target/heading check across the six changed documents:
  **51 links/anchors passed**, including sibling Ops references. This is a
  filesystem/anchor check, not a full external-link crawl.
- `bash -n` on **20 Bash examples** passed, replacing only Forge's
  `$CREATE_RELEASE()` / `$ACTIVATE_RELEASE()` template directives with no-ops
  for parsing. No documented operational command was executed by this check.
- Manual fresh-agent discovery review: root AGENTS and README both lead to
  the existing runbook, local tool/defaults index, shared entrypoint/map and
  manifest, then the resource-specific deployment and acceptance procedure.
- Final scope review: only `AGENTS.md`, `README.md`, `DEPLOY.md`,
  `ops/README.md`, `CHANGELOG.md`, and this evidence note changed.

## Reconciliation and remaining evidence limits

- Replaced active `deployment_retention=4` prose with the observed value `1`;
  preserved the former claim under a historical label. The pruner protects
  active plus newest rollback independently of provider retention. Physical
  directory counts were not read or changed in this session.
- Changed already completed rotation/monitor bootstrap instructions into
  dated installed-state guidance. No process, scheduler, or rotator was added.
- Corrected monitor defaults: no alert flag still permits status writes;
  test-alert flags send actual messages. The verifier uses temporary files
  with cleanup and isolates its off-box status rather than making zero writes.
- Removed the stale pending TEM migration instruction. Historical recovery
  evidence remains dated; the pressure-alert test's inbox receipt remains
  unproved by the September 1 evidence. No new delivery or restore claim.
- Hetzner context/provider-state visibility remains undeclared. No new server
  or context is warranted by that gap. No host, public-runtime, Cloudflare,
  job-execution or recovery audit was performed for documentation adoption.
- The shared project map's statement that no Danube AGENTS existed is explicitly
  a review-cutoff statement; this note records its subsequent local adoption.
  Shared files and deferred Ops work were left untouched. Shared links resolve
  in this workstation's sibling Ops checkout, not on the public GitHub site.
- No unresolved contradiction remains between the adopted instructions and
  the local command implementations/selected provider settings. Broader live
  acceptance and historical registry findings require their own fresh evidence.
