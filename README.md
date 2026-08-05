# Monitor Dunărea — date din surse oficiale

Aplicație web locală care adună într-un singur loc ce publică efectiv instituțiile
oficiale despre Dunăre, de la Ingolstadt (Germania) până la Sulina — și spune explicit,
pentru fiecare valoare, dacă e **măsurată**, **model/estimare**, **calculată** sau
**nepublicată în sursele revizuite**.

Vederea `/romania` aplică un test determinist de proporționalitate: separă
severitatea Dunării, impactul confirmat la CNE Cernavodă și criticitatea
Sistemului Energetic Național. Comparația cu anii anteriori folosește aceeași
celulă GloFAS și aceeași zi/fereastră calendaristică; nu o prezintă drept
istoric al bazinului de aspirație al centralei. Separat, o matrice operațională
leagă episoadele 2003, 2011, 2015 și 2022 de acțiunea publicată de SNN, iar
starea anului curent este acceptată numai din raportul proaspăt verificat.
Pentru fiecare episod, monitorul calculează separat debitul GloFAS la începutul
ferestrei și minimul din intervalul documentat. Adaugă și rezumate factuale ale
cotelor măsurate la mira Cernavodă din anuarele Comisiei Dunării și prezentarea
AFDJ, fără a redistribui tabelele zilnice. Ferestrele diferite sunt context
operațional, nu un clasament direct și nu măsurători la priza CNE.
Data generării raportului este separată de data fiecărei probe: după schimbarea
zilei, ultima valoare GloFAS își păstrează data reală până când sosește proba nouă
și nu este mutată artificial pe data curentă.
Pragurile în mdMB publicate de SNN în 2011 sunt marcate exclusiv drept reper
istoric; monitorul arată explicit că debitul Baziaș, mira AFDJ, GloFAS și
producția SEN nu reproduc nivelul și limitele curente ale bazinului de aspirație.
Pentru contextul românesc, monitorul preia din ultimul buletin lunar INHGA numai
sistemele care intră direct în sectorul românesc al Dunării: Nera, Cerna, Jiu,
Olt, Vedea și Argeș înainte de Cernavodă, respectiv Ialomița, Siret și Prut după
Cernavodă. Someș, Mureș, Crișuri și Timiș nu sunt afișate separat: aportul lor
ajunge prin Tisa înainte de Baziaș și este deja inclus în debitul de intrare în
țară. Benzile lunare sunt marcate drept prognoză, nu debit măsurat. La
Cernavodă sunt afișate dinamic și temperatura observată la mira AFDJ, plus
prognozele de cotă la 24–120 h, fără a le prezenta drept praguri ale CNE.
Separat, seriile publice DanubeHIS/NIHWM oferă statistici măsurate de la
1 ianuarie pentru cinci secțiuni de pe Jiu, Olt, Vedea, Siret și Prut: ultima
valoare, mediana anuală până la zi, mediana lunii, minimul, maximul și
acoperirea. Sunt debite instantanee brute în secțiuni parțiale, nu medii
zilnice și nu aporturi totale la Dunăre; aplicația nu le însumează și nu le
transformă în volum. Tabelul de precipitații arată distinct cumulul ERA5 de la
1 ianuarie, mediana aceleiași ferestre și abaterea fiecărui punct-proxy.
Pentru aceleași cinci secțiuni, GloFAS adaugă separat percentila modelată față
de propria referință multidecenală efectiv returnată de sursă (fereastră
calendaristică ±7 zile); intervalul real este afișat, nu presupus din cerere.
Percentila nu este atribuită măsurătorii DanubeHIS și diferența absolută dintre
model și observație nu este tratată automat drept anomalie.
Contextul național despre acumulări și restricții este preluat din ultimul
comunicat relevant ANAR. El expiră automat după 14 zile și nu completează din
comunicate vechi un coeficient sau un volum nepublicat. Pentru energie,
aplicația păstrează o fotografie SEN pe zi și activează comparația locală numai
după minimum 14 zile distincte; aceste fotografii nu sunt medii zilnice și nu
înlocuiesc rezervele sau prețurile DAMAS II.

Vederea `/date-lipsa` este registrul auto-actualizat al verigilor care ar putea
schimba concluzia: ce valoare este necesară, de ce ajută, ce există deja, ce
lipsește și ce surse oficiale au fost verificate. Un catalog de satelit sau un
link identificat rămâne separat de o observație efectiv ingerată.

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

Analiza narativă AI este opțională; sinteza și detectoarele deterministe nu
depind de ea. Modul implicit trimite digestul auditabil unui endpoint
OpenAI-compatibil, fără acces web:

