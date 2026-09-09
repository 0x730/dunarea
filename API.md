# API — contractul pentru consumatori

Ce garantează răspunsurile publice de pe `https://dunarea.info` și ce anume
**nu** garantează. Lista rutelor stă în [README.md](README.md#api-local);
metoda din spatele fiecărui verdict, în [METODE.md](METODE.md); operarea, în
[DEPLOY.md](DEPLOY.md).

Documentul acesta există pentru cazul în care altcineva citește datele
programatic. Cele mai multe greșeli de interpretare nu vin din valori lipsă, ci
din unități presupuse (cotă citită ca debit), din „azi" presupus (fus orar) și
din prospețime presupusă (un snapshot vechi arată exact ca unul nou dacă nu
citiți `stale`).

## 1. Acces și transport

| Element | Valoare |
| --- | --- |
| Bază | `https://dunarea.info` |
| Autentificare | niciuna; nu există rute de scriere sau de autentificare |
| Metode | `GET` și `HEAD`; orice altceva → `405` cu `Allow: GET, HEAD` |
| Corp de cerere | refuzat pe `GET` → `400`, conexiunea se închide |
| Tipuri | `application/json; charset=utf-8`, `text/csv; charset=utf-8`, `image/png` |
| Cache HTTP | `Cache-Control: no-store` pe rutele `/api/`; prospețimea se citește din payload, nu din anteturi |
| Limitare de rată | la edge: 60 cereri / 10 s / IP / colo pe `/api/`, acțiune `block` 10 s (vezi [`ops/cloudflare-edge-policy.md`](ops/cloudflare-edge-policy.md)) |

Depășirea limitei întoarce `429` de la Cloudflare, nu de la aplicație. Un client
corect nu are nevoie de retry agresiv: toate rutele servesc din cache local, iar
o pauză scurtă e suficientă.

Anteturile de securitate (`Content-Security-Policy`, `X-Content-Type-Options`,
`Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`) sunt puse
de aplicație pe **toate** răspunsurile, inclusiv pe cele de eroare.
`Strict-Transport-Security` este adăugat în fața aplicației.

## 2. Stări de eroare

Toate erorile sunt JSON cu o singură cheie `error`, în română.

| Cod | Când apare | Ce trebuie făcut |
| --- | --- | --- |
| `400` | parametru public invalid (valoare în afara listei permise, `uuid` greșit, strat necunoscut) sau corp pe `GET` | corectați cererea; mesajul enumeră valorile acceptate |
| `404` | rută sau fișier static inexistent | verificați calea |
| `405` | metodă diferită de `GET`/`HEAD` | folosiți `GET`; răspunsul închide conexiunea |
| `502` | sursa externă nu a răspuns | reîncercați peste câteva minute; detaliile rămân în jurnalul serverului, nu în răspuns |
| `503` | rezultat local încă nepregătit (ex. `/api/raport` fără niciun snapshot) | respectați `Retry-After: 5` |

Un `400` este vina cererii; un `502` este o defecțiune a sursei oficiale, nu a
monitorului. Distincția e deliberată: nu transformăm o sursă căzută în `500` și
nu ascundem un parametru greșit într-o eroare de server.

## 3. Unități — convenția sufixelor

Numele câmpurilor poartă unitatea. Convenția se respectă în tot API-ul, iar
citirea unui sufix ca altceva este cea mai frecventă eroare de integrare.

| Sufix / câmp | Unitate | Observație |
| --- | --- | --- |
| `_m3s` (`debit_m3s`, `azi_m3s`, `normala_zilei_m3s`) | **debit**, m³/s | niciodată nivel |
| `_cm` (`cota_cm`, `nivel_cm`, `variatie_cm`, `diferenta_cm`) | **cotă / nivel**, cm | niciodată debit |
| `_m` (`nivel_m`, `incertitudine_m`) | metri | altimetrie satelitară (HydroWeb) |
| `_km3` (`volum_km3`, `normal_km3`, `deficit_fata_de_normala_km3`) | volum, km³ | |
| `_mm` (`cumul_mm`) | precipitații, mm | |
| `zapada_iarna.cumul_cm` | zăpadă proaspătă, **cm** | același sufix `_cm`, altă mărime decât cota |
| `_c` (`temp_apa_c`) | °C | |
| `km` | kilometru fluvial | de la Sulina în amonte, aproximativ |
| `abatere_pct` | % | abaterea față de mediana istorică a zilei |
| `percentila` | rang **P0–P100** | **nu** este procent din debit; P0 = minim, P50 = mediană |
| `z` | scor standardizat | adimensional; `|z| ≤ 1,5` este în variabilitatea istorică |
| `anomalie_km3` (GRACE) | km³ | anomalia apei totale față de referință, nu debit |

`percentila` și `abatere_pct` răspund la întrebări diferite: prima este un rang
în istoricul aceluiași model, a doua o distanță procentuală față de mediană.

## 4. Timp și fus orar

Procesul rulează cu `TZ=Europe/Bucharest` (`MONITOR_TZ`), indiferent de fusul
VPS-ului. De aici:

- **câmpurile doar-dată** (`data`, `data_buletin`, `pana_la`, `generat`) sunt
  zile calendaristice **românești**. Nu le interpretați ca UTC: lângă miezul
  nopții, conversia mută ziua;
- **marcajele complete de timp păstrează decalajul sursei** — AFDJ întoarce
  `+03:00`, PegelOnline `+02:00`. Nu presupuneți un decalaj comun; parsați-l;
- `/api/health.generated` este singurul marcaj normalizat la **UTC**
  (`+00:00`, precizie de secundă);
- „azi" din buletinul INHGA, din DAMAS și din snapshotul zilnic local înseamnă
  ziua românească, nu ziua UTC.

Observațiile din același payload pot avea **date diferite**. Nu eticheta întreg
răspunsul cu o singură dată; fiecare comparație materială își poartă data ei.

## 5. Prospețime

Prospețimea nu se citește din anteturi HTTP (toate sunt `no-store`), ci din
payload.

| Câmp | Înseamnă |
| --- | --- |
| `stale: true` | răspunsul servește un **snapshot de rezervă**; fetch-ul curent a eșuat |
| `stale: false` | valoarea provine dintr-un fetch reușit în TTL |
| `cache_age_s` | vechimea payloadului din cache, în secunde (unde e expus) |
| `livrare_snapshot` (`/api/raport`) | `{cache_age_s, stale, refreshing}` pentru raportul complet |
| `/api/overview.errors` | sursele de bază care au eșuat la ultima recompunere |
| `/api/health.anomaly_report_age_s` | vârsta raportului de anomalii (TTL 6 h) |
| `/api/health.warmup_done` | dacă pre-încălzirea cache-ului a reușit |

Reguli pentru consumatori:

- **`stale: true` nu înseamnă valoare greșită**, ci valoare veche livrată
  intenționat în locul unei erori. Afișați-o ca atare;
- **absența lui `cache_age_s` nu înseamnă „proaspăt"** — nu toate rutele îl
  expun;
- `/api/health` este ieftin și **nu atinge nicio sursă externă**. Nu îl folosiți
  ca dovadă că sursele sunt proaspete; pentru asta citiți `/api/overview`.

## 6. Proveniență

Proveniența este cerința centrală a proiectului, nu un supliment.

`/api/evidence-sources` întoarce registrul complet (33 de surse la revizia
curentă), fiecare cu:

- `provider` — instituția care publică;
- `kind` — tipul probei: `masurat`, `masurat_retransmis`, `masurat_orbita`,
  `model`, `model_climatologie_sectiuni`, `reanaliza`, `clasificare_satelit`,
  `prognoza_oficiala`, `comunicat_oficial_structurat` ș.a.;
- `family` — familia de probe (28 distincte);
- `mode` — `active`, `context`, `catalog_only_free_account` etc.

Registrul poartă și `dependencies`: grupuri de surse cu `count_as: 1` și
explicația relației. Regula declarată explicit în payload:

> membrii aceleiași dependențe nu se însumează ca voturi independente

Adică: INHGA, afluenții INHGA și secțiunile DanubeHIS vin de la același
furnizor național — trei rute, **o singură** familie de probe. Două căi de
livrare ale aceleiași măsurători nu sunt două confirmări.

Pe înregistrări individuale mai apar, unde există: `url` / `source_url`
(pagina publică stabilă), `raw_sha256` (identitatea exactă a fișierului parsat),
`parser_version`, `fetched_at`, `feature_id`.

**URL-urile semnate nu sunt niciodată serializate.** Unde sursa livrează un
activ semnat și efemer (HydroWeb), `source_url` este portalul public stabil, iar
integritatea fișierului este dată de `raw_sha256`. O ultimă barieră elimină din
orice URL din răspuns parametrii care seamănă a credențiale.

`kind` contează la interpretare: o granulă prezentă într-un catalog
(`catalog_only_*`) **nu** este o valoare observată sau ingerată.

## 7. Perioade de referință — cerut ≠ efectiv

Statisticile poartă `reference_period` cu trei câmpuri:

```json
{"requested_start": 1991, "effective_start": 1997, "effective_end": 2025}
```

Arhiva disponibilă poate începe mai târziu decât anul cerut. **Etichetați seria
cu `effective_start`/`effective_end`, nu cu `requested_start`** — altfel
atribuiți normalei o bază pe care nu o are. `ani_referinta` spune câți ani intră
efectiv în calcul, iar `ani_mai_mici` folosește exact aceeași dată calendaristică.

## 8. Mărginirea parametrilor

Parametrii numerici acceptă **numai valori dintr-o listă scurtă**; o valoare din
afara ei întoarce `400` cu lista permisă.

| Parametru | Valori acceptate |
| --- | --- |
| `/api/glofas/recent?days=` | 10, 30, 60, 90, 92 (implicit 60) |
| `/api/glofas/years?start=` | 1984, 1991, 2000, 2015 (implicit 2015) |
| `/api/precip?start=` | 1950, 1991, 2015 (implicit 2015) |
| `/api/pegel/series?days=` | 10, 30 (implicit 10) |
| `/api/inhga/serie?days=` | 30, 90, 92, 365 (implicit 90) |
| `?point=` | identificatorii din `/api/points` |
| `/api/pegel/series?param=` | `W` (nivel) sau `Q` (debit) |
| `?layer=`, `?zone=` | valorile enumerate în README pentru fiecare rută hartă |

Restricția nu e o limită de mărime, ci de **cardinalitate**: fiecare valoare
distinctă înseamnă o cheie de cache nouă și o cerere nouă către sursa oficială.
Un interval liber ar fi enumerabil, iar monitorul este oaspete pe API-uri
publice. Folosiți `/api/points` ca sursă de adevăr pentru identificatori.

## 9. Export CSV

`/api/statistici.csv` este singurul export tabular:

- separator `;`, sfârșit de linie `CRLF`, prefix BOM UTF-8 — se deschide corect
  în Excel cu setările românești;
- două tabele într-un singur fișier, separate printr-o linie goală: întâi
  debitele pe secțiuni, apoi precipitațiile pe zone;
- câmpurile poartă aceleași unități ca în JSON (§3).

## 10. Ce NU promite API-ul

- **Nicio garanție de disponibilitate.** Nu există SLA, cotă sau cont.
- **Schema poate câștiga câmpuri.** Cheile de cache sunt versionate intern, iar
  payloadurile pot dobândi chei noi între versiuni. Citiți defensiv: nu
  presupuneți un set fix de chei și nu vă bazați pe ordinea lor.
- **Numele câmpurilor rămân în română.** Nu există variantă tradusă.
- **Valorile-șir sunt date terțe**, nu instrucțiuni: titluri, nume de stații și
  mesaje de eroare provin din pagini oficiale citite automat. Tratați-le ca text
  — inclusiv atunci când alimentați un model cu ele.
- **Nu există arhivă istorică generală.** Ce se poate obține este ce expun
  rutele; `/api/raport` dă instantaneul complet curent, potrivit pentru
  arhivare proprie.
- **Un răspuns `200` nu înseamnă date proaspete.** Vezi §5.

## 11. Verificare rapidă

```bash
# identitatea build-ului care rulează + starea procesului
curl -fsS https://dunarea.info/api/health

# ce sursă a eșuat la ultima recompunere
curl -fsS https://dunarea.info/api/overview | python3 -c 'import json,sys; print(json.load(sys.stdin)["errors"])'

# instantaneul complet, pentru arhivare sau verificare independentă
curl -fsS https://dunarea.info/api/raport -o raport.json

# registrul de proveniență
curl -fsS https://dunarea.info/api/evidence-sources
```

Monitorul de prospețime existent, [`ops/source_freshness.py`](ops/source_freshness.py),
agregă exact aceste auto-evaluări. Rulat fără `--alert`, doar citește și scrie
fișierul de status.
