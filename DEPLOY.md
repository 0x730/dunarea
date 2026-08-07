# Deploy pe Laravel Forge (Hetzner) + nginx + Cloudflare

Aplicația e un singur proces Python (stdlib, fără build), deci pe Forge se
rulează ca **daemon** cu **nginx în față** ca reverse proxy. Pași, în ordine:

## -1. Serverul Hetzner (dacă nu există deja)

- Forge → **Servers → Create Server → Hetzner Cloud** (conectezi API token-ul
  Hetzner din Cloud Console → Security → API Tokens).
- Mărime: **CX22 / CPX11 e mai mult decât suficient** — aplicația consumă
  ~100 MB RAM și aproape zero CPU între reîmprospătări; cache-ul stă sub 1 GB.
- Locație: Falkenstein/Nürnberg — latență mică și către sursele DE/AT, și
  către Cloudflare.
- Tip: **Web Server** (nu are nevoie de MySQL/Redis — poți debifa tot).
- Firewall: Forge configurează ufw cu 22/80/443 — exact ce trebuie; opțional,
  mai târziu, poți restrânge 80/443 la IP-urile Cloudflare.

## 0. Repo

```bash
# local (repo-ul e deja inițializat și comis):
git remote add origin git@github.com:CONTUL_TAU/danube-monitor.git
git push -u origin main
```

## 1. Site în Forge

- **Sites → New Site**: domeniul (ex. `dunarea.exemplu.ro`), project type
  **Static HTML** (nu instalăm PHP pentru el), web directory irelevant.
- **Git Repository**: conectează repo-ul, branch `main`.
- **Deploy Script** — înlocuiește tot cu:

```bash
cd /home/forge/dunarea.exemplu.ro
git pull origin $FORGE_SITE_BRANCH
# dependența opțională pentru gravimetria GRACE:
pip3 install --user --quiet h5py || true
# repornește aplicația după fiecare deploy (numele daemonului îl vezi la pasul 2):
sudo -S supervisorctl restart daemon-XXXXXX:* || true
```

(Prima dată lasă linia de supervisorctl comentată; o completezi după ce creezi
daemonul și îi afli numele.)

## 2. Daemon (procesul aplicației)

**Server → Daemons → New Daemon:**

- Command: `python3 server.py`
- Directory: `/home/forge/dunarea.exemplu.ro`
- User: `forge`
- **Environment** (aici stau secretele, NU în git — folosiți env, nu fișiere
  în directorul de deploy):
  ```
  PORT=7300
  MONITOR_TZ=Europe/Bucharest
  # jurnal: erorile și cererile lente se scriu întotdeauna; puneți
  # MONITOR_ACCESS_LOG=1 numai când depanați, altfel inundă supervisorul
  # MONITOR_ACCESS_LOG=1
  # MONITOR_SLOW_MS=5000
  HYDROWEB_KEY=cheia_ta_hydroweb
  AI_API_KEY=cheia_openai            # activează analiza narativă
  AI_MODEL=gpt-4o-mini               # opțional
  ENTSOE_TOKEN=tokenul_tau_daca_il_ai
  DAHITI_KEY=daca_apare_vreodata
  ```
- **Restart policy**: în Forge → daemon, lăsați `startsecs`/`startretries` la
  valorile implicite sau creșteți-le; aplicația sare singură peste warmup dacă
  a rulat în ultimele 6 ore, dar o buclă de repornire tot e de evitat.

Supervisor îl pornește, îl ține în viață și îl repornește la crash. La prima
pornire, warmup-ul durează 1–2 minute (snap-ul celulelor GloFAS + arhiva INHGA);
serverul ascultă imediat, doar unele carduri se umplu mai lent.

Fișierul GRDC (dacă îl obții): `scp` direct în
`/home/forge/dunarea.exemplu.ro/data/grdc/` — e în .gitignore, nu vine din repo.

## 3. Nginx

**Site → Edit Nginx Configuration** — în blocul `server { }`, înlocuiește
`location /` (și scoate `try_files`-ul de static) cu:

```nginx
    location / {
        proxy_pass         http://127.0.0.1:7300;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        # primele calcule (statistici/anomalii pe cache rece) pot dura ~1 min:
        proxy_read_timeout 180s;
        proxy_connect_timeout 10s;
    }
```

