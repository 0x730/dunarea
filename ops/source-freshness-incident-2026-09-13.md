# Source freshness incident — 13 September 2026

## Trigger and scope

The installed daily monitor (`source_freshness.py`, Forge `2120262`, `25 9 * * *`)
reported state `stale` at `2026-09-13T09:25:28Z` across 15 endpoints, with four
lines:

```
/api/overview: stale la inhga
/api/danubehis: .: observation Gönyű: stale (2026-08-13; limit 2 days)
/api/inhga: stale
/api/romania: stale la source_freshness.inhga
```

This is the first daily run after the observation policy of
[12 September](source-freshness-evidence-2026-09-12.md) was deployed; the
previous run reported `fresh` at `2026-09-12T09:25:02Z`. The four lines are two
upstream conditions, not four incidents.

Triage was read-only: HTTPS GETs to the public official sources from the
workstation, plus local source inspection. No provider state was read or
changed, no alert, backup, prune or recovery command was run, no paid AI, and
the application was not started locally.

## Findings

**INHGA — three lines, one fact: upstream TLS latency above the fetch budget.**
Root-level `stale: true` means `_fetch_and_store` served the stored snapshot
after the fetch raised. Measured against `www.hidro.ro`: TCP connect 0.02–0.12 s,
TLS handshake 9 s, 13.9 s, 22.2 s, 28.0 s and 40.6 s across attempts, and two
homepage attempts that never completed within 60 s. `www.danubehis.org` and
`www.afdj.ro` handshook in 0.07–0.30 s from the same machine, so the stall is
the upstream host, not the local network. `http_get`'s socket timeout covers the
handshake, so the default 25 s budget fell in the middle of the measured range
and `inhga_bulletin()` — two sequential requests — failed intermittently.
`cached()` behaved correctly throughout; the fallback marking is what the
monitor saw.

**The INHGA parser is intact.** The listing regex matched 40 bulletin links
(newest `12-09-2026`) and that bulletin parsed to debit 1300 m³/s, tendință
`scădere`, medie multianuală 3800 m³/s, prognoză 1250 m³/s and 4 official
paragraphs. No `13-09-2026` bulletin existed at check time — normal publication
lag, inside the 1-day tolerance, which is why INHGA showed only the delivery
flag and no observation problem.

**DanubeHIS Gönyű — a genuine upstream station gap, correctly detected.**
Delivery succeeded. Of 15 stations, 13 were dated 2026-09-13; Vác was dated
2026-09-11 (at the 2-day limit, expected to trip next); Gönyű was frozen at
2026-08-13, 702.4 m³/s, 31 days old. Neighbouring Nagybajcs (656.0) and Komárom
(760.7) were current, so this is station-specific at OVF/ICPDR, not a Hungarian
outage. A plausible-looking frozen value is exactly what the observation policy
exists to expose. Nothing is changed in code for it; the tolerance is not
raised.

**Defect found during triage.** In `maintenance_cycle()`, the `inhga` job
wrapper called `C.inhga_bulletin()` without returning it, so the cycle's
fallback check (`result.get("stale") is True`) was unreachable for that job
alone. A refresh served from a snapshot reported `ok` in `/api/health.maintenance`.
This is why the incident under-reported: state `stale` with no
`maintenance inhga: failed` line, even though the refresh had in fact fallen
back. The intent at that check was already implemented for every other task;
INHGA disagreed with it, so it is recorded and repaired as a defect.

## Accepted change

The operator authorized the dated record and the timeout work through the
ICE → spec → failing test → implementation path. No new job, alert transport,
host policy or data archive is introduced; no provider setting is touched.

- `connectors.py` gains `INHGA_HTTP_TIMEOUT_S = 45`, sized above the measured
  handshake range and applied to every `hidro.ro` fetch: the daily bulletin
  listing and page, the monthly tributary listing and page (previously 30 s),
  and the archived daily bulletin. The budget is per request and covers the
  handshake; two sequential requests stay under the documented
  `proxy_read_timeout 180s`, which is a comfortable bound rather than a hard
  ceiling on the call. The archive path is included because a timeout there
  writes `None` into the permanent day cache — that stops new holes, it does not
  heal existing ones (see the deferred defect below).
