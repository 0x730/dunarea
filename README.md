# Monitor Dunărea — date din surse oficiale

Aplicație web locală care adună într-un singur loc ce publică efectiv instituțiile
oficiale despre Dunăre, de la Kelheim (Germania) până la Sulina — și spune explicit,
pentru fiecare valoare, dacă e **măsurată**, **model/estimare**, **calculată** sau
**nepublicată** de nimeni.

## Pornire

```bash
python3 server.py           # → http://localhost:7300
```

Fără dependențe externe (doar Python 3 standard library). Opțional:

```bash
PORT=8080 python3 server.py             # alt port
ENTSOE_TOKEN=... python3 server.py      # activează producția pe unități la PF I/II
                                        # (token gratuit: transparency.entsoe.eu →
                                        #  cont → "Web API Security Token")
```

La prima pornire serverul „încălzește" cache-ul (găsește celulele de râu GloFAS
pentru fiecare punct — durează 1–2 minute); rulările următoare sunt instant.
Cache-ul stă în `cache.db` (SQLite) și poate fi șters oricând fără pierderi.

## Sursele de date

| Sursă | Ce dă | Frecvență | Tip |
|---|---|---|---|
| [PEGELONLINE](https://www.pegelonline.wsv.de) (WSV, Germania) | nivel + debit, stații DE și AT (VIA DONAU) | orar/15 min | măsurat (brut, neverificat) |
| [INHGA](https://www.hidro.ro) | buletinul „Diagnoza și prognoza pentru Dunăre": debit Baziaș, medie multianuală, prognoză | zilnic | măsurat, oficial |
| [AFDJ Galați](https://www.afdj.ro/ro/cotele-dunarii) | cotele Dunării pe tot sectorul românesc + brațe (flux XML) | zilnic | măsurat, oficial |
| [RHMZ Serbia](https://www.hidmet.gov.rs/eng/osmotreni/stanje_voda.php) | nivel/debit stații sârbești, până la Prahovo | zilnic | măsurat, oficial |
| GloFAS / [Open-Meteo flood API](https://open-meteo.com/en/docs/flood-api) (Copernicus) | debit zilnic în orice punct, arhivă din 1984 | zilnic | **model** |
| ERA5 / [Open-Meteo archive](https://open-meteo.com/en/docs/historical-weather-api) (Copernicus) | precipitații zilnice, arhivă | zilnic | reanaliză |
| [ENTSO-E Transparency](https://transparency.entsoe.eu) (opțional) | producție pe unități ≥100 MW (PF I) | orar, cu întârziere | măsurat |
| [DanubeSTREAM](https://www.danubeportal.com) (FAIRway) | mirele de navigație din toate țările riverane (~100 stații AT/SK/HU/RS/RO/BG) + cross-check automat cu AFDJ | cvasi-orar | măsurat |
| [Transelectrica SEN](https://www.transelectrica.ro/sen-grafic) | producția pe surse (hidro, nuclear/CNE), linia Djerdap, sold | timp real | măsurat |
| [hydroweb.next](https://hydroweb.next.theia-land.fr) (cheie în `data/keys/hydroweb.key`) | niveluri din altimetrie satelitară (Sentinel-3/6, SWOT) pe stații virtuale Dunăre, cu percentila proprie | la trecerea satelitului | măsurat din orbită |
| [DAHITI](https://dahiti.dgfi.tum.de) (opțional, `DAHITI_KEY=`) | rezervă la hydroweb.next, aceleași tipuri de date | la trecerea satelitului | măsurat din orbită |
| [GRDC](https://portal.grdc.bafg.de) (opțional, fișier în `data/grdc/`) | serii de debit măsurate din sec. XIX (ex. Ceatal Izmail) | istoric | măsurat |
| Gravimetrie GRACE/GRACE-FO (SAGSA via hydroweb.next; necesită `pip3 install h5py`) | anomalia apei totale din bazin (suprafață+sol+subteran), km³, din 2002 | lunar, decalaj ~1 an | măsurat din orbită |
| Zăpadă ERA5 (Open-Meteo, aceleași puncte-proxy) | ninsoarea iernii nov–mar vs. istoric, în tabelul de statistici | zilnic | reanaliză |

Aplicația își construiește automat **arhiva locală**: un snapshot pe zi pentru
AFDJ, RHMZ, DanubeSTREAM, SEN și buletinele INHGA (`/api/istoric` arată stadiul).
Cu fiecare zi de rulare, detectoarele capătă serie măsurată proprie.

Neintegrate, cu motivul la vedere: SHMU Slovacia (public doar niveluri, deja
acoperite prin DanubeSTREAM), hidrologia ucraineană (fără endpoint public — a
fost sondată), SWOT L4 discharge (fișier continental de 7,4 GB),
Hydroweb lacuri (nicio stație în zona Dunării), măștile de apă Sentinel-2 din
catalogul Theia (ultimele produse: 2024 — pentru imagini la zi folosiți Copernicus
Browser, linkul din aplicație).

Note importante, afișate și în aplicație:

- **Comparațiile multianuale** (debit + precipitații) folosesc GloFAS/ERA5 pentru că
  sunt serii consecvente în timp — bune la comparat ani între ei, chiar dacă valoarea
  absolută diferă de măsurătoarea INHGA din ziua respectivă.
- **Distribuția pe brațe în deltă nu se calculează din model**: GloFAS rutează
  aproape tot debitul pe o singură celulă la bifurcație, iar aplicația respinge
  automat rezultatul în loc să afișeze un număr fals. Debitele reale pe brațe provin
  doar din campanii ADCP (INHGA/AFDJ/Comisia Dunării); pentru Bâstroe nu există date
  ucrainene publice.
- **Telemetria barajelor** de la Porțile de Fier (turbine/deversoare/ecluze, pe ore),
  curbele cotă–volum ale lacurilor și contorizarea reală a captărilor **nu sunt
  fluxuri publice la niciun operator** — aplicația marchează aceste poziții cu
  „nepublicat" și indică unde pot fi cerute (Legea 544/2001).
- Site-ul RHMZ Serbia are lanțul de certificate TLS incomplet; conectorul citește
  pagina publică cu verificarea relaxată doar pentru acest host.

## Detectorul de anomalii

Secțiunea „Anomalii față de istoric" rulează patru verificări automate
(`anomalii.py`, endpoint `/api/anomalii`, recalculat la 6 h):

1. **Climatologie** — percentila zilei calendaristice (fereastră ±7 zile) față de
   GloFAS 1991–anul trecut, în 5 secțiuni; serii de zile consecutive sub P10.
2. **Bilanț Baziaș→Gruia** — decalajul de propagare se estimează din corelația
   variațiilor zilnice, apoi reziduul relativ al ultimelor 14 zile se compară cu
   distribuția istorică a aceleiași luni (z-score). Detectează „apă lipsă" persistentă.
3. **Măsurat vs. model** — seria oficială INHGA (reconstruită din arhiva publică a
   buletinelor, descărcată automat la pornire) împărțită la modelul Copernicus;
   bias-ul stabil e normal, ruptura bruscă e semnal.
4. **Coerența precipitații↔debit** — euristic: percentila cumulului de ploi pe 90 de
   zile în bazinul amonte vs. percentila debitului.

Fiecare verdict afișează metoda, cifrele și limitele — detectoarele produc probe
verificabile, nu acuzații.

## Structură

```
server.py        server HTTP (stdlib) + rutele /api/*
connectors.py    conectorii către surse + cache SQLite cu TTL
anomalii.py      cele patru detectoare de anomalii
static/          frontend (HTML/CSS/JS + ECharts vendorizat)
cache.db         cache local (generat la rulare)
```

## API local

`/api/overview` · `/api/afdj` · `/api/hidmet` · `/api/inhga` ·
`/api/pegel/stations` · `/api/pegel/series?uuid=&param=W|Q&days=` ·
`/api/glofas/recent?point=&days=` · `/api/glofas/years?point=&start=` ·
`/api/precip?point=&start=` · `/api/delta` · `/api/entsoe` · `/api/points` ·
`/api/anomalii` · `/api/inhga/serie?days=` · `/api/statistici` (+`.csv` pentru export)
