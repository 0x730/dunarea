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
- **Environment** (aici stau secretele, NU în git):
  ```
  PORT=7300
  HYDROWEB_KEY=cheia_ta_hydroweb
  ENTSOE_TOKEN=tokenul_tau_daca_il_ai
  DAHITI_KEY=daca_apare_vreodata
  ```

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

Apoi restart nginx din Forge. Aplicația își servește singură staticele —
nginx-ul doar proxy-ază tot.

## 4. Cloudflare

- **DNS**: înregistrare `A` pentru `dunarea` → IP-ul serverului Forge,
  **Proxied** (nor portocaliu).
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
curl -s https://dunarea.exemplu.ro/api/istoric        # → JSON cu arhiva
curl -s -o /dev/null -w "%{http_code}\n" https://dunarea.exemplu.ro/   # → 200
```

Pastilele din header-ul paginii arată live starea fiecărei surse; dacă vreuna
e roșie permanent după ~5 minute de la pornire, `supervisorctl tail -f
daemon-XXXXXX` pe server arată de ce.

## Note de exploatare

- `cache.db` se creează singur pe server (nu vine din git); poate fi șters
  oricând — se reconstruiește (prima reconstrucție GRACE durează ~10 min).
- Arhiva locală zilnică (AFDJ/RHMZ/DanubeSTREAM/SEN/INHGA) trăiește tot în
  `cache.db` — pentru backup, e de ajuns o copie periodică a fișierului
  (Forge → Backups nu îl prinde; un cron simplu cu `cp` + rotație e suficient).
- Aplicația e read-only și fără autentificare — nu expune nimic sensibil;
  cheile stau doar în env-ul daemonului.
