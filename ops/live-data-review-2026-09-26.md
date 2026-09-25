# Live data review — 25–26 September 2026

## ICE

**Intent.** The operator asked for a review of the live data and for whatever
needs improving. After the first report they pointed to the home page hero,
which read "Buletinul INHGA nu a putut fi citit acum", and authorized
improving the rest of the findings.

**Context.** Production ran v1.1.2 (`3ce9a2919a65`), deployed 13 September.
The baseline checkout was `edb7ceb`, clean. The review used only public reads:
the dunarea.info API, `ops/source_freshness.py --base-url https://dunarea.info`
with a scratch status file and no alert, and HTTPS GETs to the official
sources. No provider state was read or changed at that stage.

Findings at `2026-09-25T20:54Z`:

- every delivery was fresh and `/api/overview.errors` was empty. The monitor
  exited 1 on one line: DanubeHIS Gönyű discharge frozen at 2026-08-13. OVF's
  own table carried the Gönyű level for the day with an empty discharge, so
  the provider has stopped publishing Q there; it is an upstream gap;
- the INHGA bulletin of 25 September was fetched but parsed to
  `debit_bazias_m3s: null`. INHGA wrote "Baziaș) a fost 1600 m³/s" instead of
  "a fost staționar la valoarea de 1600 m³/s" (23–24 September). Both the
  current and the archive regex require "valoarea/valorii de". The observation
  policy still reported `fresh`, because it reads only the bulletin date;
- the INHGA archive is refilled only by a full warmup, which a restart within
  six hours skips, and `inhga_backfill()` never retries a day cached as a
  failure (deferred on 13 September). A missed day therefore stays a hole;
- the daily freshness job alerts on every non-fresh run with no memory, so the
  unchanged Gönyű line has produced one alert a day since 13 September
  (inferred from source and data; the inbox and host status file were not
  read). A new incident arrives looking like the same daily email;
- the INHGA↔GloFAS test reads `relatie_recent_schimbata` (z −2.98), shown as
  severe with "method, station or river" as the suggested causes. The series
  shows a rain-driven rise: 13–19 September GloFAS +60 %, INHGA +19 %;
- the process had been up 5 h (start 15:49:55Z) with no deploy since
  13 September;
- Ops 0089 (25 September) measured the shared host's journal at 2.1 GiB,
  critical, after replacing an SSH-user `journalctl --disk-usage` that
  undercounts. `ops/runtime_hygiene.py` uses the same undercounting call.

Checked and not a defect: EDO CDI is 24 days old at the publisher (its service
page names 1 September); the Delta P0 is consistent along the Romanian reach;
equal Cernavodă/Brăila medians are Open-Meteo value quantization; "Gönyû" is
OVF's own spelling (`&#251;`).

**Expectations.**

- A. Both INHGA parsers extract the Baziaș discharge from the "a fost … la
  valoarea de N" and the "a fost N m³/s" forms; the trend stays null when the
  bulletin does not publish one. The 25 September text parses to 1600 and
  forecast 1350.
- B. A bulletin whose date parses but whose Baziaș discharge is missing is
  observation `unknown`, never `fresh`, and names the missing value; the daily
  monitor lists it.
- C. A day cached as a failure is retried under `inhga_bulletin_for()`'s
  existing windows (2 h for the last three days, 7 days before), and every
  30-minute maintenance cycle backfills the last 14 days, so a missed day no
  longer waits for a full warmup.
- D. With `--alert`, the freshness monitor sends when the problem set is new
  or changed, repeats an unchanged set after 7 days, and sends one recovery on
  the return to fresh. Exit code, status evidence and `--test-alert` keep their
  meaning; the status section records the alerted set and time.
- E. The INHGA↔model result carries both series' change between the tested
  week and the week before. When the independent model moved by 15 % or more
  and the measurement moved the same way, the page says the break happened
  during a flow change and may be transient. Verdict, z and thresholds are
  unchanged; METODE §3 states the limit; the report cache key is bumped.
  (Refined after review from "when either moved": see the accepted decision
  below.)
