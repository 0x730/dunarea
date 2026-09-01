# ops — backup și verificarea posturii

Utilitare fără pachete Python externe pentru lucrurile pe care documentația le
*descrie* dar nimeni nu le *verifică*.

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
python3 ops/source_freshness.py                       # doar verificare, exit 0/1
python3 ops/source_freshness.py --alert               # e-mail numai la incident
python3 ops/source_freshness.py --test-alert          # probă de livrare
python3 ops/source_freshness.py --base-url https://dunarea.info  # de pe alt host
```

Aplicația își evaluează singură sursele: `stale: true` în payload înseamnă că
servește un snapshot de rezervă în locul unui fetch reușit. Scriptul citește
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
  alertă Cloudflare când o sursă servește snapshot de rezervă.

Joburile sunt instalate în Forge, nu în crontab-ul vizibil utilizatorului.
Primele execuții programate și probele providerului anterior sunt consemnate în
`DEPLOY.md`. Testul controlat Cloudflare din checkout a trecut, dar migrarea de
producție nu este închisă până la schimbarea configurației 0600 în aceeași
sesiune cu deploy-ul explicit și până la testul post-deploy.

## `write_build_revision.py` — checkout-ul care rulează

Deploy script-ul îl rulează în directorul noului release înainte de teste.
Fișierul `.build-revision` este generat atomic din `git rev-parse HEAD`, apoi
comparat din nou după activare înainte de restartul daemonului. `/api/health`
îl publică drept `buildSha`.

## `verify_deploy.sh` — ce *este* configurat, nu ce *ar trebui*

```bash
bash ops/verify_deploy.sh --origin-ip 157.90.144.210 --domain dunarea.info
```

Nu modifică nimic. Verifică: aplicația ascultă doar pe loopback, permisiunile
cheilor, `data/keys` neurmărit de git și absent din istoric, prospețimea și
integritatea celui mai recent backup, cronul, ufw, anteturile de securitate.

Cea mai importantă verificare combină `--origin-ip` cu `--domain`: `curl`
conectează la IP, dar trimite SNI și Host pentru domeniul real. Pe un server cu
mai multe vhost-uri, testarea simplă a `https://IP/` verifică doar catch-all-ul
și poate produce un fals pozitiv. Pentru producție, accesul prin Cloudflare
trebuie să dea 200 și accesul direct cu SNI `dunarea.info` trebuie să dea 403.

Ieșire diferită de zero dacă o verificare esențială pică.
