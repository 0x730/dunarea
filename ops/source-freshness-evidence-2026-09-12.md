# Source freshness and automatic refresh — 12 September 2026

## Trigger and scope

Read-only audit of production revision `e4f75f64a74f` found a successful
DanubeHIS response containing Gönyű dated 13 August (30 days old), alongside
current stations. Its delivery flag was `stale: false`. The existing monitor
recorded `fresh` at `2026-09-12T09:25:02Z`, across 15 endpoints. AFDJ's 23
stations and Hydroinfo's 93 stations were dated 12 September. ANAR's 46-day-old
notice was correctly historical-only. These are dated observations, not a
claim of permanent upstream availability.

The user authorized the four improvements, commit/push, and explicit deployment.
No additional job, alert transport, host policy, or data archive is introduced.

## Implementation

- `freshness.py` applies explicit observation-age policies after cache reads.
  Delivery `stale` retains its meaning; `observation_freshness` reports mixed
  dates, missing/invalid/future dates and individual station problems. Historical
  context keeps its existing classification. [API contract](../API.md#5-prospețime).
- `server.py` source wrappers preserve cache age and delivery metadata, including
  inputs to Romania and downloadable reports. Report schema keys advance;
  source caches and permanent history remain intact.
- `maintenance_cycle()` isolates each existing task, exposes bounded status in
  health, logs only the task name on failure, and retries failed daily work on
  the next 30-minute cycle. Successful daily work retains its daily cadence.
- The existing `ops/source_freshness.py` still writes status and optionally
  alerts; it now reads observation assessments and maintenance failures. Its
  installed daily schedule and transport remain unchanged.
- Browser refreshes share one complete cycle, await asynchronous panels, bound
  requests to 45 seconds, pause while hidden and resume on visibility. Warnings
  use text content; stale DanubeHIS fallback points are excluded from the
  current profile.

## Validation and release gates

Local regression checks cover mixed station dates (including Gönyű), publication
boundaries, timezone offsets, missing/future dates, independent Q/W timestamps,
historical context, monitor aggregation, retained input fallback, independent
maintenance failure/retry, and browser concurrency/visibility/deadline behavior.
Browser behavior tests use Node's built-in runner through the Python suite.

Release prepared: `v1.1.1`. All 161 Python tests passed for the final versioned source;
`git diff --check`, JavaScript syntax and changed local documentation links
also passed. Four browser behavior tests run within that suite. A Chromium smoke using the captured public data
and simulated unavailable endpoints showed the Gönyű warning and amber
DanubeHIS indicator, no uncaught page errors, and no horizontal overflow at
390 px; desktop 1280 px and mobile screenshots were inspected. This fixture
smoke does not prove upstream availability.

Provider preflight confirmed site `3331936`, `0x730/dunarea` / `main`, user
`dunarea`, Quick Deploy off, zero downtime on, retention 1; process `1006295`
running with the declared command/directory; source monitor `2120262` installed
at `25 9 * * *`; and the dedicated deploy script containing revision/receipt,
Python tests, activation, exact-SHA checks, process restart and the existing
bounded release pruner. No provider configuration change is needed.

This note is committed with the implementation before deployment. The returned
Forge ID, completion, exact remote/runtime SHA and host acceptance are reported
in the session handoff after deployment, separately from these preparation
checks. Recipient receipt and restore proof remain separate; this release does
not exercise a real alert or a restore.
