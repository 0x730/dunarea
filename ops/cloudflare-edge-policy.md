# Politica edge Cloudflare — Dunărea

Aceasta este specificația operațională pentru `dunarea.info`. Se aplică la edge;
nu cere modificarea aplicației, un deploy Forge sau un serviciu plătit.

## Inventar și limite de scop

- serverul expune 47 de rute sub `/api/`; toate sunt publice și acceptă numai
  `GET`/`HEAD`; `POST`, `PUT`, `PATCH`, `DELETE` și `OPTIONS` răspund `405`;
- nu există rute de autentificare sau scriere în codul lansat;
- paginile publice, `/vendor/*`, celelalte asset-uri statice și toate rutele din
  afara `/api/` sunt excluse din regula edge;
- zona este pe planul Cloudflare Free, deci folosește exact o regulă de rate
  limiting, fără funcții Advanced Rate Limiting sau produse plătite.

### `robots.txt` este generat la edge, nu din repo

Citit pe 9 septembrie 2026: `https://dunarea.info/robots.txt` răspunde `200`
cu un corp marcat `BEGIN Cloudflare Managed content`. Fișierul **nu există în
repo** — sub `static/` este versionat doar `.well-known/security.txt` — deci
aplicația ar întoarce `404` pe această cale; conținutul vine din funcția
Cloudflare Managed robots.txt a zonei.

Conținutul curent declară `Content-Signal: search=yes,ai-train=no,use=reference`
pentru `User-agent: *`, cu `Allow: /`, și `Disallow: /` pentru crawlerele de
antrenare declarate de Cloudflare (Amazonbot, Applebot-Extended, Bytespider,
CCBot, ClaudeBot, CloudflareBrowserRenderingCrawler, Google-Extended, GPTBot,
meta-externalagent).

Consecințele care contează pentru contractele deja declarate:

- indexarea rămâne permisă (`search=yes`, `Allow: /`), deci verificarea
  `robots` și `indexability: index` din manifestul flotei rămâne satisfăcută;
- semnalul `ai-train=no` este o rezervare de drepturi, nu un control tehnic;
- fiind generat la edge, conținutul se poate schimba când Cloudflare își
  actualizează lista gestionată, **fără niciun commit și fără deploy**. Nu
  există aici o probă care să rămână valabilă între citiri: la orice verificare
  a suprafeței publice, `robots.txt` se recitește, nu se presupune.

Aceasta este o decizie de zonă a operatorului, consemnată aici pentru că
inventarul de mai sus trebuie să acopere tot ce răspunde la edge. Dacă politica
trebuie schimbată — fie dezactivând conținutul gestionat și versionând un
`static/robots.txt` propriu, fie ajustând semnalele în Cloudflare — este o
acțiune pe zonă, separată de deploy-ul aplicației, și se reconsemnează aici cu
data read-back-ului.

## Read-back Cloudflare — 28 august 2026

Înainte de schimbare, read-back-ul sanitizat a arătat o zonă activă pe planul
Free, un singur TXT DMARC strict și niciun entrypoint `http_ratelimit`.
DMARC-ul existent este `p=reject; sp=reject; adkim=s; aspf=s; pct=100`.
Conform scopului Danube, acesta a fost numai verificat: nu se activează DMARC
Management, nu se schimbă politica și nu se creează adresă, inbox sau furnizor
de email. `daniel@0x730.com` rămâne contactul operatorului.

După schimbare, read-back-ul arată un singur ruleset `http_ratelimit`, cu o
singură regulă activă:

| Câmp | Valoare |
| --- | --- |
| potrivire | `starts_with(http.request.uri.path, "/api/")` |
| caracteristici | `cf.colo.id`, `ip.src` |
| prag | 60 cereri / 10 secunde / IP |
| acțiune | block |
| durată blocare | 10 secunde |

`starts_with` este intenționat: dry-run-ul a confirmat că operatorul regex
`matches` ar cere Advanced Rate Limiting. Predicatul ales are aceeași limită de
cale pentru această aplicație, fără plan sau regulă suplimentară.

## Verificare după activare

Înainte de testul de limită, drumurile publice obișnuite `/`, `/romania`,
`/api/health` și `/api/overview` au răspuns `200`. Testul controlat a trimis
exact 65 cereri concurente la `/api/health`: 62 au primit `200`, 3 au primit
`429`. După 11 secunde, `/api/health` a răspuns din nou `200`.

Nu repetați testul ca trafic de rutină. La orice schimbare a rutei, actualizați
întâi inventarul și verificați că regula unică rămâne limitată la `/api/`.