Adăugați și o limită de rată (aplicația interoghează surse oficiale — nu vrem
ca cineva să le bombardeze prin ea). În blocul `http { }` din
`/etc/nginx/nginx.conf`:

```nginx
limit_req_zone $binary_remote_addr zone=api:10m rate=5r/s;
```

…iar în `location /` din configurația sitului:

```nginx
        limit_req zone=api burst=20 nodelay;
```

Apoi restart nginx din Forge. Aplicația își servește singură staticele —
nginx-ul doar proxy-ază tot. Aplicația trimite `Content-Security-Policy`,
`X-Content-Type-Options` și `Referrer-Policy` pe fiecare răspuns, inclusiv pe
erorile generate de biblioteca standard (501/505/414), pentru că anteturile
sunt emise din `send_response`, prin care trec toate răspunsurile.

**Nu adăugați `proxy_set_header Connection "";` fără să știți ce faceți.**
Împreună cu `proxy_http_version 1.1` de mai sus, activează keep-alive între
nginx și aplicație. Aplicația închide explicit conexiunea pe 405 și refuză
cererile cu corp, deci nu se desincronizează — dar keep-alive-ul face ca o
singură cerere numărată de `limit_req` să poată transporta mai multe cereri
către aplicație.

## 4. Cloudflare

- **DNS**: înregistrare `A` pentru `dunarea` → IP-ul serverului Forge,
  **Proxied** (nor portocaliu).
- **Restricționați 80/443 la IP-urile Cloudflare** (Forge → Network, sau ufw
  cu lista de la cloudflare.com/ips) — altfel IP-ul de origine rămâne
  accesibil direct și ocolește orice protecție Cloudflare.
- **SSL/TLS → Overview**: modul **Full (strict)**.
- **Certificat pe origine** — două variante, oricare merge:
  - Forge → site → **SSL → LetsEncrypt** (funcționează și cu norul portocaliu
    activ — Cloudflare lasă `/.well-known/acme-challenge` să treacă); sau
  - Cloudflare → **Origin Server → Create Certificate**, apoi Forge → SSL →
    **Install Existing Certificate** (valabil 15 ani, fără reînnoiri).
- Opțional, ca să nu bată nimeni serverul degeaba:
  - **Cache Rule**: `/vendor/*` → Cache eligible, Edge TTL 1 lună (ECharts,
    1 MB, nu se schimbă niciodată);
  - **Cache Rule**: `/api/*` → Bypass cache (aplicația își face singură
    cache-ul pe surse; nu vrem staleness dublu).

## 5. Verificare după deploy

```bash
curl -s https://dunarea.exemplu.ro/api/health         # → stare proces
curl -s https://dunarea.exemplu.ro/api/istoric        # → JSON cu arhiva
curl -s -o /dev/null -w "%{http_code}\n" https://dunarea.exemplu.ro/   # → 200
```

`/api/health` e ieftin și **nu** interoghează nicio sursă externă: raportează
doar uptime-ul, dacă warmup-ul s-a terminat și vechimea raportului de anomalii.
Potrivit ca health check pentru supervisor sau pentru un monitor extern — un
endpoint care ar declanșa fetch-uri ar fi și o pârghie de amplificare, și ar
raporta „bolnav" când de fapt doar o sursă e jos. Prospețimea fiecărei surse
rămâne în `/api/overview`.

Pastilele din header-ul paginii arată live starea fiecărei surse; dacă vreuna
e roșie permanent după ~5 minute de la pornire, `supervisorctl tail -f
daemon-XXXXXX` pe server arată de ce.

## Verificarea posturii de exploatare

Documentația de mai jos descrie ce ar *trebui* configurat. Ce *este* configurat
se verifică rulând pe server:

```bash
bash ops/verify_deploy.sh --origin-ip IP_SERVER --domain dunarea.exemplu.ro
```

Verifică portul aplicației (numai loopback), permisiunile cheilor, faptul că
`data/keys` nu e urmărit de git, prospețimea și integritatea backupului, cronul,
ufw, anteturile de securitate și — cel mai important — dacă IP-ul de origine
răspunde direct pe 443. Ultima verificare e concludentă numai rulată din afara
rețelei Cloudflare: dacă originea răspunde direct, oricine poate ocoli
Cloudflare, limitarea de rată și tot ce e în față, cerând direct IP-ul.

