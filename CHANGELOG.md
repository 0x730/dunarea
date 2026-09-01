# Istoric versiuni

Toate versiunile publice ale Monitorului Dunărea sunt documentate aici. Proiectul
folosește versiuni semantice, iar tag-ul Git corespunzător este proba exactă a
codului lansat.

## [v1.0.9](https://github.com/0x730/dunarea/tree/v1.0.9) — 2026-09-01

- Hydroinfo (OVF Ungaria) își reînnoise certificatul TLS pe 25.08.2026, dar
  serverul lor trimite alt intermediar decât emitentul real al certificatului;
  fetch-ul pica la verificare și tabelul Dunării rămânea blocat pe snapshotul
  din 25 august, marcat `stale`;
- `http_get` primește ancore CA suplimentare per host: intermediarul corect
  (din AIA) și rădăcina „e-Szigno RSA TLS Root CA 2025" cross-semnată de
  „Microsec e-Szigno Root CA 2009" din depozitul de sistem — lanțul se închide
  cu verificare TLS completă, fără `verify=off`;
- testele fixează contractul: verificarea rămâne `CERT_REQUIRED` cu
  `check_hostname`, ancorele sunt prezente, iar celelalte hosturi rămân pe
  depozitul implicit al sistemului.

## [v1.0.8](https://github.com/0x730/dunarea/tree/v1.0.8) — 2026-08-30

- `/api/raport` livrează imediat ultimul snapshot complet și mută
  reîmprospătarea potențial lentă în fundal, single-flight, astfel încât o
  expirare simultană a surselor nu mai ține cererea deschisă peste timeoutul
  proxy-ului;
- snapshoturile expirate sunt marcate explicit prin `livrare_snapshot`, iar un
  cache inițial gol răspunde rapid cu `503` și `Retry-After` până când warmup-ul
  finalizează prima copie;
- warmup-ul și watcherul de mentenanță pregătesc periodic snapshotul în afara
  firelor HTTP, iar testele acoperă livrarea imediată, refreshul concurent și
  contractul retryabil pentru cache rece.

## [v1.0.7](https://github.com/0x730/dunarea/tree/v1.0.7) — 2026-08-28

- alertele de recovery trimise prin Cloudflare includ acum un template HTML
  autonom, cu stiluri inline și stări vizuale distincte pentru fresh, stale,
  missing și failure;
- fiecare mesaj păstrează fallbackul plain-text, nu încarcă imagini, fonturi sau
  CSS extern și escapează valorile obiectului înainte de randarea HTML;
- testele fixează contractul multipart, diferențierea incidentelor, lipsa
  resurselor externe și limita de dimensiune a template-ului.

## [v1.0.6](https://github.com/0x730/dunarea/tree/v1.0.6) — 2026-08-28

- alerta operațională pentru backup migrează de la Scaleway TEM la API-ul REST
  Cloudflare Email Sending, cu token separat `Email Sending: Edit` și expeditor
  pe domeniul `0x730.com` deja onboarded; `dunarea.info` nu devine domeniu de
  email;
- răspunsul Cloudflare este acceptat numai dacă destinatarul apare în
  `delivered` sau `queued`, iar bounce-ul permanent și răspunsurile ambigue sunt
  eșecuri;
- monitorul nu mai trimite a doua alertă după ce alerta incidentului a fost deja
  acceptată înaintea ieșirii non-zero;
- `0x730.com` a fost citit ca activ pentru Email Sending, iar testul controlat
  cu tokenul separat a primit starea `delivered` sau `queued`; operatorul a
  confirmat mesajul în inbox. Configurarea, deploy-ul și testul post-deploy
  rămân porți distincte.

## [v1.0.5](https://github.com/0x730/dunarea/tree/v1.0.5) — 2026-08-28

Contractele publice corectate după doctorul flotei:

- pagina de start declară canonicalul exact `https://dunarea.info/`;
- `/sitemap.xml` răspunde cu sitemap XML valid pentru cele șapte vederi publice,
  în locul răspunsului JSON 404;