```bash
AI_API_KEY=... python3 analiza_ai.py
# opțional: AI_MODEL=... AI_BASE_URL=...
```

Pentru a compara separat concluziile și cu surse instituționale actuale,
activați căutarea web. Acest mod folosește [Responses API cu web search](https://developers.openai.com/api/docs/guides/tools-web-search),
este disponibil numai cu API-ul OpenAI oficial și păstrează linkurile citate de
model; poate adăuga timp și cost apelului. Analiza AI nu este afișată în
interfață, nu rulează la refresh și nu are watcher de fundal. Se pornește numai
prin comanda explicită:

```bash
AI_API_KEY=... AI_WEB_SEARCH=1 python3 analiza_ai.py
# opțional: AI_WEB_MODEL=gpt-5.6-terra
```

Promptul verifică explicit erorile și prospețimea cache-ului, separă
măsurătorile de modele/reanalize/cataloguri și nu numără drept confirmări
independente surse care retransmit aceleași date. Rularea manuală afișează
promptul, digestul exact, modul, modelul și citările în terminal și arhivează
rezultatul local. `/api/analiza-ai` publică doar faptul că modul este manual;
nici `?run=1` nu poate declanșa un apel extern.

Verificare locală:

```bash
python3 -m unittest discover -s tests -v
```

La prima pornire serverul „încălzește" cache-ul (găsește celulele de râu GloFAS
pentru fiecare punct — durează 1–2 minute); rulările următoare sunt instant.
Cache-ul stă în `cache.db` (SQLite). Poate fi reconstruit din sursele externe,
dar ștergerea lui pierde snapshot-urile locale zilnice care nu pot fi refăcute integral.

## Sursele de date

