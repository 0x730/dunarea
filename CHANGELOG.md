# Istoric versiuni

Toate versiunile publice ale Monitorului Dunărea sunt documentate aici. Proiectul
folosește versiuni semantice, iar tag-ul Git corespunzător este proba exactă a
codului lansat.

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