- F. `runtime_hygiene.py` measures the journal as the byte count of the
  journal directories, `unavailable` when unreadable, as the Ops reader does.
- G. The 25 September restart is examined read-only and the outcome recorded.

Not changed: tolerances, TLS, provenance labels, delivery semantics, the alert
transport and thresholds. No deployment, host change, journald cap or provider
mutation without a separate explicit authorization.

## Plan

1. Failing tests first for A–F, run against `edb7ceb`.
2. Implement in `connectors.py`, `freshness.py`, `server.py`,
   `ops/source_freshness.py`, `anomalii.py`, `static/app.js`,
   `ops/runtime_hygiene.py`.
3. Suite, `git diff --check`, changed links.
4. Independent read-only review of acceptance, diff and this note; maker
   fixes and reruns.
5. Reconcile API.md, METODE.md, ops/README.md, CHANGELOG Unreleased and this
   note; commit and push on `main` (Quick Deploy is off).
6. Deployment and the journald cap only on explicit operator go.

Root owns the test lane. Stop if a change would weaken a tolerance or
provenance label, or if a host/provider mutation becomes necessary.

## Implementation evidence

Native Plan mode was not used; this note is the durable plan, and the session
todo was its view. Baseline `edb7ceb`, clean. No hooks are active
(`core.hooksPath` unset, only `.sample` files), so the checks below were run
by hand; there is no hosted CI.

**Red first.** The 13 new or extended tests were run against the unchanged
source: 7 failures and 6 errors, each for the intended reason, including
`None != 1600.0` for the 25 September form, `1350.0 is not None` (the old
regex took the forecast as the measurement when the "a fost" sentence had no
value), `'fresh' != 'unknown'`, `KeyError: 'inhga_archive'`, `no attribute
'decide_alert'` and `no attribute 'JOURNAL_DIRECTORIES'`. Three fixtures were
adjusted for the new contract: the INHGA date-tolerance test now carries a
discharge, and the two maintenance tests mock the new archive task. The old
`journalctl` parser test was replaced with the directory-byte contract.

**Green.** `python3 -m unittest discover -s tests -v`: `Ran 174 tests`, `OK`,
exit 0, no skips (the OpenSSL skip did not apply). `git diff --check` exit 0.
`node --check static/app.js` passed. Changed Markdown links and anchors
resolve.

**Real data.** The saved 23, 24 and 25 September bulletins parse to 1600 m³/s
each; 23–24 now also carry `staționar`, and 25 September carries forecast
1350 and no trend. The last 30 archived bulletins (26 August–24 September),
parsed with the new code, reproduce all 30 values production holds in its
series: 30 matching, 0 differing. The live `/api/inhga` payload annotated
with the new policy reads `unknown (2026-09-25; limit 1 days; missing
debit_bazias_m3s)`.

**Not performed (G).** The read-only host check of the 25 September restart
(`uptime -s`, process start, supervisor log over the existing `forge` SSH
access) was refused by the session's auto-mode classifier as a production
read. It is left to the operator; nothing was attempted by another route.

**Consequences to expect after a deployment.**

- The first `--alert` run has no alert memory, so it sends once for the
  current set (Gönyű), then mutes it until something new appears or a week
  passes.
