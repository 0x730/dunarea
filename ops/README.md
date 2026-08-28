# ops — backup și verificarea posturii

Patru scripturi, fără pachete Python externe, pentru lucrurile pe care
documentația le *descrie* dar nimeni nu le *verifică*.

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
al stagingului. `monitor` face un read-back independent și poate folosi
Scaleway TEM fără serviciu nou. `restore-drill` nu acceptă cale destinație și nu
poate suprascrie producția; raportează doar număr de rânduri, RPO/RTO și cleanup,
niciodată conținutul bazei.

Modelul exact, fără secrete, este `ops/offsite-backup.env.example`.

### Joburi Forge instalate

- `2117004`, `15 3 * * *`, user `dunarea`: rulează
  `ops/offsite_backup.py backup`, care începe obligatoriu cu `ops/backup.py`,
  păstrează copia SQLite locală 14 zile, apoi criptează și verifică obiectul
  privat Spaces sub prefixul Danube;
- `2117005`, `17 8 * * *`, user `dunarea`: rulează
  `ops/offsite_backup.py monitor --max-age-hours 30 --alert` pentru read-back de
  prospețime și acceptare TEM la lipsă/eșec/stale.

Ambele joburi sunt instalate în Forge, nu în crontab-ul vizibil utilizatorului.
Proba manuală este verde, dar primele execuții programate rămân de verificat pe
28 august 2026 după 03:15, respectiv 08:17 UTC.

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