| Sursă | Ce dă | Frecvență | Tip |
|---|---|---|---|
| [PEGELONLINE](https://www.pegelonline.wsv.de) (WSV, Germania) | nivel + debit, stații DE și AT (VIA DONAU) | orar/15 min | măsurat (brut, neverificat) |
| [INHGA](https://www.hidro.ro) | buletinul zilnic pentru Dunăre (Baziaș) și ultima prognoză lunară în benzi pentru afluenții selectați ai sectorului românesc | zilnic / lunar | măsurat la Baziaș; prognoză oficială pe afluenți |
| [AFDJ Galați](https://www.afdj.ro/ro/cotele-dunarii) | cotele și temperatura apei pe sectorul românesc + brațe, inclusiv prognoza de cotă la 24–120 h | zilnic | observații și prognoză oficială, etichetate separat |
| [RHMZ Serbia](https://www.hidmet.gov.rs/eng/osmotreni/stanje_voda.php) | nivel/debit stații sârbești, până la Prahovo | zilnic | măsurat, oficial |
| [OVF Hydroinfo](https://www.hydroinfo.hu/tables/eng/dunhif.html) (Ungaria) | nivel/debit pe Dunăre, inclusiv Budapesta și Mohács | zilnic | măsurat, oficial |
| [ICPDR DanubeHIS](https://www.danubehis.org/time-series/stations/Q?country=HU&river=Danube) | valori curente normalizate; fallback și control al căii de livrare OVF | aproape în timp real | măsurat, același furnizor ca Hydroinfo |
| [ICPDR DanubeHIS — România](https://www.danubehis.org/time-series/stations/Q?country=RO) | serii Q publice NIHWM pentru secțiuni pe Jiu, Olt, Vedea, Siret și Prut; statistici de la 1 ianuarie și pentru luna curentă | zilnic, cu întârzieri pe stație | măsurat brut, neverificat; acoperire parțială |
| [ANAR — resurse de apă](https://rowater.ro/activitatea-institutiei/departamente/managementul-situatiilor-de-urgenta/) | ultimul context național relevant despre acumulări, restricții și secțiuni sub minim | la comunicat | oficial; câmpurile nepublicate rămân lipsă, stare curentă max. 14 zile |
| GloFAS / [Open-Meteo flood API](https://open-meteo.com/en/docs/flood-api) (Copernicus) | debit zilnic în orice punct, arhivă din 1984 | zilnic | **model** |
| ERA5 / [Open-Meteo archive](https://open-meteo.com/en/docs/historical-weather-api) (Copernicus) | precipitații și ninsoare zilnică, cu modelul fixat explicit la ERA5 pentru consistență multidecenală | zilnic | reanaliză |
| [Copernicus EDO WMS](https://drought.emergency.copernicus.eu/data/wms-service) | hărți CDI și anomalie a umidității solului, decupate pe bazin | dekadal | model/observații compozite, doar context |
| [ENTSO-E Transparency](https://transparency.entsoe.eu) (opțional) | producție pe unități ≥100 MW (PF I) | orar, cu întârziere | măsurat |
| [DanubeSTREAM](https://www.danubeportal.com) (FAIRway) | mirele de navigație din toate țările riverane (~100 stații AT/SK/HU/RS/RO/BG) + cross-check automat cu AFDJ | cvasi-orar | măsurat |
| [Transelectrica SEN](https://www.transelectrica.ro/sen-grafic) | producția pe surse (hidro, nuclear/CNE), linia Djerdap, sold | timp real | măsurat |
| [Transelectrica — rapoarte zilnice / DAMAS II](https://newmarkets.transelectrica.ro/uu-webkit-maing02/00121011300000000000000000000100/publicReports) | destinațiile oficiale pentru consum, rezerve și rapoarte ale pieței; istoricul local SEN se acumulează separat | zilnic | identificat; comparația locală folosește fotografii, nu medii zilnice |
| [SNN — rapoarte curente](https://nuclearelectrica.ro/ir/rapoarte-curente/) | starea oficială a unităților CNE și cauza declarată de operator | la eveniment | oficial; rezumat auditat și datat |
| Comisia Dunării — [2003](https://www.danubecommission.org/uploads/doc/Library_scan/hydro_yearbooks/5.1.51_fr_ru_de.pdf), [2011](https://www.danubecommission.org/uploads/doc/2017/EG_Hydro_5_6_09_2017/yearbook_2011.pdf), [2015](https://www.danubecommission.org/uploads/doc/2021/yearbook_2015.pdf) + [AFDJ 2020–2025](https://www.danubecommission.org/uploads/doc/2026/20260305_EG_HYDRO/01_RO_AFDJ.pdf) | context măsurat la mira Cernavodă pentru episoadele 2003, 2011, 2015 și 2022 | istoric | rezumate factuale cu perioadă/pagină; fără redistribuirea tabelelor brute |
| [hydroweb.next](https://hydroweb.next.theia-land.fr) (cheie în `data/keys/hydroweb.key`) | niveluri din altimetrie satelitară (Sentinel-3/6, SWOT) pe stații virtuale Dunăre, cu percentila proprie | la trecerea satelitului | măsurat din orbită |
| [DAHITI](https://dahiti.dgfi.tum.de) (opțional, `DAHITI_KEY=`) | rezervă la hydroweb.next, aceleași tipuri de date | la trecerea satelitului | măsurat din orbită |
| [NASA OPERA DSWx-S1/HLS](https://podaac.jpl.nasa.gov/dataset/OPERA_L3_DSWX-S1_V1) via GIBS | întinderea apei din radar Sentinel-1 și optic Landsat/Sentinel-2, pe trei zone de control | la trecerea satelitului | observație clasificată, **shadow** |
| [Copernicus Land / CDSE STAC](https://stac.dataspace.copernicus.eu/v1/) | extinderea zăpezii SCE 500 m și umiditatea solului SSM 1 km, cu data produsului și quality notice | zilnic / după achiziție | observație satelitară, context |
| [NASA Earthdata CMR](https://cmr.earthdata.nasa.gov/search/) | prospețime și acoperire pentru SWOT RiverSP, SMAP, ICESat-2 ATL13 și NISAR SME2 | după misiune | metadate publice; valori opționale |
| [GRDC](https://portal.grdc.bafg.de) (opțional, export local în `data/grdc/`) | serii zilnice de debit măsurat; aplicația folosește explicit Ceatal Izmail, GRDC 6742900 | istoric, interval specific stației | măsurat; uz necomercial, fără redistribuirea datelor brute |
| Gravimetrie GRACE/GRACE-FO (SAGSA via hydroweb.next; necesită `pip3 install h5py`) | anomalia apei totale din bazin (suprafață+sol+subteran), km³, din 2002 | lunar, decalaj ~1 an | măsurat din orbită |
| Zăpadă ERA5 (Open-Meteo, aceleași puncte-proxy) | ninsoarea iernii nov–mar vs. istoric, în tabelul de statistici | zilnic | reanaliză |

### Import GRDC

Pentru comparația istorică de la intrarea în deltă se cere din portal numai
seria **daily discharge** pentru **Ceatal Izmail — GRDC 6742900**, în format
**GRDC Export / ASCII**. Instrucțiunile și condițiile de utilizare sunt în
[`data/grdc/README.md`](data/grdc/README.md). Dacă se descarcă mai multe stații,
importatorul selectează explicit 6742900 și nu amestecă o serie amonte cu
valoarea GloFAS de la Ceatal.

Snapshot-ul GRDC/WMO 2024 publicat pe Zenodo (1991–2024, CC BY-NC 4.0) a fost
verificat separat: ediția curentă nu conține o stație pe cursul principal al
Dunării, deci nu este folosită drept substitut pentru Ceatal. Produsele GRDC
clasice rămân o integrare locală opțională: uz necomercial, fără redistribuirea
fișierelor brute.

Aplicația își construiește automat **arhiva locală**: un snapshot pe zi pentru
AFDJ, RHMZ, Hydroinfo, DanubeHIS, DanubeSTREAM, SEN, ANAR, HydroWeb, OPERA și buletinele INHGA
(`/api/istoric` arată stadiul).
Cu fiecare zi de rulare, detectoarele capătă serie măsurată proprie.

Pentru rapoartele SNN, titlul și URL-ul sunt detectate automat, dar starea și
cauza sunt publicate numai pentru un PDF revizuit, cu amprenta SHA-256
neschimbată. Apariția unui raport
operațional Cernavodă necunoscut invalidează rezumatul anterior și afișează
„de revizuit”; la fel și modificarea PDF-ului sub același URL. Nu păstrează o
stare veche ca fapt curent. După trei zile fără
o actualizare comparabilă, starea este marcată prudent ca posibil veche.

Neintegrate, cu motivul la vedere: SHMU Slovacia (public doar niveluri, deja
acoperite prin DanubeSTREAM), hidrologia ucraineană (fără endpoint public — a
fost sondată) și Hydroweb lacuri (nicio stație în zona Dunării). Fișierele
științifice SWOT/SMAP/ICESat-2/NISAR sunt gratuite cu Earthdata Login; catalogul
și prospețimea lor sunt active fără cont, iar valorile rămân `catalog_only` până
există ingestie și validare per produs.

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

Secțiunea „Anomalii față de istoric" rulează verificări automate
(`anomalii.py`, endpoint `/api/anomalii`, recalculat la 6 h):

1. **Climatologie** — percentila zilei calendaristice (fereastră ±7 zile) față de
   GloFAS 1991–anul trecut, în 5 secțiuni; serii de zile consecutive sub P10.
2. **Bilanț Baziaș→Gruia** — decalajul de propagare se estimează din corelația
   variațiilor zilnice, apoi reziduul relativ al ultimelor 14 zile se compară cu
   distribuția istorică a aceleiași luni (z-score). Semnalează o divergență
   modelată persistentă; nu îi stabilește cauza.
3. **Măsurat vs. model** — seria oficială INHGA (reconstruită din arhiva publică a
   buletinelor, descărcată automat la pornire) împărțită la modelul Copernicus;
   bias-ul stabil e normal, ruptura bruscă e semnal.
4. **Coerența precipitații↔debit** — euristic: percentila cumulului de ploi pe 90 de
   zile în bazinul amonte vs. percentila debitului.
5. **Contra-probe** — AFDJ↔DanubeSTREAM, altimetrie satelitară și perechi
   măsurat↔model în Germania, Ungaria și Serbia.
6. **Austria** — screening al trendurilor de nivel; fără curbe cotă–volum și
   debite intrare/ieșire nu demonstrează și nu exclude retenția.

Fiecare verdict afișează metoda, cifrele și limitele — detectoarele produc probe
verificabile, nu acuzații.

## Structură

```
server.py        server HTTP (stdlib) + rutele /api/*
connectors.py    conectorii către surse + cache SQLite cu TTL
anomalii.py      detectoarele și screeningurile de anomalii
romania.py       testul determinist România/Cernavodă și comparația istorică
static/          frontend (HTML/CSS/JS + ECharts vendorizat)
cache.db         cache local (generat la rulare)
```

## API local

`/api/overview` · `/api/afdj` · `/api/hidmet` · `/api/inhga` ·
`/api/inhga/afluenti-dunare` ·
`/api/danubehis/afluenti-romania` ·
`/api/glofas/afluenti-romania` ·
`/api/hydroinfo` · `/api/danubehis` · `/api/edo` · `/api/edo/map?layer=cdi|soil` ·
`/api/opera` · `/api/opera/map?layer=sentinel1|hls&zone=` ·
`/api/copernicus-land` · `/api/copernicus-land/map?layer=snow|soil` ·
`/api/satellite-catalog` · `/api/evidence-sources` ·
`/api/pegel/stations` · `/api/pegel/series?uuid=&param=W|Q&days=` ·
`/api/glofas/recent?point=&days=` · `/api/glofas/years?point=&start=` ·
`/api/precip?point=&start=` · `/api/delta` · `/api/entsoe` · `/api/points` ·
`/api/anomalii` · `/api/romania` · `/api/inhga/serie?days=` ·
`/api/anar/resurse-apa` · `/api/sen/istoric` · `/api/date-lipsa` ·
`/api/statistici` (+`.csv` pentru export)
