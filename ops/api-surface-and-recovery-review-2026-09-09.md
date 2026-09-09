# Danube public API surface and recovery-mail review

Date: 2026-09-09. Owning checkout: `/home/dacrise/0x730/danube`, branch `main`.
Baseline revision: `90b95bb4c98b3eb64e23efaef70ffe8e3829a745`; working tree
initially clean.

Scope: read-only review of the public surface, one local test fix, and
documentation. **No** commit, push, deployment, provider mutation, host
maintenance, alert send, backup, prune, or recovery execution. No credential
file was read or printed. No paid AI inference was run.

Sources: [shared entrypoint](../../ops/0x730/PM/runbooks/devops-entrypoint.md),
[project map](../../ops/0x730/PM/runbooks/devops-project-map.md#danube),
[manifest](../../ops/packages/fleet/manifests/danube.json),
[shared dependency and recovery-access map](../../ops/0x730/PM/runbooks/shared-dependencies-recovery-access.md),
and the local [DEPLOY.md](../DEPLOY.md), [ops index](README.md),
[edge policy](cloudflare-edge-policy.md).

## 1. Public route inventory and parameter bounds

`server.py` exposes **47 routes, all under `/api/`**, matching the count
declared in [`cloudflare-edge-policy.md`](cloudflare-edge-policy.md); no drift.
Seven page paths alias to `index.html`. Only `GET`/`HEAD` are served.

Every public query parameter is bounded before it can reach a source:

| Parameter | Bound |
| --- | --- |
| `point` | membership in `GLOFAS_POINTS` / `PRECIP_POINTS` (published by `/api/points`) |
| `days`, `start` | `_int_choice` against short frozensets; anything else is `400` |
| `uuid` | strict UUID regex **and** membership in the fetched PegelOnline station list |
| `param` | coerced inside the connector to exactly `Q` or `W` |
| `layer`, `zone` | membership in `EDO_MAP_SPECS` / `OPERA_LAYERS` / `OPERA_ZONES` / `CDSE_CONTEXT` |

No public parameter is interpolated into an outbound URL without first being
reduced to a value from a fixed set. The value-list bound is deliberately about
cardinality, not size: each distinct value is a new cache key and a new request
to an official source.

**SSRF.** Source-supplied URLs (catalogs that return their own asset href) pass
`_validated_https_url`: HTTPS only, no userinfo, non-global IP literals
rejected, host allowlist. Three call sites (hidro.ro, HydroWeb, one further
catalog). TLS verification is never disabled; `_ssl_context_for` returns the
system default unless a host has a pinned extra CA.

**Credential leakage.** `_public_payload` is the last barrier on every response:
it strips credential-shaped query parameters (exact names plus camelCase/word
matching) and `user:password@` userinfo from any URL in any payload. HydroWeb
signed asset URLs are never serialized; provenance is the stable public portal
plus `raw_sha256`.

**Private file exposure.** Static serving resolves `realpath`, requires
`commonpath == STATIC_DIR`, and allows nine extensions. `cache.db`,
`data/keys/`, `server.log`, and the ops env files all sit outside `static/`.
`data/keys/` is `0700` with `0600` members, and is ignored both by directory
and by `*.key` pattern; `git ls-files data/` tracks only `data/grdc/README.md`.

**Rate and cache.** No application-level rate limit; the single Free-plan edge
rule (60 req / 10 s / IP / colo on `/api/`, block 10 s) is the control, verified
2026-08-28 in the edge policy. `/api/health` deliberately touches no external
source, so it cannot be used as an amplification lever. All `/api/` responses
are `no-store`; freshness is carried in the payload.

Error taxonomy confirmed live: `400` invalid parameter, `404` unknown route,
`405` non-GET with `Allow`/`Connection: close`, `502` upstream failure with a
generic message, `503` with `Retry-After` when a local result is not ready.

**Admin ingestion and CLI entrypoints.** The one operator-fed ingestion path is
the GRDC export drop: `_grdc_series_uncached` lists a directory fixed at
`BASE_DIR/data/grdc`, filtered to `.txt`/`.csv`/`.day`. No public parameter
reaches it — `/api/grdc` takes no query argument and every caller invokes
`grdc_series()` with the default station constant — so neither the directory
nor the filename is attacker-influenced, and the parse result is cached 24 h so
a decades-long series is not re-parsed per request. The CLI entrypoints are
`analiza_ai.py` (§2) and the `ops/*.py` scripts, whose default side effects are
already tabulated in [`ops/README.md`](README.md); none of them is reachable
over HTTP.

## 2. The public AI route cannot initiate a paid call

`ROUTES["/api/analiza-ai"]` discards its query dict and calls
`analiza_ai.analiza(run=False)`. Verified empirically with `socket.socket`
patched to raise on any `connect`: `{}`, `{run: 1}`, `{run: true}`, and
`{run: 1, force: 1}` all returned `activ=False, manual_only=True` with **no
egress attempted**. The only `run=True` caller in the tree is
`analiza_ai.__main__`.

Credential handling: `_ai_key()` reads `AI_API_KEY`, else
`data/keys/openai.key` (`0600`, gitignored). The key is used only as a Bearer
header and never enters the result dict, the cache, the daily snapshot, or a
log line.

Declared spend bounds: `max_tokens: 1200` (chat mode), `max_output_tokens: 3000`
(web mode), a 180 s timeout, and a module-level lock that serialises runs. Web
search mode additionally refuses to run unless `AI_BASE_URL` is the official
`https://api.openai.com` host, so opt-in citations cannot be redirected to an
arbitrary base.

Observation, not a defect: `_amprenta_stare()` computes a state fingerprint that
is stored with the result but is **not** used to skip an unchanged run — every
manual invocation is a billable call. On a manual-only path that is the
operator's decision; it is the natural place for a future "state unchanged,
skip" guard if the analysis is ever scheduled.

## 3. Collection jobs verified against actual data

Reused the existing [`source_freshness.py`](source_freshness.py) rather than
writing a probe. Run without `--alert`/`--test-alert` (so no config is read and
no mail can be sent) and with the status file redirected to a scratch path:

```
{"baseUrl": "https://dunarea.info", "checkedAt": "2026-09-09T13:27:31Z",
 "endpointsChecked": 15, "failures": [], "staleSources": [],
 "reportAgeSeconds": 13273, "state": "fresh"}   exit 0
```

Fifteen live-source endpoints, no stale sources, no failures; anomaly report age
3.7 h against a 12 h threshold. Spot check: PegelOnline stations carried
`2026-09-09T15:15:00+02:00`, minutes old at read time. The service is collecting,
not merely running.

**Limit of this evidence:** the reading was taken through Cloudflare, whereas the
production job (`2120262`) reads loopback specifically to see the process rather
than the edge. `/api/` responses are `no-store`, so this reflects origin, but it
is not the same vantage point and does not replace the scheduled job's evidence.

## 4. Recovery-alert mail reconciled with the manifest

Repository state matches the manifest's `outboundEmail` contract exactly:

| Manifest | Implementation |
| --- | --- |
| `configurationKeys` (5) | `ALERT_KEYS` — identical set |
| `retiredConfigurationKeys` (3 × `DANUBE_BACKUP_TEM_*`) | `LEGACY_ALERT_KEYS` — **actively rejected** as `backup_alert_configuration_legacy` |
| `provider: cloudflare-email-sending` | `POST /client/v4/accounts/<id>/email/sending/send` |
| `senderDomain: 0x730.com`, owner `portfolio` | env example pins `0x730.com`, explicitly not `dunarea.info` |

The transport is fail-closed: a partial alert group is refused, the account id
must be 32 hex characters, the config file must be a regular `0600` file owned
by the effective uid, the response must carry `success: true` **and** list the
recipient in `delivered`/`queued` **and** not in `permanent_bounces`. Failures
collapse to the stable reason code `backup_alert_delivery_failed`; no provider
body is echoed. The API token appears only in the `Authorization` header.

All four alerting jobs (`2117004`, `2117005`, `2120262`, `2120431`) route
through this single `offsite_backup._send_email`; there is no second mail path.
`dunarea.info` itself stays `no-mail` with DMARC `p=reject`, consistent with
borrowing Portfolio's sender identity.

Receipt evidence already exists and is dated: DEPLOY.md records the Cloudflare
read-back of `0x730.com` and `cf-bounce.0x730.com`, a controlled test at
`2026-08-28T20:10:56Z` accepted as `delivered`/`queued`, and operator inbox
confirmation at 23:10 EEST. Provider acceptance and recipient receipt are kept
as separate claims throughout, which is the required distinction.

**Ops-side wording to reconcile in the Ops session** (not edited from here): the
shared dependency map states that "the August Danube mail evidence predates its
current Cloudflare sender contract". That is accurate for the *TEM* acceptances,
which DEPLOY.md already labels historical evidence of the replaced provider, but
the same day also carries a separate Cloudflare test with operator receipt.

**Not established here:** the contents of the installed on-host env file, and
any delivery after 2026-08-28. `--test-alert` sends a real message and was
deliberately not run.

## 5. Deployed build identity, dependencies, rollback

| Fact | Value |
| --- | --- |
| Deployed `buildSha` | `7848f94218567ab691b2c142988bfba8b7b2bf15` |
| Local `main` HEAD | `90b95bb4c98b3eb64e23efaef70ffe8e3829a745` |
| Relationship | deployed SHA is an ancestor of HEAD; **2 commits behind**, fast-forward, no divergence |
| Undeployed commits | `c7522ba` (ops: finish shared-host runtime hygiene), `90b95bb` (docs: adopt shared DevOps guidance) |
| Version / release | `1.1.0` = `VERSION` = tag `v1.1.0` |
| Uptime at read | 542 106 s (~6.3 days) |

Both undeployed commits are ops/documentation only; no application change is
waiting. Deploying is a separate authorized action and was not performed.

**Dependencies.** The application is Python standard library only, with one
exception: `h5py`, imported lazily inside the GRACE parser and guarded, so its
absence degrades exactly one route (`/api/gravimetrie`) with an explicit reason
instead of failing the process. Production has it — `/api/gravimetrie` returns
`activ: true`. There is deliberately no requirements file and no build step.
Frontend: ECharts **5.6.1** vendored at `static/vendor/echarts.min.js`,
sha256 `bf4a223524e40b77c304bec67e1222cf551f14880cf42c69dc046558e11c07b1`; the
page's `integrity="sha384-pPi0zx…"` was recomputed from the file and matches.

**Public surface, live:** all declared checks pass — security.txt `200`
(Canonical, `Expires: 2027-08-27`, `Preferred-Languages: ro, en`), sitemap
`200`, `www` → `301` to the apex, unknown route `404`. Response headers match
the `danube-strict` profile: CSP enforced, `nosniff`, `no-referrer`,
permissions policy, `frame-ancestors 'none'`, HSTS `max-age=86400` (added in
front of the application).

**Rollback** stays as documented: `prune_releases.py` is fail-closed, accepts
only the two declared roots, is dry-run until `--apply`, and preserves the
active release plus the newest rollback candidate. Provider `deployment_retention`
is a separate value from the directory count on disk.

## 6. Findings

1. **Fixed — latent clock dependency in the test suite.**
   `test_hydroweb_station_quality_is_explicit` built a fixture whose newest
   observation was `2026-08-04` and asserted no quality flags, while
   `_hydroweb_station_entry` measures age against the real clock with
   `HYDROWEB_MAX_AGE_DAYS = 35`. The test therefore began failing on
   **2026-09-08**, at day 36, with no code change — the release gate was red on
   arrival. Pinned the clock with the `FixedDate` idiom already used by the
   neighbouring EDO test. To confirm this was the only such trap, the suite was
   re-run with the module clocks pushed **+30, +90 and +365 days**: 145 tests,
   0 failures at every offset; a control run with the fix reverted reproduced
   exactly the one failure, so the harness is not a no-op.

2. **Recorded — `robots.txt` is edge-managed and was undocumented.**
   `https://dunarea.info/robots.txt` returns `200` from Cloudflare Managed
   Content, carrying `Content-Signal: search=yes,ai-train=no,use=reference` and
   `Disallow: /` for ClaudeBot, GPTBot, CCBot, Google-Extended, Amazonbot,
   Bytespider, Applebot-Extended and meta-externalagent. The file is **not** in
   the repository (only `static/.well-known/security.txt` is tracked) and is
   **not** recorded in `cloudflare-edge-policy.md`, whose inventory otherwise
   accounts for the edge surface. For a public-interest open-data monitor whose
   stated purpose is provenance and reuse, whether AI crawlers are blocked is an
   operator policy decision — the finding was that the decision was implicit and
   unrecorded, not that it is wrong. Now recorded in
   [`cloudflare-edge-policy.md`](cloudflare-edge-policy.md) as a standing zone
   decision, including the point that edge-generated content can change without
   a commit or deploy, so it must be re-read rather than assumed. The zone
   itself was not changed; that would be a separate provider action.

3. **Note — the Ops project map predates `AGENTS.md`.**
   The map still says "There is no root AGENTS/CLAUDE entrypoint at the review
   cutoff"; `AGENTS.md` was added in `90b95bb`. Ops-session correction.

## 7. Local validation

`python3 -m unittest discover -s tests -v` — **145 tests, OK** (one failure
before the fix in §6.1). `git diff --check` clean. Changed documentation links
and examples were checked against the live surface.
