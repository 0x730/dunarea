# Agent workflow repair — 13 September 2026

## ICE and accepted contract

**Intent:** make Danube's agent instructions dependable across clients and
demonstrate a complete, reviewable source handoff without releasing the product.
**Context:** owning checkout `/home/dacrise/0x730/danube`, clean `main` at
`0347cfb8a56d22b26b12f1f36cbe99a349aefc42`; local `origin/main` and a fresh
`git ls-remote origin refs/heads/main` agree. No existing work needs moving.
README → DEPLOY → ops remains the documentation structure; no PM board is added.
**Expectations:** the four outcomes below are accepted from the assignment;
source describes implementation, while this acceptance defines intended behavior.
Do not relax acceptance to fit a defect.

Input: [assignment v1.0](../../ops/0x730/spec/prompts/agent-workflow-repair-danube-v1.md),
SHA-256 `55e237675599b671d311e2f1dce925802b05299a364d82c471e9f7bd8f84ee2b`.
Available Ops HEAD: `879187a5df92299d98f230b7852d2eabba602086` (read-only input,
not an assertion that all Ops working files equal that commit). Read the dated
[audit](../../ops/0x730/PM/audits/runs/2026-09-12-agent-governance/REPORT.md),
[source evidence](../../ops/0x730/PM/audits/runs/2026-09-12-agent-governance/EVIDENCE.md),
[Ops contract](../../ops/0x730/spec/technical/ops-agent-workflow.md), and
[Git authority decision](../../ops/0x730/PM/decisions/2026-09-12-agent-git-handoff-authority.md).
These workstation links require the sibling Ops checkout; no central return or
approval is required. Historical local inputs retain their dates:
[DevOps adoption](devops-adoption-evidence-2026-09-06.md),
[receipt](release-receipt-evidence-2026-09-10.md), and
[freshness](source-freshness-evidence-2026-09-12.md).

The nearest local workflow contract is [ops/README.md](README.md), reached through
[README.md](../README.md) and [DEPLOY.md](../DEPLOY.md). Before implementation,
this note reconciles its intended extension: one concise workflow section for
ICE → Spec/contract → durable Plan → failing test/evaluation → implementation
→ verification → independent review → documentation/evidence reconciliation.
Scientific contracts in README, [METODE.md](../METODE.md), and [API.md](../API.md)
are preserved: measurement/model/provenance distinctions, optional `h5py`, the
historical host-bound RHMZ TLS exception and integrity caveat, and manual-only
optional AI. Source inspection below resolves the stale description of that
exception without reinstating it or weakening the existing strict-TLS tests.

| Accepted outcome | Planned change and observable evidence |
| --- | --- |
| 1. Dependable discovery | Concise canonical AGENTS plus a thin CLAUDE explicit import; resolve paths and measure bytes using documented client scope. Distinguish static checks from native loading. Critical rules remain versioned; memory follows session authority. |
| 2. Intent and complete evidence | Version the sequence, source/intended distinction, candidate identity, exact statuses, failures/skips/retries, owners and stage boundaries in the existing workflow and this note. |
| 3. Accurate verification | Require the existing Python suite and diff check plus changed links/examples/applicable YAML. Describe Python/Node/Git/Bash fixtures, bytecode writes, embedded JS checks, receipt preparation and the OpenSSL skip accurately. Inspect hooks; retain manual enforcement. |
| 4. Demonstrated handoff | Map acceptance to the final diff/checks, obtain independent read-only review, resolve findings, reconcile docs/Unreleased, commit and push through normal checks, then verify the exact remote branch/revision. |

## Durable Plan and ownership

This record was created before implementation. The harness is in Default mode;
native Plan mode was not activated or exposed as a switch. Execution after
planning is explicitly authorized; a later planning-only/stop instruction controls.
Any session todo is only a view of this record.

1. Inspect instructions, current source/tests, effective hooks and push automation;
   record the initial document evaluation and reconcile intended acceptance here.
2. Update AGENTS, add the supported CLAUDE bridge, and put the reusable procedure
   in ops/README; reconcile README verification, DEPLOY Git boundaries and Unreleased.
3. Root runs the required suite once for the candidate, then direct document
   checks; capture actual exit statuses and all skips. Fix only demonstrated gaps.
4. Freeze the candidate/diff identity and give an independent read-only reviewer
   the original assignment, this acceptance and exact evidence. Root fixes findings
   and reruns affected checks; reviewer assesses changes without becoming maker.
5. Reconcile this note; commit/push the coherent verified/reviewed source through
   normal hooks and compare exact local/tracking/remote revisions. Retain an
   evidence-only closure checkpoint if needed to record the first delivered SHA.

Root is the sole maker, integration owner, verifier and Git publisher in Danube.
Root exclusively owns tests, temporary generated outputs and tracked edits.
The bounded reviewer may read this repository, the original read-only Ops inputs
and sanitized `/tmp` candidate evidence; its artifact is findings returned to root.
Independent judgment helps detect acceptance drift and unsupported proof claims.
It may not edit files, run suites, publish, call providers, spend AI credits or
delegate. Stop on candidate drift, an authority conflict or a need for external
proof; report unavailable review as pending, never passed.