- `runtime_hygiene.py` is expected to see the full journal. Ops measured
  2.1 GiB on 25 September as the `forge` SSH user, not as `dunarea`, so this
  is an expectation, not a verified reading. Without the journald cap
  (Ops 0089's two-line drop-in, a root step) the hourly job would go critical
  and re-alert every six hours. The measurement is corrected here; the cap is
  a separate host change.
- The 25 September day was never written to the archive (the parse returned
  nothing), so the first archive cycle fetches it.

## Independent review

A fresh read-only reviewer read this note, the exact diff against `edb7ceb`
and the evidence. It ran the suite in a scratch copy (174 tests, OK) and
reproduced the red run (7 failures, 6 errors). It ran 18 single mutations:
15 were killed and 3 survived (`>=` changed to `>`, the NEW marker removed,
the reminder label removed). It also re-ran the real-data checks. It found no
blocking issue, six should-fix findings and several nits. All were taken:

1. The page's freshness line showed the new INHGA `unknown` as "dată
   neverificabilă". It now reads "valoarea măsurată lipsește" when a value is
   missing.
2. **Accepted decision, refining E.** "Either series moved ≥ 15 %" would
   soften a jump in the official series alone, such as a new rating curve,
   which is the event the test exists to catch. The flag now needs the
   independent model to move ≥ 15 % and the measurement to move the same way.
   Tests cover an official-only jump, opposite directions and the inclusive
   boundary.
3. The alert key contained the observation date, so a steadily late source
   alerted as "new" every day. The key is now route, station and status; the
   full line stays in the message.
4. A failed send overwrote the status section and lost the memory, so a
   recovery could be lost. The failure path now keeps the memory.
5. The recovery and reminder emails read as incidents, and the reminder dated
   itself from the previous reminder. Each now has its own purpose, headline,
   action and subject. The reminder names `incidentSince`, which is kept
   across reminders and new problems and cleared on recovery; invalid values
   are dropped.
6. A `du` failure aborted the whole host check. As in the Ops reader, only the
   journal becomes unavailable now. Disk and inodes are still evaluated, and
   `journal=unavailable` is a warning that cannot close an incident.

Nits taken:

- `du` runs with `LC_ALL=C`, and the total is read by position;
- the reminder allows one hour for run-time jitter;
- the diagnosis stops at "va fi" and at a sentence boundary;
- a digit is required in the value;
- "ȋ" is normalized;
- the METODE numbers are the field's weekly means (+37.8 % / +19.1 %), and it
  says "at least";
- the unused `import math` is removed;
- the size assertion no longer depends on block allocation;
- the stale job descriptions in DEPLOY and ops/README are updated;
- the two UI-string assertions added earlier are replaced by behavioural node
  tests of `freshnessIssues`, `renderHero` and a new pure `ratioBreakMessage`.

Re-reading the archive for trend wording turned up two more upstream quirks
that predate this change: a Cyrillic "ӑ" (U+04D3), and the adjective placed
first ("în ușoarӑ creștere"), which the page showed as trend "ușoarӑ". Both
are normalized, with a test.

**Rerun.** The review-fix tests failed first against the reviewed candidate:
12 of 14 Python tests and 2 node tests, for the intended reasons. The
inclusive-threshold test passed, because it guards existing behaviour and
kills the surviving `>` mutation. After the fixes:

- `python3 -m unittest discover -s tests`: `Ran 184 tests`, `OK`, exit 0, no
  skips. Timings ranged from 5 s to 27 s with the workstation's load average
  near 12.
- `git diff --check` exit 0.
- `node --test tests/refresh.test.mjs`: pass 8, fail 0.
- The 30 archived bulletins still match production 30/30. Their trends now
  read `staționar` 13, `creștere` 9, `scădere` 7, `ușoară creștere` 1.

## Journald cap (host)

The operator authorized the cap. The session's auto-mode classifier refused
both the root Recipe and a local syntax check of its script, so the cap is
handed to the operator, as for Ops 0089. Nothing was attempted by another
route. The prepared, unexecuted scripts are in the session scratchpad:

- `journald-cap.sh`, the Ops 0089 drop-in `0x730-budget.conf`
  (`SystemMaxUse=192M`, `RuntimeMaxUse=32M`), prints before/after bytes, the
  drop-in hash, effective settings, the oldest retained entry and whether
  `dunarea` can measure the journal;
- `run-journald-cap.sh`, which creates one temporary root Recipe, verifies
  the stored script hash, runs it on server `949568`, polls the run, prints
  its output, deletes the Recipe and reads back the 404.

The Recipe schema used was read from Forge's current OpenAPI document:
create `POST /orgs/{org}/recipes` `{name, user, script}`; run
`POST …/recipes/{id}/runs` `{servers, email}` answering 202; runs expose
`status`, `output`, `started_at` and `finished_at`; delete answers 204.
Trimming the journal removes the oldest host log history for every co-tenant.
