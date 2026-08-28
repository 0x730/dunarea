# Producție: Laravel Forge + Hetzner + Cloudflare

Acesta este runbook-ul canonic pentru `https://dunarea.info`. Valorile secrete
nu apar aici și nu trebuie adăugate vreodată în repo; repo-ul GitHub este public.

## Starea instalată la 28 august 2026

- stare publică: **deployed** la [https://dunarea.info](https://dunarea.info/);
- release instalat: [`v1.0.6`](https://github.com/0x730/dunarea/tree/v1.0.6);
- sănătate runtime: [https://dunarea.info/api/health](https://dunarea.info/api/health);
- server Hetzner existent, administrat prin Laravel Forge: `157.90.144.210`;
- site Forge izolat, utilizator Unix `dunarea`;
- repo GitHub `0x730/dunarea`, branch `main`;
- deploy-uri Forge zero-downtime, cu patru release-uri păstrate;
- proces Python administrat ca Forge Background Process;
- aplicația ascultă numai pe `127.0.0.1:7300`;
- Nginx reverse proxy pentru `dunarea.info`;
- Cloudflare proxied (nor portocaliu), SSL/TLS `Full (strict)`;
- certificat Let's Encrypt administrat de Forge prin DNS-01;
- baza SQLite și cheile sunt shared paths între release-uri;
- pagina de start declară exact canonicalul `https://dunarea.info/`, iar
  `/sitemap.xml` servește sitemap XML pentru cele șapte vederi publice;
- job Forge `2117004` instalat la `03:15 UTC`: copie SQLite locală verificată,
  apoi criptare și upload în Spaces sub prefixul Danube;
- job Forge `2117005` instalat la `08:17 UTC`: read-back de prospețime și alertă
  la lipsă/eșec/stale prin Cloudflare Email Sending, cu token separat și
  expeditor `alerts@0x730.com`;
- Quick Deploy este `false`. Workflow-ul manual are două acțiuni explicite în
  aceeași sesiune a proprietarului: push-ul commit-ului curat, apoi invocarea
  deploy-ului prin Forge API. Push-ul singur nu pornește producția.

Serverul găzduiește și alte site-uri. Nu restrângeți global porturile 80/443 și
nu modificați procesele, vhost-urile sau firewall-ul celorlalte aplicații.
Restricția Cloudflare este aplicată numai vhost-ului Danube.

## 1. Modelul de deploy Forge

Site-ul este conectat la GitHub prin integrarea Forge. Deploy script-ul live:

```bash
set -euo pipefail

$CREATE_RELEASE()

cd $FORGE_RELEASE_DIRECTORY
RELEASE_SHA="$(python3 ops/write_build_revision.py \
  --repository "$FORGE_RELEASE_DIRECTORY" \
  --output "$FORGE_RELEASE_DIRECTORY/.build-revision")"
python3 -m unittest discover -s tests

$ACTIVATE_RELEASE()

test "$(git -C /home/dunarea/dunarea.info/current rev-parse HEAD)" = "$RELEASE_SHA"
test "$(cat /home/dunarea/dunarea.info/current/.build-revision)" = "$RELEASE_SHA"
sudo supervisorctl restart daemon-ID_DIN_FORGE:*
```

ID-ul procesului rămâne în Forge, nu în repo. Ordinea este intenționată:
release-ul nu devine activ dacă revizia nu poate fi legată de checkout sau dacă
un test eșuează. După activare, aceleași 40 de cifre hex trebuie să existe în
Git HEAD și în `.build-revision`; procesul este repornit numai după aceste două
probe. `/api/health.buildSha` citește exclusiv fișierul din release-ul care
rulează, nu o valoare de mediu ce ar putea deriva.

În acest runbook, „deploy manual” înseamnă exact workflow-ul owner-session în
doi pași, nu Quick Deploy și nu un webhook pornit automat de push:

1. validați și împingeți commit-ul/tag-ul curat, apoi confirmați SHA-ul remote;
2. în aceeași sesiune, invocați explicit `POST
   /orgs/{organization}/servers/{server}/sites/{site}/deployments` prin Forge
   API și verificați deployment ID-ul rezultat.

Nu activați Quick Deploy. Existența commit-ului pe `origin/main` nu constituie
dovadă de deploy; numai deployment-ul API terminat și SHA-ul public îl închid.

Înainte de un deploy manual:

```bash
git status --short --branch
git fetch origin main
git rev-parse HEAD
git rev-parse origin/main
python3 -m unittest discover -s tests
```

După deploy, verificați în Forge că statusul este `finished`, logul conține
toate testele și commit-ul este cel așteptat. Verificați și că `version` din
`/api/health` coincide cu fișierul `VERSION` și cu tag-ul release-ului, iar
`buildSha` coincide cu SHA-ul complet din Forge și GitHub.

## 2. Date persistente și secrete

Forge shared paths:

```text
/home/dunarea/dunarea.info/cache.db  -> current/cache.db
/home/dunarea/dunarea.info/data/keys -> current/data/keys
```

Permisiuni obligatorii:

```text
.env                       0600
data/keys/                 0700
data/keys/*.key            0600
cache.db                   0600
backups/                   0700
backups/cache-*.db         0600
backup-staging/            0700 (gol între rulări)
backup-status.json         0600
data/keys/offsite-backup.env             0600
data/keys/danube-backup-encryption.key    0600
```

În producție este instalată cheia HydroWeb. Cheia OpenAI nu este necesară
pentru runtime-ul web: analiza AI este o acțiune manuală de operator și nu se
pornește prin HTTP. ENTSO-E și DAHITI rămân opționale până când există
credentiale dedicate.

Nu puneți niciodată în GitHub:

- tokenurile Forge, Cloudflare, Hetzner sau API-urile sursă;
- chei private SSH, fișiere `.env`, deploy-hook URLs sau certificate private;
- conținutul din `data/keys/` ori o copie a `cache.db`;
- output verbose de la `curl` autentificat, deoarece poate tipări headere.

Tokenurile operatorului rămân în fișiere locale `0600`, în afara repo-ului.

## 3. Procesul aplicației

Forge Background Process:

```text
Command:   env PORT=7300 MONITOR_TZ=Europe/Bucharest python3 server.py
Directory: /home/dunarea/dunarea.info/current
User:      dunarea
Processes: 1
Signal:    SIGTERM
```

`python3-h5py` este instalat din pachetele Ubuntu pentru gravimetria GRACE.
Restul aplicației folosește biblioteca standard Python și nu are pas de build.

Verificare locală pe server:

```bash
curl -fsS http://127.0.0.1:7300/api/health
ss -ltn | grep ':7300'
python3 -c 'import h5py; print(h5py.__version__)'
```

Portul 7300 trebuie să apară numai pe loopback. `warmup_done` trebuie să devină
`true`; la un cache importat și proaspăt acest lucru se întâmplă imediat.

## 4. Nginx și protecția originii

Vhost-ul Forge proxy-ează toate rutele către `http://127.0.0.1:7300`, cu:

```nginx
location / {
    limit_req zone=danube_api burst=120 nodelay;
    proxy_pass http://127.0.0.1:7300;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 180s;
    proxy_connect_timeout 10s;
}
```

Un fișier în `/etc/nginx/conf.d/` definește:

- zona `danube_api`, `5r/s`, folosită numai de acest vhost;
- un `geo` construit din listele oficiale Cloudflare IPv4/IPv6;
- loopback ca excepție pentru verificările locale.

Vhost-ul restabilește IP-ul vizitatorului numai pentru peer-uri Cloudflare și
respinge cu 403 orice peer care nu este Cloudflare. Verificarea se bazează pe
`$realip_remote_addr` (peer-ul TCP original), nu pe un header care poate fi
falsificat. Când Cloudflare schimbă listele publicate, actualizați simultan
`set_real_ip_from` și harta `geo`, apoi rulați `nginx -t` înainte de reload.

Nu copiați în repo configurația live completă: Forge scrie acolo căile
certificatului și identificatorii interni, care se pot schimba la reînnoire.
Păstrați markerii `FORGE CONFIG` și `FORGE SSL` când editați vhost-ul prin API.

## 5. Cloudflare și TLS

În zona `dunarea.info`:

```text
A      dunarea.info      -> 157.90.144.210   Proxied
CNAME  www               -> dunarea.info     Proxied
```

Cele două CNAME-uri `_acme-challenge` furnizate de Forge rămân permanent
`DNS only`. Forge folosește delegarea DNS-01 pentru emitere și reînnoire; dacă
aceste înregistrări sunt șterse, reînnoirea certificatului va eșua.

Cloudflare SSL/TLS trebuie să rămână `Full (strict)`. `/api/*` nu trebuie
cache-uit la edge; aplicația gestionează prospețimea și cadențarea surselor.
Fișierele versionate din `/vendor/*` pot primi un TTL lung dacă se adaugă o
regulă explicită.

### Limitare edge a API-ului

Pe 28 august 2026, zona Free `dunarea.info` a primit exact o regulă Cloudflare
`http_ratelimit`, limitată la `starts_with(http.request.uri.path, "/api/")`.
Regula numără `ip.src` (plus `cf.colo.id`, cerut de Cloudflare), permite 60
cereri în 10 secunde și blochează 10 secunde după depășirea pragului. Danube nu
are endpointuri de autentificare sau scriere: toate cele 47 rute `/api/` acceptă
doar `GET`/`HEAD`, iar metodele de scriere primesc `405`. Nu include pagini,
asset-uri sau `/vendor/*` și nu se adaugă o a doua regulă.

DMARC este deja strict `p=reject`; pentru acest proiect s-a verificat, nu s-a
modificat. Nu activați aici o adresă de raportare, inbox, furnizor sau produs
DMARC nou. Detaliile sanitizate de read-back și testul `429` sunt în
[`ops/cloudflare-edge-policy.md`](ops/cloudflare-edge-policy.md).

Verificări din afara serverului:

```bash
curl -fsS -D - https://dunarea.info/api/health -o /dev/null
curl -sS -o /dev/null -w '%{http_code}\n' \
  --resolve dunarea.info:443:157.90.144.210 \
  https://dunarea.info/api/health
```

Primul răspuns trebuie să fie 200 și să conțină `cf-ray`. Al doilea trebuie să
fie 403: conectează direct la origine, dar păstrează SNI/Host corect.

## 6. Backup local, copie criptată off-box și restaurare de probă

`ops/offsite_backup.py` nu înlocuiește backupul SQLite. Îl apelează întâi pe
`ops/backup.py`, cere din nou integritate, schema `cache` și cel puțin un rând
permanent, apoi criptează client-side. Jobul Forge compus rulează ca `dunarea`:

```cron
15 3 * * * cd /home/dunarea/dunarea.info/current && /usr/bin/python3 ops/offsite_backup.py backup \
  --config /home/dunarea/dunarea.info/data/keys/offsite-backup.env \
  --source /home/dunarea/dunarea.info/cache.db \
  --dest /home/dunarea/dunarea.info/backups --keep-days 14 \
  --offsite-keep-days 30 --alert-on-failure \
  >> /home/dunarea/dunarea.info/backup.log 2>&1
```

`ops/backup.py` folosește API-ul SQLite online, include WAL-ul, verifică
integritatea și rândurile permanente, apoi promovează copia atomic. Un `cp` al
bazei active nu este un backup corect.

Obiectul are forma fixă
`database/danube/AAAA/LL/danube-AAAALLZZTHHMMSSZ.sqlite3.enc`. AES-256-CTR cu
PBKDF2-SHA-512 criptează copia; un HMAC-SHA-256 cu o subcheie scrypt distinctă
autentifică antetul, contextul exact al obiectului și ciphertext-ul. Cheia
Danube nu este credentialul Spaces și nu este partajată cu alt produs.
Uploadul cere `ACL private`, apoi un HEAD și un GET semnate trebuie să reproducă
mărimea și SHA-256; un GET nesemnat trebuie să primească 403/404. Retenția de 30
zile rulează numai după aceste probe și poate șterge exclusiv chei parseabile
sub prefixul constant `database/danube/`.

Monitorul independent rulează după fereastra backupului și alertează numai o
copie lipsă/eșuată/mai veche de 30h:

```cron
17 8 * * * cd /home/dunarea/dunarea.info/current && /usr/bin/python3 ops/offsite_backup.py monitor \
  --config /home/dunarea/dunarea.info/data/keys/offsite-backup.env \
  --max-age-hours 30 --alert \
  >> /home/dunarea/dunarea.info/backup-monitor.log 2>&1
```

Alertarea folosește direct API-ul REST Cloudflare Email Sending; nu cere Worker.
Configurația 0600 conține ID-ul contului și un token Danube separat, limitat la
permisiunea `Email Sending: Edit`. Expeditorul este o adresă `@0x730.com` deja
onboarded în Cloudflare Email Service. `dunarea.info` nu este folosit ca domeniu
de trimitere și nu trebuie configurat pentru email. Un test explicit se face
fără a simula un incident:

```bash
python3 ops/offsite_backup.py monitor \
  --config /home/dunarea/dunarea.info/data/keys/offsite-backup.env \
  --max-age-hours 30 --test-alert
```

Configurația reutilizează bucketul privat comun și identitatea de recovery
acceptată pentru flotă; nu creați alt bucket, provider sau Worker.
Izolarea Danube este dată de configurația sa 0600, prefixul fix
`database/danube/` și cheia de criptare proprie, distinctă de credentialul
Spaces și de cheile celorlalte produse. Cloudflare reutilizează domeniul de
trimitere `0x730.com`, dar tokenul, configurația și mesajele sunt Danube-only.
Cheile vechi `DANUBE_BACKUP_TEM_*` sunt refuzate explicit. Dacă aceste fișiere
lipsesc, nu reinstalați joburile și nu pretindeți livrare; restaurați
configurația prin procedura operatorului fără a tipări valori. Modelul fără
secrete rămâne `ops/offsite-backup.env.example`.

### Starea recovery verificată la 28 august 2026

- jobul `2117004` și jobul `2117005` sunt `installed` în Forge ca `dunarea`;
- o copie SQLite WAL-aware a produs 949 rânduri, dintre care 638 permanente;
- obiectul client-criptat sub `database/danube/` a trecut HEAD și GET
  autentificate cu mărime/SHA identice, iar accesul anonim a fost refuzat;
- restore-drill-ul a măsurat `RPO=13s` și `RTO=1.088s`, nu a suprascris
  producția și a eliminat bytes decriptați, stagingul și artefactele temporare;
- Scaleway TEM a acceptat cererea de test la nivel API; primirea în inbox nu
  este probată și nu trebuie declarată;
- prima execuție a jobului `2117004` a reușit la `2026-08-28T03:15:01Z`: 963
  rânduri, 647 permanente, obiect privat în Spaces cu read-back autentificat și
  cleanup verificat;
- prima execuție a jobului `2117005` a reușit la `2026-08-28T08:17:01Z`: același
  obiect a fost citit autentificat și era `fresh` la 18.120 secunde, sub limita
  de 30 h;
- testul explicit TEM de la `2026-08-28T09:38:20Z` a fost acceptat de API pentru
  obiectul curent; nu există probă de primire în inbox și nu trebuie declarată.
- acceptările TEM de mai sus sunt probe istorice ale providerului înlocuit; nu
  validează Cloudflare Email Sending;
- read-back-ul Cloudflare a confirmat `0x730.com` activ pentru Email Sending și
  return-path-ul `cf-bounce.0x730.com`;
- tokenul separat a fost validat activ, iar testul controlat din checkout de la
  `2026-08-28T20:10:56Z` a primit pentru `daniel@0x730.com` starea `delivered`
  sau `queued` de la API; operatorul a confirmat mesajul în inbox la 23:10 EEST;
- configurația instalată pe server folosește grupul Cloudflare complet în
  fișierul 0600 și nu mai conține chei `DANUBE_BACKUP_TEM_*`. Configurarea,
  deploy-ul explicit și testul post-deploy rămân probe separate în fiecare
  release și nu trebuie deduse una din alta.

Restaurare de probă off-box, fără oprirea daemonului și fără destinație aleasă
de operator:

```bash
python3 ops/offsite_backup.py restore-drill \
  --config /home/dunarea/dunarea.info/data/keys/offsite-backup.env
```

Scriptul selectează cel mai nou obiect valid, îl citește autentificat, verifică
HMAC înainte de decriptare, creează numai o copie SQLite aleatoare 0600 într-un
director 0700, verifică integritatea, schema și arhiva permanentă, măsoară RPO
și RTO, apoi șterge bytes decriptați și toate artefactele. Succesul include
`productionOverwritten: false` și trei probe de cleanup.

Restaurare reală în caz de incident (separată de drill):

1. opriți Background Process-ul în Forge;
2. verificați copia cu `python3 ops/backup.py --verify-only BACKUP`;
3. înlocuiți atomic `/home/dunarea/dunarea.info/cache.db` și aplicați `0600`;
4. reporniți procesul și verificați `/api/health`, `/api/overview` și arhiva;
5. păstrați copia înlocuită până când revizuirea datelor este terminată.

## 7. Acceptanță post-deploy

```bash
bash ops/verify_deploy.sh \
  --origin-ip 157.90.144.210 \
  --domain dunarea.info \
  --backups /home/dunarea/dunarea.info/backups \
  --offsite-config /home/dunarea/dunarea.info/data/keys/offsite-backup.env
```

În plus față de postura tehnică, acceptanța cere o revizuire a datelor live:
timestamp-ul fiecărei surse, statutul de fetch, întârzierea normală a
furnizorului, consistența dintre sumar și seriile brute și diferența dintre
date stale servite din cache și o sursă oficială indisponibilă. Un endpoint 200
nu este dovadă că datele sunt actuale.

Pentru detaliile backupului și verificatorului, vedeți `ops/README.md`.
