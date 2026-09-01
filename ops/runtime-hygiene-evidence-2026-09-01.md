# Shared-host runtime hygiene evidence

**Date:** 2026-09-01

**Host:** Forge `949568`, shared by Danube and Portfolio

**Policy:** `fleet-runtime-hygiene-v1`

## Scope and safety

This checkpoint changes only process-log rotation and the existing host-wide
disk-pressure alert. It does not deploy product code or alter databases,
backups, sites, product processes, release roots, or unrelated jobs. Provider
acceptance is recorded separately from inbox receipt.

## Pre-change readback

- `/etc/logrotate.d/0x730-processes` was root-owned `0644` and covered only
  `/home/dunarea/.forge/*.log` and `/home/dunarea/dunarea.info/*.log` with the
  fleet contract.
- `pm2-logrotate` `2.7.0` owned the saved PM2 processes' logs under both
  `/home/forge/.pm2/logs` and the custom
  `/home/forge/swing.boostit.dev/logs` path, with daily rotation, compression,
  `max_size=10M`, and `retain=7`. It had no 14-day maximum or
  delayed-compression contract.
- No `/etc/logrotate.conf` or `/etc/logrotate.d/*` file referred to either PM2
  path.
- Root disk was 32%, root inodes were 12%, and the system journal was
  178.8 MiB. Active PM2 logs were below 20 MiB.
- Forge scheduled job `2120431` was already the only shared-host pressure job:
  hourly at minute 13 as `dunarea`, using the existing Cloudflare alert config
  and owner-only state file.

## Post-change evidence

- Temporary root Recipe run `366568` finished the initial transition. Final
  saved-process readback then surfaced Swing's custom PM2 output path, so the
  same source-owned stanza was extended to preserve that already-existing
  rotation coverage. Final validation run `366572` from temporary Recipe
  `111925` installed the expanded policy and reported the exact checksum plus
  an enabled/active timer. The final `/etc/logrotate.d/0x730-processes` is
  root-owned `0644`, matches the source, and has SHA-256
  `242e02e98f41097e7037323f0637666debe00d9e8dac29aae47704f1afe79aa8`.
- The installed file has exactly two ownership stanzas. The `forge` stanza
  references `/home/forge/.pm2/logs/*.log` and
  `/home/forge/swing.boostit.dev/logs/*.log` exactly once each. Both stanzas use
  daily/20 MiB rotation, 14 generations, 14 days, compression, delayed
  compression, and `copytruncate`.
- The PM2 module process, module directory, and module configuration are all
  absent. No other system-logrotate file refers to either PM2 log path.
  Portfolio process `site-3235234` and existing process `0x730-swingbot` stayed
  online after the owner transition; neither process or site was reconfigured.
- Sixteen expired archives created by the retired PM2 module, totaling 2,707
  bytes, were removed. They are not recoverable; 17 in-policy recent archives
  and every active log were preserved.
- The no-disk-fill exercise used the deployed decision and state-write path
  with a temporary owner-only state file and a mocked sender. Its exact
  lifecycle was `warning: 1 message`, immediate warning repeat: `0`, recovery
  at 74%: `1`, and repeated healthy state: `0`. The temporary state was
  removed automatically.
- A separate live `--test-alert` used the existing Cloudflare transport and
  returned `testAlertAccepted=true`. This means the strict provider response
  contained the recipient as `delivered` or `queued`; inbox receipt was not
  observed or claimed. The test did not change persistent incident state.
- A normal live run observed disk 32% and inodes 12%, sent no message, and
  left the `0600` state `healthy`, `active=false`. Forge readback shows exactly
  one host-pressure job, `2120431`, installed hourly at minute 13 as `dunarea`.
- The full seven-project read-only hygiene rerun was
  `115 pass / 3 fail / 2 to_verify / 0 exception`, with zero critical failures
  and zero product/provider writes. All three shared-host rotation checks pass;
  the shared host's only remaining row is the Ops manifest still declaring the
  now-evidenced disk alert `absent`. That registry reconciliation belongs to a
  later Ops read-only/control-plane checkpoint, not this owning-product write.

The completed temporary Forge Recipe was deleted (`HTTP 204`), its local
payload and remote temporary state were removed, and no deployment was run.