- `server.py`'s `bulletin()` returns its refresh result, so a fallback delivery
  marks the maintenance task `failed` uniformly with every other task and is
  retried on the next 30-minute cycle. When no refresh is due the wrapper
  returns `None` and the task stays `ok`.
- `API.md` states explicitly that a refresh served from a snapshot is reported
  `failed`.

Delivery semantics, observation policy and tolerances, TLS verification, cache
TTLs and the alert transport are unchanged.

## Operator-visible consequence

The monitor appends a failed maintenance task to `failures`, not to
`staleSources`, and `state` is `failed` when any failure exists. So the same
upstream condition that produced today's `stale` report will produce **`failed`**
once this is deployed — a change in the daily state label and alert subject, not
merely one extra line. Alert volume and exit code are unchanged: the job already
alerted and exited non-zero for `stale`. Tomorrow's `failed` should be read as
this repair landing, not as a worse incident.

## Deferred defect (not fixed here)

`inhga_backfill()` skips a day when `cache_get(...) is not None`, but `cache_get`
returns a truthy `{"age": …, "data": None}` for a day cached as a failure with
TTL `10**9`. Days that timed out during earlier warmups are therefore never
retried, and the 2 h / 7 d retry window inside `inhga_bulletin_for()` is
unreachable because backfill is its only caller. Existing holes in the 90-day
Baziaș series persist. Verified in source during this work; left out of scope
deliberately, because repairing it changes archive-filling behaviour and
deserves its own acceptance.

## Independent review

Reviewed against the acceptance, the exact diff and this evidence. The reviewer
ran the suite independently and reproduced both tests failing against unmodified
`HEAD`, and confirmed by mutation that reverting any one of the five fetch sites
or the added `return` makes the new tests fail. No blocking code defect; scope
confirmed as exactly (A) + (B) plus the tests, with TLS, tolerances, provenance
and transport untouched.

Two blocking process findings, both the maker's: the reviewer was handed only
the four-file code diff while the documentation edits and the untracked note sat
outside it, and the note is linked from `CHANGELOG.md` and `ops/README.md` while
untracked — a commit without an explicit `git add` would ship two broken links
into a public repo. Both are resolved in this commit.

Accepted and applied from the non-blocking findings: a third maintenance case
pinning that no due refresh leaves the task `ok` without touching the source; a
source-level assertion so a later `inhga_*` fetch site cannot fall back to the
default budget silently; a precise statement of the per-request budget in the
code comment and above; the operator-visible escalation recorded; and the
backfill defect recorded as deferred rather than folded in. The reviewer also
noted that 45 s is only 11% above the worst successful handshake observed and
two attempts never finished inside 60 s — this reduces fallback delivery, it
cannot remove it, and the limits section below is the honest statement of that.

## Validation

Tests were written first and confirmed failing against the unchanged source:
`AttributeError: module 'connectors' has no attribute 'INHGA_HTTP_TIMEOUT_S'`
and `AssertionError: 'ok' != 'failed'`. They pin behaviour, not constants: every
`hidro.ro` request must carry a budget longer than `http_get`'s own default, and
a fallback refresh must be `failed` while a successful one stays `ok`.

`python3 -m unittest discover -s tests -v`: 163 tests, all passed, no failures,
no skips (the OpenSSL-dependent skip did not apply). `git diff --check` clean.
Changed Markdown links checked.

## Not performed

No deployment, no post-deploy acceptance, no Forge or Cloudflare interaction, no
alert exercised and no restore proof. The measurements prove upstream behaviour
at the time of triage, not future availability. The Gönyű gap and the hidro.ro
latency are upstream conditions: the change reduces how often the latency turns
into fallback delivery and makes the fallback honest in health, but it cannot
make either source publish.
