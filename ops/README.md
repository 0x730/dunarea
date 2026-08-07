# ops — backup și verificarea posturii

Două scripturi, ambele fără dependențe externe, pentru lucrurile pe care
documentația le *descrie* dar nimeni nu le *verifică*.

## `backup.py` — backup verificat al `cache.db`

```bash
python3 ops/backup.py --dest /home/forge/backups --keep-days 14
python3 ops/backup.py --verify-only /home/forge/backups/cache-2026-08-07.db
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

Cron zilnic, ca utilizatorul `forge`:

```cron
15 3 * * * cd /home/forge/SITE && /usr/bin/python3 ops/backup.py \
           --dest /home/forge/backups --keep-days 14 >> /home/forge/backup.log 2>&1
```

**Restaurare:** opriți daemonul, înlocuiți `cache.db` cu backupul, reporniți.
Instanța pornește direct din arhivă și sare warmup-ul dacă `warmup_done` e în
copie. Un backup neîncercat rămâne o ipoteză — încercați-l periodic pe un port
separat, nu peste producție.

## `verify_deploy.sh` — ce *este* configurat, nu ce *ar trebui*

```bash
bash ops/verify_deploy.sh --origin-ip 203.0.113.10 --domain dunarea.exemplu.ro
```

Nu modifică nimic. Verifică: aplicația ascultă doar pe loopback, permisiunile
cheilor, `data/keys` neurmărit de git și absent din istoric, prospețimea și
integritatea celui mai recent backup, cronul, ufw, anteturile de securitate.

Cea mai importantă verificare e `--origin-ip`: dacă IP-ul de origine răspunde
direct pe 443, oricine poate ocoli Cloudflare, limitarea de rată și tot ce e în
față cerând direct IP-ul. **Rulați-o din afara rețelei Cloudflare** — de pe
serverul însuși poate răspunde chiar el și rezultatul nu e concludent.

Ieșire diferită de zero dacă o verificare esențială pică.