## Note de exploatare

- `cache.db` se creează singur pe server (nu vine din git). Poate fi șters,
  dar **evitați**: conține arhiva locală zilnică (AFDJ/RHMZ/DanubeSTREAM/SEN/
  INHGA/analize AI), care crește în valoare cu timpul. Ștergerea declanșează
  și o reconstrucție costisitoare (GRACE ~10 min, snap GloFAS ~2 min).
- **Backup corect.** `cp` pe baza vie e greșit de două ori: poate prinde
  fișierul la mijlocul unei tranzacții, iar de când baza rulează în
  `journal_mode=WAL` lasă în urmă ce nu e încă checkpointat în `cache.db-wal`.
  Rețeta cu `sqlite3 ... ".backup"` are altă problemă: **utilitarul de linie de
  comandă nu e instalat implicit** pe un Ubuntu minimal, deci cronul ar fi
  eșuat tăcut, iar dumneavoastră ați fi crezut că aveți backupuri.

  Folosiți scriptul din repo, care merge cu interpretorul deja instalat:
  ```bash
  # cron zilnic, ca utilizatorul forge:
  15 3 * * * cd /home/forge/SITE && /usr/bin/python3 ops/backup.py \
             --dest /home/forge/backups --keep-days 14 >> /home/forge/backup.log 2>&1
  ```
  Copiază online (fără oprirea serverului), convertește copia într-un singur
  fișier, o verifică — integritate, structură, numărul de rânduri permanente —
  și abia apoi o promovează atomic. Ieșire diferită de zero dacă ceva pică, deci
  cronul vă poate anunța. Backupul primește 600, directorul 700.

  **Probă de restaurare** — un backup neverificat este o ipoteză:
  ```bash
  python3 ops/backup.py --verify-only /home/forge/backups/cache-2026-08-07.db
  # restaurare completă: opriți daemonul, înlocuiți cache.db, reporniți
  ```
  Probă făcută pe 2026-08-07: instanța pornită pe o copie restaurată a servit
  aceleași valori ca originalul (14 secțiuni de climatologie, același z al
  bilanțului) și a sărit warmup-ul, adică arhiva locală a supraviețuit intactă.
- Cache-ul se curăță singur (rânduri expirate de peste 30 de zile, o dată pe
  zi); cheile permanente — arhiva locală — sunt protejate explicit.
- Aplicația nu acceptă niciun verb care modifică (POST/PUT/PATCH/DELETE dau
  405) și nu are autentificare, pentru că nu expune nimic privat. Atenție însă
  la formularea exactă: **un GET public poate scrie** — populează `cache.db` și,
  prin `daily_snapshot()`, adaugă rânduri permanente în arhiva locală; tot un
  GET consumă cota de API a operatorului la HydroWeb/DAHITI/ENTSO-E. Nu e
  „fără scriere din exterior", ci „fără scriere de conținut din exterior".
- Cheile se citesc din env-ul daemonului **sau**, dacă env-ul nu le are, din
  `data/keys/` (ignorat de git, chmod 600). Pe server preferați env-ul. Cheile
  **nu** ajung în `cache.db`, în răspunsuri sau în mesajele de eroare —
  verificat empiric pe baza vie și pe modurile reale de eșec.
- Analiza AI nu apare în UI, nu rulează în fundal și nu poate fi pornită prin
  HTTP; se lansează local, numai la cererea operatorului, cu
  `python3 analiza_ai.py`. Endpointul `/api/analiza-ai` întoarce doar acest
  statut manual, iar patru teste blochează regresia.
- Dependența `h5py` (doar pentru gravimetria GRACE) e opțională: dacă
  instalarea eșuează, cardul respectiv se dezactivează singur, restul merge.
- `static/vendor/echarts.min.js` e livrat cu `integrity=` (SRI). Dacă
  actualizați biblioteca, recalculați hash-ul și înlocuiți-l în `index.html`,
  altfel browserul refuză să execute scriptul și graficele dispar:
  ```bash
  openssl dgst -sha384 -binary static/vendor/echarts.min.js | openssl base64 -A
  ```
