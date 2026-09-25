# ops — backup și verificarea posturii

Utilitare fără pachete Python externe pentru backup, prospețime, igiena hostului
și verificarea posturii. Runbook-ul canonic este [DEPLOY.md](../DEPLOY.md),
descoperit prin [README.md](../README.md) și [AGENTS.md](../AGENTS.md).
Înainte de operații citiți și
[entrypoint-ul Ops](../../ops/0x730/PM/runbooks/devops-entrypoint.md),
[harta Danube](../../ops/0x730/PM/runbooks/devops-project-map.md#danube) și
[manifestul Danube](../../ops/packages/fleet/manifests/danube.json) din
checkout-ul Ops vecin. Nu există un client/deployer nou de instalat aici:
transportul Forge este `~/.forge/fc <path> [curl-args...]`.

## Agent workflow and source handoff

[AGENTS.md](../AGENTS.md) is canonical; [CLAUDE.md](../CLAUDE.md) explicitly
imports it. Procedures remain in README → DEPLOY → ops. For non-trivial work,
use one concise dated `ops/` note linked from the relevant docs and Unreleased;
the [13 September repair](agent-workflow-repair-evidence-2026-09-13.md) demonstrates
the contract. Routine wording changes need only proportionate diff/link evidence.

1. Record **ICE**: Intent (outcome and reason), Context (source state, constraints,
   dependencies), Expectations (observable acceptance and important failure cases).
   Source establishes implementation; accepted ICE/spec establishes intent.
   Reconcile the nearest spec/contract and note before implementation; discrepancies
   are defects or explicit accepted decisions, never acceptance rewritten to bless
   a regression. Preserve the scientific and operational invariants in AGENTS.
2. Write a durable **Plan** in that note: baseline revision/dirty work, accepted
   scope and authority, steps and paths, owners/exclusive resources, review and
   stop conditions. Native Plan mode remains the planning surface when available
   and selected; the session todo is its view, not a second durable plan. Record
   unavailable mode honestly and honor planning-only requests until execution is
   authorized. Existing execution/Git authority needs no repeated approval.
3. Before a behavior fix, demonstrate an independently failing test/evaluation of
   the intended contract. For documentation use direct document/link evaluation;
   do not add tests that merely pin prose. Extend an existing helper only for a
   demonstrated gap, with a regression first and its contract in the same commit.
4. Implement and verify the candidate; root owns the single test/generated-output
   lane. Record exact candidate identity, command, exit and relevant result, including
   failures, skips and justified retries. Capture status before trailing commands;
   with pipelines capture each needed status (`PIPESTATUS` immediately in Bash)
   and use `pipefail`. Wait for background work to finish; record timeouts/signals
   as incomplete or failed, never infer success from a log grep or plausible summary.
5. When required by the task/risk, use an independent read-only reviewer. Supply
   original acceptance, exact candidate/diff and evidence; name why delegation
   helps, repository, allowed actions/artifacts, integration/review owners,
   exclusive resources and stop conditions. Reviewer reports findings; maker
   fixes and reruns affected checks. Unavailable required review remains pending.
   A child changing directory gains no authority over another project.
6. Map accepted outcomes to diff and actual evidence, resolve findings, reconcile
   nearest docs/spec, note and CHANGELOG Unreleased, then choose a coherent
   commit/push checkpoint through normal hooks. Inspect all outgoing work and
   actual push automation, including Forge Quick Deploy per [DEPLOY.md](../DEPLOY.md).
   Verify the exact remote ref/revision with `git ls-remote`, against local HEAD
   and the fetched tracking ref. If remote work changed, preserve and reconcile
   it; do not force-push. Keep review, implementation, Git delivery, explicit
   deployment, public SHA/readiness, source freshness, provider acceptance,
   recipient receipt and restore proof separate. Old proof retains its date/target;
   unperformed and accepted-unproven gates remain explicit.

The required local checks, from the repository root, are:

```bash
python3 -m unittest discover -s tests -v
git diff --check
```

Also check changed Markdown targets/anchors, examples and applicable YAML.
For Bash examples use `bash -n` without executing operations; Forge template
directives need explicit parse-only substitution. Inspect effective
`git config --show-origin --get core.hooksPath` and the actual hooks directory.
At the dated repair checkpoint no active hooks were found: enforcement is manual,
not automatic or hosted CI. Do not install a hook framework just for uniformity.
There is no production build step. Passing document structure is not proof of the
requested behavior or a completed review/release.

The suite uses Python, Node, Git and Bash with temporary SQLite/filesystem/Git
fixtures and mocked external requests. Python may write bytecode caches.
`tests/test_freshness.py` already invokes `node --test tests/refresh.test.mjs`;
do not run a duplicate JS gate. Receipt tests exercise the real Python→Node
entrypoint in temporary repositories. Preserve/report the `openssl absent` skip
in `tests/test_ops.py`; a skip is not a pass. Do not promise a fixed suite duration.

`server.py` starts a listener and fetches/caches official data; GETs can populate
SQLite history/cache. `analiza_ai.py` is manual-only and can spend credits and
archive output. Revision/receipt writers write files. The operations below can
write, prune, upload, send messages or access authenticated services; do not run
them as document checks. Critical practices stay versioned, while memory access
and writes follow the active client/session authority. This contract adds no
private/global settings, memory-write, deployment, paid-AI or live-operation grant.

## Efectele comenzilor existente

Acestea sunt comenzi de operare pe host, nu verificări de documentație.
Inspectați parserul și funcția `main` înainte de utilizare; niciuna nu are un
gate generic `--write`.

| Comandă | Implicit / efecte și opțiuni |
| --- | --- |
| `backup.py` | Scrie copia SQLite și elimină copiile locale expirate (`--keep-days 14`). `--verify-only FIȘIER` verifică o copie existentă. |
| `offsite_backup.py backup` | Scrie backup local, staging și status, face upload Spaces și retenție locală/off-box; `--alert-on-failure` poate trimite email. |
| `offsite_backup.py monitor` | Citește obiectul off-box și scrie `--status-file` chiar fără alerte. `--alert` trimite la incident; `--test-alert` trimite mesaj real. |
| `offsite_backup.py restore-drill` | Citește/decriptează într-un director temporar, verifică și curăță copia; scrie status persistent. Nu acceptă destinație de producție. |
| `prune_releases.py --root CALE` | Dry-run implicit; `--apply` șterge release-urile din plan. Acceptă numai cele două rădăcini declarate. |
| `runtime_hygiene.py` | Citește presiunea hostului și scrie `--state-file` chiar fără `--alert`. `--test-alert` trimite un mesaj real, fără schimbarea stării incidentului. |
| `source_freshness.py` | Citește aplicația pe loopback și scrie `--status-file`, inclusiv fără `--alert`. `--alert` trimite la probleme noi, repetă un set neschimbat după 7 zile și trimite o revenire; `--test-alert` trimite email real. |
| `write_build_revision.py` | Scrie atomic `--output` din SHA-ul checkout-ului `--repository`, apoi apelează writer-ul canonic cu Node; scrie receipt-ul la rădăcina repo-ului. Eșecul oprește pregătirea release-ului. |
| `write-release-receipt.mjs` | Copie canonică Ops, apelată de entrypoint-ul Python; scrie `.release-receipt.json` în rădăcina Git, îmbină artefactele aceleiași revizii și refuză lipsa reviziei. |
| `verify_deploy.sh` | Verificare pe host: citiri locale/publice/off-box și workspace temporar 0700 cu cleanup; fără alertă sau schimbarea stării persistente. |

Statusul implicit pentru backup/source freshness este
`/home/dunarea/dunarea.info/backup-status.json`; pentru presiunea hostului este
`/home/dunarea/dunarea.info/runtime-hygiene-status.json`. O rulare fără flaguri
de alertă nu este automat read-only. Verificatorul izolează propriul status
off-box într-un director temporar; păstrați acest comportament.

Politica Cloudflare separată, inclusiv scopul exact al singurei reguli Free de
rate limiting și read-back-ul sanitizat, este în
[`cloudflare-edge-policy.md`](cloudflare-edge-policy.md). Ea nu schimbă scriptul
de deploy sau aplicația.

## `backup.py` — backup verificat al `cache.db`

```bash
python3 ops/backup.py --dest /home/dunarea/dunarea.info/backups --keep-days 14
python3 ops/backup.py --verify-only /home/dunarea/dunarea.info/backups/cache-2026-08-10.db
```

`cache.db` conține arhiva locală zilnică — singura parte a datelor care **nu se
poate reface** dintr-o sursă externă. De aceea backupul nu e considerat reușit
până nu e și verificat, iar verificarea numără explicit rândurile permanente.

De ce nu rețeta clasică `sqlite3 ... ".backup"`: utilitarul de linie de comandă
nu e instalat implicit pe un Ubuntu minimal, deci cronul ar eșua tăcut. Iar un
`cp` pe baza vie e greșit de două ori — poate prinde fișierul la mijlocul unei
tranzacții, iar de când baza rulează în `journal_mode=WAL` lasă în urmă
conținutul necheckpointat din `cache.db-wal`.

Ce face, în ordine: copiere online prin `Connection.backup()` (serverul poate
rămâne pornit), conversia copiei într-un singur fișier, verificare
(integritate, structură, rânduri permanente), apoi promovare atomică. Backupul
primește `600`, directorul `700`. Ieșire diferită de zero dacă ceva pică — deci
cronul vă poate anunța.

Pe orice eroare de copiere, `.partial`, `-wal` și `-shm` sunt eliminate. Testele
acoperă explicit un rând comis rămas în WAL și o schemă defectă care nu lasă
staging în urmă.

`ops/backup.py` nu mai este programat singur în producție. Rămâne primitiva
obligatorie apelată prima de jobul compus `2117004`; astfel copia locală zilnică
și verificarea WAL-aware sunt păstrate înaintea criptării sau a oricărui apel
Spaces.

**Restaurare:** opriți daemonul, înlocuiți `cache.db` cu backupul, reporniți.
Instanța pornește direct din arhivă și sare warmup-ul dacă `warmup_done` e în
copie. Un backup neîncercat rămâne o ipoteză — încercați-l periodic pe un port
separat, nu peste producție.

## `offsite_backup.py` — Spaces privat, freshness, alert și restore drill

```bash
python3 ops/offsite_backup.py backup --config /cale/offsite-backup.env \
  --source /cale/cache.db --dest /cale/backups --alert-on-failure
python3 ops/offsite_backup.py monitor --config /cale/offsite-backup.env --alert
python3 ops/offsite_backup.py restore-drill --config /cale/offsite-backup.env
```

Prefixul `database/danube/` este constant în cod. Configurația 0600 furnizează
o cheie Spaces readwrite limitată la bucketul privat comun, dar retenția nu
acceptă nicio cheie din alt prefix. Cheia de criptare Danube stă într-un al
doilea fișier 0600 și trebuie să fie distinctă de credentialele storage.

Succesul cere toate probele: `backup.py` WAL-aware, verificare SQLite, arhivă
permanentă nenulă, criptare autentificată, PUT privat, HEAD+GET semnate cu
mărime/SHA identice, refuz la GET nesemnat, retenție post-verificare și cleanup
al stagingului. `monitor` face un read-back independent și poate apela direct
Cloudflare Email Sending, fără Worker, cu token separat limitat la `Email
Sending: Edit`. Expeditorul folosește domeniul `0x730.com` deja onboarded;
`dunarea.info` nu este domeniu de email. Mesajul include un template HTML
autonom, email-safe, și fallback plain-text, fără resurse externe.
`restore-drill` nu acceptă cale destinație și nu poate suprascrie producția;
raportează doar număr de rânduri, RPO/RTO și cleanup, niciodată conținutul bazei.

Modelul exact, fără secrete, este `ops/offsite-backup.env.example`.

## `prune_releases.py` — maximum două release-uri pe hostul comun

```bash
python3 ops/prune_releases.py --root /home/dunarea/dunarea.info/releases
python3 ops/prune_releases.py --root /home/dunarea/dunarea.info/releases --apply
```

Fără `--apply`, scriptul este dry-run. Acceptă numai rădăcinile exacte Danube și
Portfolio, rezolvă obligatoriu symlinkul `current`, păstrează release-ul activ
și cel mai nou rollback și refuză symlinkuri sau fișiere neașteptate în
directorul de release. Nu atinge backupuri, cache, date persistente sau alte
site-uri.

## `runtime_hygiene.py` — o alertă pentru hostul fizic comun

```bash
python3 ops/runtime_hygiene.py
python3 ops/runtime_hygiene.py --alert
python3 ops/runtime_hygiene.py --test-alert
```

Monitorul agregă disk, inode și jurnal systemd pentru Forge `949568`, partajat
de Danube și Portfolio. Aplică exact ciclul Ops 80% warning / 90% critical /
sub 75% recovery, cu re-alertare la șase ore și o singură tranziție de recovery.
Jurnalul este măsurat ca Ops după cardul 0089: `du -s -c -B1` în locale `C` pe
`/var/log/journal` și `/run/log/journal`. `journalctl --disk-usage` rulat ca
`dunarea` numără doar jurnalele accesibile utilizatorului; Ops a măsurat
2,1 GiB pe hostul comun la 25 septembrie, peste pragul critic de 512 MiB. Ca la
Ops, numai jurnalul devine indisponibil dacă o parte nu poate fi citită: disk și
inode se evaluează în continuare, iar `journal=unavailable` este un warning,
nu o trecere, și nu închide un incident.
Starea atomică `0600` păstrează histerezisul; alerta reutilizează numai grupul
Cloudflare din configurația existentă și nu cere cheile S3. Configurația
[`logrotate/0x730-processes`](logrotate/0x730-processes) acoperă separat cele
două directoare de log Danube și toate căile declarate de procesele PM2:
`/home/forge/.pm2/logs/*.log` și
`/home/forge/swing.boostit.dev/logs/*.log`, în stanzas cu utilizatorii corecți.
System logrotate este singurul proprietar; modulul PM2 `pm2-logrotate` nu
rămâne instalat.

## `source_freshness.py` — sursele de date rămân proaspete

```bash
python3 ops/source_freshness.py                       # verificare + status, exit 0/1
python3 ops/source_freshness.py --alert               # e-mail la problemă nouă / săptămânal / revenire
python3 ops/source_freshness.py --test-alert          # probă de livrare
python3 ops/source_freshness.py --base-url https://dunarea.info  # de pe alt host
```

Aplicația își evaluează singură sursele: `stale: true` în payload înseamnă că
servește un snapshot de rezervă în locul unui fetch reușit. Separat,
`observation_freshness` detectează observații vechi sau fără dată verificabilă,
inclusiv un feed doar parțial vechi. Monitorul citește aceste evaluări, inclusiv
ruta secțiunilor DanubeHIS RO, și taskurile `maintenance` eșuate din health.
Politica exactă și sursele acoperite sunt în [API.md](../API.md#5-prospețime).
Cum se citește un incident real — livrare de rezervă din latență upstream
față de observație veche la sursă — în
[nota din 13 septembrie](source-freshness-incident-2026-09-13.md).
Nu deduce prospețimea dintr-o dată arbitrară dintr-un tabel istoric. Scriptul citește
aceste auto-evaluări de pe instanța locală (`127.0.0.1:7300`, nu prin
Cloudflare), plus erorile din `/api/overview` și vârsta raportului de anomalii
din `/api/health` (limită implicită 12 h), și iese non-zero când ceva nu e
proaspăt — Forge marchează job-ul eșuat. Cu `--alert` trimite e-mail prin
același canal Cloudflare Email Sending și același fișier de configurare 0600 ca
monitorul de backup (folosește numai grupul de chei de alertă; nu cere cheile
S3), enumerând exact sursele vinovate, cu template HTML autonom și fallback
plain-text. Dovada fiecărei rulări se scrie în secțiunea `sourceFreshness` a
fișierului de status, lângă secțiunile de backup.

Motivul scriptului: în august 2026 Hydroinfo a servit o săptămână snapshotul
din 25.08, corect marcat `stale` în API, și nimeni nu a aflat — alerta
existentă privea numai backup-urile.

Alerta ține minte ce a anunțat. Din 13 septembrie același gol DanubeHIS Gönyű
producea câte un e-mail pe zi, iar un incident nou ar fi sosit sub același
subiect zilnic. O problemă se identifică prin rută, stație și stare; data
observației și vârsta raportului rămân în mesaj, dar nu fac dintr-o întârziere
care avansează zilnic o problemă nouă. Cu `--alert`, scriptul trimite când apare
o problemă nealertată (marcată `NEW` dacă altele persistă), trimite o
reamintire săptămânală cât timp setul nu crește (cu o oră toleranță pentru
durata rulării) și o singură revenire când totul e din nou proaspăt. Un set care
se micșorează este reținut fără e-mail, ca o reapariție să fie din nou nouă.
Reamintirea și revenirea au subiect și text proprii; reamintirea numește
începutul incidentului (`incidentSince`). Secțiunea `sourceFreshness` păstrează
`alertDecision`, `alertedProblems`, `incidentSince`, `lastAlertAt` și
`lastRecoveryAt`; o memorie ilizibilă sau din viitor produce alertă, nu tăcere.
Și rularea eșuată, inclusiv un e-mail refuzat, păstrează memoria, ca revenirea
să nu se piardă; o revenire nelivrată iese non-zero și se reîncearcă a doua zi.
Altfel codul de ieșire nu se schimbă: jobul rămâne eșuat în Forge cât timp o
sursă nu e proaspătă. Rulările fără `--alert` și `--test-alert` nu modifică
memoria. [Dovada din 26 septembrie](live-data-review-2026-09-26.md).

### Joburi Forge instalate

- `2117004`, `15 3 * * *`, user `dunarea`: rulează
  `ops/offsite_backup.py backup`, care începe obligatoriu cu `ops/backup.py`,
  păstrează copia SQLite locală 14 zile, apoi criptează și verifică obiectul
  privat Spaces sub prefixul Danube;
- `2117005`, `17 8 * * *`, user `dunarea`: rulează
  `ops/offsite_backup.py monitor --max-age-hours 30 --alert` pentru read-back de
  prospețime și alertă Cloudflare la lipsă/eșec/stale;
- `2120262`, `25 9 * * *`, user `dunarea`: rulează
  `ops/source_freshness.py --alert` pentru prospețimea surselor de date și
  alertă Cloudflare la o problemă nouă de prospețime (livrare de rezervă sau
  observație veche, nedatată ori fără valoare), cu reamintire săptămânală și
  revenire;
- `2120431`, `13 * * * *`, user `dunarea`: rulează
  `ops/runtime_hygiene.py --alert`, singurul monitor de presiune pentru hostul
  comun. Programările de mai sus sunt în UTC; recitiți starea Forge înaintea
  unei schimbări.

Joburile sunt instalate în Forge, nu în crontab-ul vizibil utilizatorului.
Primele execuții programate și probele providerului anterior sunt consemnate ca
istoric în [DEPLOY.md](../DEPLOY.md#istoric-starea-recovery-verificată-la-28-august-2026).
Vechea instrucțiune de migrare TEM → Cloudflare este depășită: runbook-ul
consemnează configurația Cloudflare instalată și eliminarea cheilor legacy.
Această probă datată nu înlocuiește verificarea configurației, execuției,
acceptării providerului și primirii în inbox pentru o operație nouă. Dovada
[igienei din 1 septembrie](runtime-hygiene-evidence-2026-09-01.md) confirmă
separat acceptarea API a testului de presiune, fără probă de inbox pentru acel test.

## `write_build_revision.py` — checkout-ul care rulează

Deploy script-ul îl rulează în directorul noului release înainte de teste.
Fișierul `.build-revision` este generat atomic din `git rev-parse HEAD`, apoi
comparat din nou după activare înainte de restartul daemonului. `/api/health`
îl publică drept `buildSha`.

Același entrypoint apelează acum `node ops/write-release-receipt.mjs` cu
directorul de lucru `--repository` și `FORGE_DEPLOY_SHA` fixat la SHA-ul Git
deja verificat, pentru ca receipt-ul și `.build-revision` să aibă aceeași
identitate chiar dacă mediul moștenește alt SHA. Stdout rămâne un singur SHA;
mesajul writer-ului merge pe stderr în logul de deploy. Node trebuie să fie în
PATH; absența lui, un exit non-zero sau depășirea limitei de 30 s opresc
pregătirea. Funcția `write_revision()` își păstrează efectul atomic existent;
hook-ul este în `main()`.

### Receipt-ul release-ului

[write-release-receipt.mjs](write-release-receipt.mjs) este copiat **verbatim**
din `packages/fleet/kit/write-release-receipt.mjs` al checkout-ului Ops, fără
port Python și fără pachete npm. El păstrează rădăcina Git, îmbină artefactele
pentru aceeași revizie, pornește un receipt nou la altă revizie, consemnează
explicit artefactele lipsă și refuză scrierea fără revizie.

Mapping: `dunarea.info` / site Forge `3331936` / grup `web-api` →
`artifacts: {}`. Proiectul livrează sursele direct și nu are output de build
determinist de declarat. `.build-revision` variază cu SHA-ul, iar baza SQLite
și cache-urile variază la runtime; niciunul nu este artefact comparabil.
`.release-receipt.json` este gitignored și aparține fiecărui release, fără
shared path. Testele folosesc Git și Node local în directoare temporare,
inclusiv capturarea exactă `RELEASE_SHA` folosită de Forge.

La 10 septembrie, mecanismul este verificat și live prin deployment-ul Forge
`77396437`: receipt-ul din release corespunde reviziei `d155091ba056`, iar
logul conține linia writer-ului și 152 de teste reușite. Nu există un build
de producție separat în Danube. La fiecare deploy autorizat se verifică din
nou linia writer-ului din log și concordanța dintre `revision`, SHA-ul
Git/Forge, `.build-revision` și `/api/health.buildSha`, împreună cu acceptanța
din [DEPLOY.md](../DEPLOY.md). [Dovadă locală și live](release-receipt-evidence-2026-09-10.md).

## `verify_deploy.sh` — ce *este* configurat, nu ce *ar trebui*

```bash
cd /home/dunarea/dunarea.info/current
bash ops/verify_deploy.sh --origin-ip 157.90.144.210 --domain dunarea.info
```

Rulați pe hostul de producție. Folosește numai fișiere temporare cu cleanup și
citiri autentificate, fără modificări persistente sau mesaje. Verifică:
aplicația ascultă doar pe loopback, versiunea/SHA-ul și `.build-revision`, permisiunile
cheilor, `data/keys` neurmărit de git și absent din istoric, prospețimea și
integritatea celui mai recent backup, cronul, ufw, anteturile de securitate.

Cea mai importantă verificare combină `--origin-ip` cu `--domain`: `curl`
conectează la IP, dar trimite SNI și Host pentru domeniul real. Pe un server cu
mai multe vhost-uri, testarea simplă a `https://IP/` verifică doar catch-all-ul
și poate produce un fals pozitiv. Pentru producție, accesul prin Cloudflare
trebuie să dea 200 și accesul direct cu SNI `dunarea.info` trebuie să dea 403.

Ieșire diferită de zero dacă o verificare esențială pică. Citiți și avertismentele:
exit zero nu confirmă singur warmup, joburile Forge sau UFW când vizibilitatea
lipsește. Public `buildSha`, prospețimea surselor, primirea în inbox și restaurarea
se verifică separat conform [acceptanței](../DEPLOY.md#8-acceptanță-post-deploy).

Adoptarea DevOps, verificările locale și limitele probei sunt în
[nota din 6 septembrie](devops-adoption-evidence-2026-09-06.md).

Inventarul suprafeței publice, mărginirea parametrilor, reconcilierea mailului
de recovery cu manifestul și identitatea build-ului livrat sunt în
[nota din 9 septembrie](api-surface-and-recovery-review-2026-09-09.md).
Contractul pentru consumatorii API-ului este în [API.md](../API.md).