Authorized: scoped instructions/docs/evidence, necessary demonstrated narrow
verification corrections, independent review, coherent commit/push. Excluded:
deployment, paid AI, live data/alerts/backups/recovery, provider or host mutations,
global/private settings or memory writes, other-repo edits, scientific changes,
new autonomous agents and public-output promotion. Provider GET is limited to the
assignment's required push-trigger check, with no host/runtime audit.

## Initial evaluation and command evidence

- Static baseline: CLAUDE absent; AGENTS still says documentation grants no
  commit/push authority; README promises the suite takes under one second.
  These fail the accepted document expectations. No helper defect was found;
  direct document evaluation is appropriate, without tests that pin new prose.
- `git config --show-origin --get core.hooksPath`: exit 1, unset (expected absence).
  `git rev-parse --git-path hooks`: exit 0, `.git/hooks`; no executable non-sample
  hooks. No tracked `.github/`, `.husky/` or `.githooks/` files. Checks are manually
  required; no hook framework is installed by this repair.
- A combined source search exited 2 because `tests/test_offsite_backup.py` does
  not exist; corrected to the existing `tests/test_ops.py`. This was a discovery
  error, not a suite failure. `command -v gh` found no CLI; no installation made.
- Source readback: `tests/test_freshness.py` invokes the four Node browser tests;
  `tests/test_release_receipt.py` prepares real Python→Node receipts in temporary
  Git repos. `tests/test_ops.py` uses temporary SQLite/filesystem fixtures, mocked
  external requests, Bash syntax checks, and an explicit `openssl absent` skip
  for the authenticated-encryption round trip. No duplicate JS gate is needed.
- Forge sites collection GET via the existing `~/.forge/fc` initially failed:
  curl exit 6 (sandbox DNS) and jq capability error; pipeline exit 1. The same
  bounded read with escalation and `pipefail` exited 0. Selected Danube record:
  repository `0x730/dunarea`, branch `main`, installed, `quick_deploy=false`;
  pagination `per_page=30`, `next_cursor=null`. No raw private payload retained.
- `curl -fsS --max-time 20 https://api.github.com/repos/0x730/dunarea/actions/workflows`:
  exit 0, `total_count=0`, empty workflows. Together with source and Forge readback,
  this confirms the existing documented non-deploying push path. No provider
  configuration is changed. This is not a general audit of private integrations.
- `git ls-remote origin refs/heads/main`: exit 0, exact baseline above; its
  background tool session was waited to completion before recording success.

## Candidate verification and review

The initial candidate changes only seven Markdown files: AGENTS, CLAUDE, README,
DEPLOY, ops/README, CHANGELOG and this note. No helper, suite, scientific source,
public API contract, version, provider configuration or runtime file is changed.

Source discrepancy discovered during verification: the assignment and README
describe a relaxed RHMZ exception, but `connectors.py::hidmet_report()` already
calls the normal verified `http_get`; `test_no_transport_can_disable_tls_verification`
rejects disabled TLS, and `test_hidmet_preserves_observation_time_and_requires_verified_tls`
requires verified delivery. Decision within the authorized documentation scope:
correct the stale description, preserve the old exception's RHMZ-only scope and
integrity caveat as history, retain the existing unverified-data exclusion tested
by `test_serbia_check_does_not_count_unverified_rhmz_transport`, and leave source
and acceptance tests unchanged. This is no new TLS exception or scientific decision.

| Check / attempt | Exact result and limit |
| --- | --- |
| `python3 -m unittest discover -s tests -v`, sandbox | Exit 1; 161 tests in 10.846 s, five receipt failures (`not inside a Git repository`), no skips. Preserved as failed evidence. |
| Same command, authorized execution context | Exit 0; `Ran 161 tests in 14.352s`, `OK`, no skips. Includes four browser tests and the OpenSSL round trip; no duplicate JS run. Both tool sessions waited to terminal completion. |
| Bounded Node `spawnSync` Git-root probe | Sandbox exit 1, `errorCode=EPERM` even though returned status was 0; unchanged escalated probe exit 0, status 0, no error or signal. The writer catches this subprocess error and emits its generic missing-repository message. No source workaround warranted. |
| Same required suite after the README TLS correction | Exit 0; `Ran 161 tests in 13.374s`, `OK`, no skips, on frozen review candidate v1. Waited to completion. |
| `git diff --check` | Exit 0, including the reconciled candidate. |
| `python3 /tmp/danube-workflow-doc-check.py` | Exit 0 initially: 84 local targets/anchors and 21 Bash examples. After TLS reconciliation: 85 targets/anchors and 21 examples, exit 0; only the two Forge template directives substituted with `:` for `bash -n`. No example executed. |
| `git fetch origin main` | First exit 255: sandbox `.git/FETCH_HEAD` read-only. Unchanged authorized retry exit 0, waited to completion; HEAD/tracking remained the baseline. No outgoing earlier commits. |
| `git diff --exit-code 0347cfb8a56d22b26b12f1f36cbe99a349aefc42 -- '*.py' '*.mjs' static API.md METODE.md VERSION` | Exit 0: application, tests, scientific/API contracts and version unchanged. |
| YAML | Not applicable: all seven changed files are Markdown without YAML frontmatter. |