- testele verifică markup-ul, schema/URL-urile sitemapului și răspunsurile GET/
  HEAD cu MIME XML;
- documentația consemnează primele rulări reușite ale joburilor Forge `2117004`
  și `2117005`, prospețimea obiectului și acceptarea API a testului TEM, fără
  afirmație despre primirea în inbox.

## [v1.0.4](https://github.com/0x730/dunarea/tree/v1.0.4) — 2026-08-27

Reconciliere operațională după verificarea independentă a flotei:

- Quick Deploy rămâne dezactivat, iar runbook-ul numește precis fluxul în doi
  pași: push curat, urmat de invocarea explicită a deploy-ului prin Forge API;
- documentația reflectă joburile Forge instalate `2117004` și `2117005`, proba
  manuală criptată și faptul că primele execuții programate sunt încă în așteptare;
- proba TEM se oprește la acceptarea API și nu pretinde primire în inbox;
- `ops/verify_deploy.sh` folosește un singur workspace `mktemp -d`, eliminat prin
  trap la ieșire normală, eroare sau semnal, fără căi temporare predictibile.

## [v1.0.3](https://github.com/0x730/dunarea/tree/v1.0.3) — 2026-08-27

Remediere operațională pentru recovery și contractele publice:

- copia SQLite WAL-aware existentă este compusă cu criptare autentificată,
  upload privat Spaces, read-back de mărime/SHA, retenție Danube-only și cleanup;
- monitorul independent poate alerta prin Scaleway TEM, iar drill-ul restaurează
  numai într-o copie temporară și măsoară RPO/RTO;
- `/api/health.buildSha` este legat de checkout-ul Forge activ;
- `/.well-known/security.txt` publică contactul canonic cu MIME `text/plain`;
- Quick Deploy este dezactivat; după push, producția este pornită numai printr-o
  invocare explicită a deploy-ului Forge API în sesiunea proprietarului.

Testul parserului GRDC ocolește explicit cache-ul persistent de runtime, astfel
încât poarta Forge verifică fixture-ul izolat și rămâne deterministă pe server.

## [v1.0.2](https://github.com/0x730/dunarea/tree/v1.0.2) — 2026-08-27

Release candidate respins corect înainte de activare: testul GRDC folosea un
rezultat vechi din cache-ul persistent în locul fixture-ului temporar. Producția
nu a rulat această revizie; corecția și remedierea sunt publicate în `v1.0.3`.

## [v1.0.1](https://github.com/0x730/dunarea/tree/v1.0.1) — 2026-08-11

Patch de securitate pentru suprafața publică și transportul către surse:

- excepțiile upstream sunt publicate numai ca reason codes stabile, fără căi,
  URL-uri semnate sau fragmente din răspunsurile terțe;
- redirecturile cererilor HydroWeb autentificate rămân strict pe hostul oficial,
  HTTPS și portul 443;
- a fost eliminată complet facilitatea nefolosită care putea dezactiva
  verificarea certificatelor TLS;
- testele de regresie acoperă explicit cele trei bariere.

## [v1.0.0](https://github.com/0x730/dunarea/tree/v1.0.0) — 2026-08-11

Prima versiune publică de producție, disponibilă la
[dunarea.info](https://dunarea.info/).

- agregă și explică datele hidrologice, meteorologice, satelitare și energetice
  din sursele oficiale documentate în [METODE.md](METODE.md);
- publică proveniența, prospețimea, limitele și datele lipsă fără a transforma
  absența unei probe într-o concluzie;
- include pagina dedicată României și CNE Cernavodă, raportul JSON arhivabil și
  exporturile CSV;
- rulează prin Laravel Forge pe un server Hetzner existent, în spatele
  Cloudflare Full (strict), cu origine restricționată, rate limiting și backup
  SQLite verificat;
- include identitatea text, faviconul și corecțiile de prospețime pentru RHMZ,
  EDO, ERA5 și sursele satelitare validate la lansare.
