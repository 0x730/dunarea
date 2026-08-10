# Istoric versiuni

Toate versiunile publice ale Monitorului Dunărea sunt documentate aici. Proiectul
folosește versiuni semantice, iar tag-ul Git corespunzător este proba exactă a
codului lansat.

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