The temporary document checker resolves relative files and heading fragments
(including sibling Ops links), checks the literal Claude import outside fences,
measures instruction bytes and parses Bash blocks. It is an ad hoc document
evaluation, not a repository test or operational framework. Results do not prove
external-link availability or client loading.

Discovery references retrieved on 13 September:
[official Codex instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
document root-to-working-directory discovery with override precedence and a
32 KiB default combined project limit. There are no root/nested overrides here;
the selected global instruction files are absent, and safe inspection of only
`project_doc_max_bytes`/`project_doc_fallback_filenames` found both unset.
Installed CLI version probes exited 0: Codex 0.154.0 and Claude Code 2.1.270;
these do not identify this hosted session's loader implementation.
[Official Claude memory documentation](https://code.claude.com/docs/en/memory)
supports an unfenced `@AGENTS.md`, relative to CLAUDE.md, as the shared-instruction
bridge; imports consume context. Only that file is imported, with no external
import or global settings change. Full README/DEPLOY/ops procedures are explicit
reads, not automatic imports. Final bytes: AGENTS 5,660, CLAUDE 216; the repository
Codex chain is 5,660 bytes, and the explicit Claude bridge plus import is 5,876.

Native loader evidence: this active session received the original AGENTS text
in its user context and explicitly read changed files through tools. It exposes
no fresh client loader manifest or Claude `/context` command. No new paid session
was launched; fresh native loading of the repaired files is **unverified**, not
inferred from static discovery or the review subagent. No overflow defect is claimed.

Independent review: `/root/workflow_review` received the original assignment,
the exact candidate diff, seven-file SHA-256 manifest, this accepted contract and
command results. It independently verified every candidate file/hash and the
baseline, inspected source/tests and the document checker, and reported **no
content defects or blocking findings**. It agreed with the strict-TLS reconciliation
and all four outcome mappings. It did not rerun tests or reproduce provider reads;
root owns those observed results. Review did not edit the candidate. Its only
remaining action was root's evidence/Git closure, followed by a short note review.

Frozen candidate v1 (before this evidence-only reconciliation): manifest SHA-256
`8e7705aa9dd3a64d140f829c80f70f484f586bb6741d5c69a37fe0d14b8d7f32`, diff SHA-256
`168af0d47652d335e539989a1d9b514e2dcc20497ad2098ca2c7e058613da6a4`.
Baseline is the full SHA above. The six final instruction/procedure files retain
these SHA-256 identities; subsequent reconciliation changes only this note:

| File | SHA-256 |
| --- | --- |
| AGENTS.md | `190030226874b5480dd89f5e4c6aeba617c946ca2e038ee44e4cbe0866e103f8` |
| CLAUDE.md | `c31b237a3238307c86802ddf19982cd0ec983010c67642ed741fae160b628529` |
| README.md | `8c93f7bf3c7f1d8ef0b5e3a66fb0ef1a7822554608963efebd0268a44c92a244` |
| DEPLOY.md | `2e10ad3cda5d9aab055632c7f94976b2c783cb903ff019958e87cde37ec5e5a9` |
| ops/README.md | `c063685e247a601873f048847c19dba98c857d4f7241e6b6c9bc574e21f56c8e` |
| CHANGELOG.md | `ef5ccff5eacf230ea8f82640000fd9d2233cd3fb715c31de6157a69062bd1db2` |

| Accepted outcome | Implemented and verified result |
| --- | --- |
| 1. Discovery | AGENTS/CLAUDE, README and ops discovery links; import/path/byte checks and independent source review passed. Fresh native loading explicitly unverified. |
| 2. Intent/evidence | AGENTS and ops workflow plus this pre-implementation ICE/Plan record retain original acceptance, candidate identity, exact statuses, failed attempts and scope boundaries. |
| 3. Verification | README/DEPLOY/ops match inspected source/fixtures; final suite 161 passed, no skips; 85 links/anchors and 21 examples passed; manual hooks and YAML inapplicability stated honestly. |
| 4. Handoff | Independent review completed, nearest contracts and Unreleased reconciled; coherent Git delivery is the next authorized step, recorded below after exact remote readback. |

## Completion and remaining stages

Plan steps 1–4 are complete; step 5 is at the verified/reviewed source checkpoint.
Git commit/push and exact remote result are pending until executed, and will be
recorded in the evidence-only closure checkpoint. Deployment, public
SHA/readiness, fresh source observations, inbox receipt and restore are unperformed
and outside this assignment. The manifest's September 9 accepted-unproven
alert-receipt/restore exceptions stay unproven. Historical August 28 and September
release evidence does not refresh these gates.

Reusable practices: keep the durable plan and acceptance in the existing dated
note; diagnose execution-context failures with an unchanged bounded retry before
editing a working helper; inspect current source before preserving an obsolete
description. These lessons are versioned here, with no private memory write or
sibling-project change.
