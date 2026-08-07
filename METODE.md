# METODE — dosar de verificare pentru hidrologi

Document de control pentru **Monitor Dunărea**. Scopul lui e ca un hidrolog de la
INHGA, AFDJ, ANAR sau ICPDR să poată verifica în ~15 minute *ce* se calculează,
*pe ce date*, *față de ce referință* și *ce nu se afirmă* — fără să deschidă codul.

Cifrele-exemplu sunt verificate la **7 august 2026**; ele se schimbă zilnic,
metoda nu.

---

## 0. Convenții comune (valabile pentru toate detectoarele)

| Aspect | Convenția aplicată |
|---|---|
| **Debit** | m³/s, valori zilnice medii. Toate debitele „de model" vin din **GloFAS v4** (Copernicus EMS, model LISFLOOD forțat cu ERA5), grilă 0,05° (~5 km), livrate prin Open-Meteo Flood API. |
| **Cotă** | cm **față de zero-ul local al mirei**. Nu se convertește niciodată în mdMB, mdMN sau mdMB-Sulina și nu se convertește niciodată în debit. Nu există curbă cotă-debit în aplicație. |
| **Precipitații** | mm/zi, **ERA5** (reanaliză, 0,25° ≈ 25 km), `precipitation_sum` = total (ploaie + echivalentul în apă al ninsorii). Întârziere de publicare ~3 zile. |
| **Zăpadă** | cm **zăpadă proaspătă** (`snowfall_sum` Open-Meteo), NU echivalent în apă (SWE). Nu se convertește în mm apă și nu intră în niciun bilanț. |
| **Percentilă** | percentila empirică = `100 × (nr. valori de referință ≤ valoarea testată) / n`. Se refuză sub **30 de mostre** de referință. |
| **29 februarie** | exclus din toate ferestrele calendaristice, ca ferestrele să fie identice între ani. |
| **Anul curent** | exclus din propria referință în toate detectoarele climatologice. |
| **Perioada efectivă** | fiecare răspuns JSON publică `reference_period = {requested_start, effective_start, effective_end}`. **Se citește `effective_start`, nu `requested_start`.** |

### Avertisment care se aplică peste tot

> **GloFAS nu este o măsurătoare.** Este un model hidrologic global cu reprezentare
> simplificată a lacurilor de acumulare (parametri de operare stabiliți „prin
> judecată de expert", în lipsa înregistrărilor globale de manevre) și **fără**
> captări, transferuri sau folosințe. Pe secțiunile românești, aval de Porțile de
> Fier I și II, debitul real e regularizat; modelul nu vede manevrele barajului.
> Comparația validă este **serie GloFAS de azi vs. istoricul aceleiași serii
> GloFAS**, nu GloFAS vs. debitul măsurat.

Media multianuală GloFAS 1997–2025 se încadrează totuși în ±1…12 % față de
mediile multianuale publicate pe toate cele 14 secțiuni (Passau 1 446 vs ~1 420 m³/s;
Baziaș 5 526 vs ~5 300; Ceatal Izmail 6 611 vs ~6 500). **Media este calibrată
rezonabil; asta nu spune nimic despre biasul la ape mici.**

---

## 1. Regim hidrologic — `climatology()` / „percentila zilei"

**Date.** GloFAS v4, serie zilnică, 14 secțiuni de la Regensburg (km 2379) la
Ceatal Izmail (km 80).

**Fereastră.** Valoarea zilei curente.

**Referință.** Toate valorile din **fereastra calendaristică ±7 zile** (15 zile
calendaristice), din toți anii disponibili **în afară de anul curent**.
La 29 de ani × 15 zile ≈ **435 de mostre**.

**Perioada efectivă.** Cerută: 1991. **Livrată de furnizor: 1997-01-01.**
Anii 1991–1996 se întorc integral goi (`null`) din API-ul Flood; sunt eliminați
tăcut din calcul. **Referința reală este 1997–2025, adică 29 de ani** — cu un an
sub minimul de 30 de ani cerut de practica standard (normale climatologice OMM,
praguri USGS WaterWatch). JSON-ul raportează corect `effective_start: 1997`.

**Ce afirmă.**
- `percentila` — poziția debitului de azi în distribuția aceleiași ferestre
  calendaristice a ultimilor 29 de ani, *în același model*.
- `normala_zilei_m3s` — **mediana** ferestrei ±7 zile (nu media).
- `abatere_pct` — abaterea procentuală față de acea mediană.
- `ani_mai_mici / ani_referinta` — câți ani au avut, **exact în aceeași zi
  calendaristică**, o valoare mai mică. Numitor ≈ 29, nu 435.
- `zile_sub_p10` — numărul de **zile calendaristic consecutive** (nu „valori
  disponibile consecutive") cu percentila < 10.

**Clasificarea severității** (identică în spirit cu clasele USGS WaterWatch):

| percentila | clasă |
|---|---|
| < 2 sau > 98 | `extrem` |
| < 10 sau > 90 | `sever` |
| < 25 sau > 75 | `atentie` |
| 25 – 75 | `normal` |

**Ce NU afirmă.**
- Nu e debit măsurat și nu înlocuiește buletinul INHGA sau cotele AFDJ.
- P10 aici **nu** e Q90 din curba de durată clasică (aceea e anuală, neseparată
  pe sezon); e un prag variabil pe zi calendaristică.
- Nu se afirmă timp de revenire. Nu există MAM7, 7Q10, curbă de durată sau
  volum de deficit.

**Limite pe care trebuie să le știe cititorul.**
1. **`zile_sub_p10` este plafonat la 45.** Detectorul se uită doar în ultimele 45
   de zile calendaristice. La 7 aug. 2026 seria reală la Passau era de **53 de
   zile**, publicată ca 45; la Baziaș 46, publicată ca 45. Cifra e un
   **minim garantat**, nu durata episodului.
2. **`percentila = 0,0` este publicată ca număr.** Corect ar fi „sub minimul
   perioadei de referință" (cu n=435, afirmația exactă e P < 0,23). Se citește
   împreună cu `ani_mai_mici`.
3. Fereastra ±7 zile e mai îngustă decât ±15 zile, uzuală în literatura pragului
   variabil. Diferența e mică dar reală: pe aceeași zi, ±7 dă P=15,2 iar ±30 dă
   P=11,8 la Baziaș.
4. **Nu se separă perioada cu fenomene de iarnă.** Comisia Dunării exclude
   explicit perioadele cu gheață la definirea ENN/LNWL; aici o percentilă de
   ianuarie și una de august sunt tratate identic.
5. Referința e o fereastră glisantă (1997→anul trecut), nu o normală fixă. „P5
   azi" nu e strict comparabil cu „P5 în 2020". Tendința din referință e negativă
   dar **nesemnificativă statistic** la 29 de ani (Mann-Kendall: Passau Z=−0,24;
   Baziaș Z=−0,92; Ceatal Izmail Z=−1,63; pragul 5 % este |Z|>1,96).

---

## 2. Bilanț Baziaș → Gruia — `balance()`

**Date.** GloFAS v4 în **două** secțiuni: Baziaș (km 1071, intrarea în România) și
Gruia (km 851, aval de Porțile de Fier II). **Ambele din același model.**

**Decalajul de propagare.** Nu e luat din literatură sau de la operator. E
**estimat empiric**: se caută pe 0…4 zile decalajul care maximizează corelația
**variațiilor zilnice** (nu a nivelurilor) pe ultimele 1 500 de zile. Diferențierea
e o formă simplă de *pre-whitening* — fără ea, sezonalitatea comună ar da corelații
>0,92 la orice decalaj și maximul ar fi nedeterminat. Dacă cea mai bună corelație
e sub 0,30, se folosește decalajul documentat de 1 zi.

*Verificat la 7 aug. 2026:* maximul e la **lag = 2 zile, r = 0,783** (r la lag 1 =
0,660; la lag 3 = 0,737). Pe niveluri brute maximul ar fi la lag 3, r = 0,979 —
motivul pentru care se lucrează pe variații.

**Reziduul.** `rel = (Q_Gruia(t) − Q_Baziaș(t − lag)) / Q_Baziaș(t − lag)`,
calculat numai când Q_Baziaș > 200 m³/s (protecție la împărțire).

*Valori observate 2015–2026:* medie **+3,74 %**, abatere standard 10,37 %.
Semnul pozitiv e cel așteptat fizic — între km 1071 și km 851 intră Nera, Cerna,
Timokul și afluenții laterali (~200 m³/s din ~18 000 km², adică ~11 l/s/km²).
**Coerența internă a modelului este plauzibilă hidrologic.**

**Fereastra testată.** Media ultimelor **14 zile calendaristic consecutive**.

**Referința.** Distribuția mediilor **tuturor ferestrelor de 14 zile care se
încheie în aceeași lună calendaristică**, din anii 2015 → anul trecut, excluzând
anul curent. Se cere minimum 10 astfel de ferestre.

**Ce afirmă.** Un `z` — de câte abateri standard diferă fereastra curentă de
distribuția istorică a acelorași ferestre în aceeași lună.

**Ce NU afirmă — declarat explicit în payload.**
> „AMBELE serii vin din același model GloFAS: testul detectează o schimbare a
> consistenței interne a modelului, NU poate detecta o captare sau o deviere
> reală de apă la Porțile de Fier."

**Limite pe care trebuie să le știe cititorul.**
1. **`n_etalon` (~341 în august) supraestimează masiv informația.** Ferestrele de
   14 zile care se încheie în august se suprapun pe 13 zile din 14; reziduul zilnic
   are autocorelație lag-1 de **0,89**. Numărul de ani independenți e **11**.
   Deviația standard estimată e nedeplasată, dar incertitudinea ei e cea de la
   n≈11 (eroare standard ~21 % pe σ), nu cea de la n=341. **Un z de 2,0 are o
   incertitudine proprie de ordinul ±0,5.**
2. Decalajul e un întreg de zile. Timpul real de propagare prin lacurile Porțile
   de Fier depinde puternic de debit și de cota lacului și e subzilnic la ape mari;
   un decalaj fix de 2 zile aplicat pe 11 ani e o simplificare.
3. Decalajul găsit e **decalajul de rutare al LISFLOOD**, nu al Dunării.
4. 11 ani de referință e o bază scurtă. Distribuția reziduului e asimetrică
   (min −39,7 %, max +55,3 % pe valori zilnice); z-ul nu se traduce în
   probabilitate. Aplicația **nu** publică valoare p — corect.

---

## 3. INHGA măsurat ↔ GloFAS model — `measured_vs_model()`

**Date.** Debitul oficial publicat în buletinele INHGA (ultimele 90 de zile) și
GloFAS în aceeași secțiune (Baziaș).

**Metrica.** Raportul zilnic `oficial / model`.

**Fereastra testată.** Media raportului pe **ultimele 7 zile** — **exclusă** din
etalon.

**Referința.** Distribuția mediilor pe 7 observații din perioada anterioară
ferestrei testate (minimum 10 astfel de medii).

**Ce afirmă.** `z` pe ruptura recentă a raportului. Verdictul e
`relatie_in_limitele_biasului_istoric` la |z| ≤ 1,5, altfel
`relatie_recent_schimbata`.

**Ce NU afirmă — declarat explicit.**
> „diferența absolută măsurat–model nu este anomalie; semnalul este ruptura
> recentă a raportului"

**Limite.** Aceeași supraestimare a lui `n_etalon`: mediile pe 7 observații se
suprapun pe 6 din 7, deci numărul efectiv de mostre independente e ~n/7. Baza de
referință e de doar ~90 de zile, deci nu acoperă un ciclu sezonier — o deplasare
sezonieră normală a biasului model-măsurătoare poate declanșa semnalul.

---

## 4. Coerență precipitații ↔ debit — `precip_coherence()`

**Date.** ERA5, două puncte-proxy: bazin superior (Passau) și bazin mijlociu
(Budapesta). Debitul: percentila GloFAS de la Baziaș.

**Fereastra.** Cumul de **90 de zile calendaristic consecutive** (se refuză
ferestrele care nu au exact 90 de zile calendaristice între capete).

**Referința.** Cumulele de 90 de zile care se termină la **aceeași dată
calendaristică ±5 zile**, din anii 2000 → anul trecut.

**Ce afirmă.** Doi indicatori alăturați: percentila ploii pe 90 de zile și
percentila debitului. **Euristic de citire umană**, nu test statistic.

**Ce NU afirmă.** Nu e bilanț hidrologic. Nu e medie de bazin — sunt două puncte.
Nu se calculează nicio corelație, niciun prag automat, niciun verdict.

**Limite.** `mostre_referinta` (~275) numără ferestre de 90 de zile care se
suprapun aproape complet în interiorul aceluiași an; mostrele independente sunt
~25 (câte una pe an).

---

## 5. Mire încrucișate AFDJ ↔ DanubeSTREAM — `crosscheck_mire()`

**Date.** Cotele AFDJ (citirea de dimineață, XML public) și aceleași mire în
rețeaua de navigație DanubeSTREAM (cvasi-orar). Numai stațiile RO.

**Metrica.** Diferența în cm pe stațiile comune; se publică mediana și maximul
abaterii și primele 3 stații după abatere absolută.

**Ce afirmă.** Că două sisteme raportează aceeași miră. Diferențe mari și
persistente ar însemna că unul greșește sau raportează altceva.

**Ce NU afirmă.** Nu e verificare a exactității măsurătorii — ambele sisteme
citesc, în principiu, aceleași instrumente. Diferențele mici țin de ora citirii
și de variația zilei. La Călărași cele două sisteme par să citească **mire fizice
diferite** (limită cunoscută, afișată).

---

## 6. Altimetrie satelitară — `satellite_check()`

**Date.** hydroweb.next / CNES (Sentinel-3/6, SWOT), stații virtuale pe cursul
principal.

**Filtre de eligibilitate.** Numai observații proaspete (≤35 zile), cu
incertitudine ≤0,25 m și istoric lunar suficient. Se cer ≥6 stații eligibile pe
≥3 segmente distincte.

**Metrica.** Mediana percentilelor lunare proprii ale stațiilor eligibile.

**Ce afirmă.** `shadow_coerent` dacă mediana ≤ 15 — adică nivelurile văzute din
orbită sunt, în ansamblu, în partea joasă a propriei lor climatologii.

**Ce NU afirmă — declarat explicit.**
> „probă secundară din orbită; nu transformă nivelul în debit și nu este numărată
> ca mai multe surse independente"

Reperul e geoidul, nu zero-ul mirei. Stațiile apropiate și produsele pe aceleași
misiuni nu se numără drept surse independente.

---

## 7–9. Perechi măsurat ↔ model pe alte teritorii

Trei detectoare cu aceeași structură: debit **măsurat** de un operator național
vs. GloFAS în aceeași secțiune.

| Detector | Secțiune | Sursă măsurată |
|---|---|---|
| `germany_check()` | Hofkirchen (km 2257) | PEGELONLINE / WSV, debit orar |
| `hungary_check()` | Budapesta (km 1647) | OVF prin Hydroinfo, normalizat și prin ICPDR DanubeHIS |
| `serbia_check()` | Novi Sad (km 1255) | RHMZ; dacă lanțul TLS RHMZ nu se verifică, se preferă livrarea OVF/Hydroinfo |

**Metrica.** Raportul `măsurat / model`.

**Pragul.** `coerent` dacă raportul e în banda **0,40 – 2,50**.

**Ce afirmă.** Doar **incompatibilitatea grosieră**. Textul din payload spune
explicit:
> „Se testează DOAR incompatibilitatea grosieră (bandă 0,4–2,5): o deplasare lentă
> a raportului rămâne înăuntru și nu este detectată aici."

La Budapesta se verifică suplimentar dacă cele două livrări (Hydroinfo direct și
DanubeHIS) diferă cu ≤15 %, cu mențiunea explicită că **nu sunt măsurători
independente**.

**Ce NU afirmă.** Banda 0,4–2,5 nu validează modelul. Nu e calibrare, nu e
corecție de bias, nu se folosește pentru a ajusta seriile.

**Limită.** Pragurile 0,4 și 2,5 sunt alese, nu derivate din distribuția
istorică a raportului. Nu există echivalentul detectorului INHGA (test de ruptură)
pe aceste trei secțiuni.

---

## 10. Screening mire austriece — `austria_check()`

**Date.** Mirele VIA DONAU (orare, publice prin PEGELONLINE), nivel W, ultimele
30 de zile; minimum 4 stații cu ≥200 de valori.

**Metrica.** Diferența dintre media ultimei zile și media primei zile din
fereastra de 30 de zile (cm/30 zile), per stație; se publică mediana pe stații.
Separat: variația procentuală a debitului **măsurat** la Hofkirchen (intrarea
dinspre Germania) pe aceeași fereastră.

**Regula.** `suspiciune_retentie = True` **numai dacă** mediana trendului > +15 cm
**ȘI** trendul de intrare e cunoscut **ȘI** ≤ 0. Dacă intrarea nu se poate citi,
starea e `necunoscuta` și suspiciunea rămâne falsă — necunoscutul nu se tratează
niciodată ca îndeplinit.

**Ce NU afirmă — declarat explicit.**
> „niveluri stabile nu exclud manevre: lipsesc curbele cotă-volum și debitele de
> intrare/ieșire ale fiecărui baraj"

Nu e bilanț de stocare. Este screening de tendință, cu putere de detecție
nedeterminată.

---

## 11. Context GRDC (istoric măsurat) — `grdc_context()`

**Date.** Serie zilnică **măsurată** GRDC (Koblenz) la Ceatal Izmail (6742900),
snapshot 1991–2024, cerut manual de pe portalul GRDC și păstrat local.

**Metrica.** Percentila valorii **de azi din model (GloFAS)** în istoricul
**măsurat** GRDC din fereastra ±7 zile.

**Ce NU afirmă — declarat explicit.**
> „valoarea de azi e din model (GloFAS); istoricul e măsurat (GRDC) — comparație
> orientativă între produse necalibrate unul față de celălalt"

`record_minim_zi` (superlativul „cea mai mică valoare din această zi") se publică
**numai** dacă există ≥10 ani cu exact acea zi calendaristică, iar numitorul lui
(`ani_cu_aceasta_zi`) e diferit de `mostre_referinta` (fereastra ±7 zile) și se
publică separat.

---

## 12. Statistici publice — `full_stats()` / `precip_stats()`

**Debit.** Rezumat pe 14 secțiuni al detectorului 1.

**Precipitații (6 zone-proxy ERA5: Passau, Budapesta, Craiova, București, Galați,
Tulcea).** Pentru fiecare zonă:
- **ian → azi**: cumul de la 1 ianuarie până la ziua curentă vs. mediana
  acelorași ferestre din 2000 → anul trecut;
- **iarna nov–mar**: dacă iarna curentă e în desfășurare (ian–mar), se compară cu
  **aceeași porțiune** din iernile istorice, nu cu ierni întregi;
- **zăpadă iarna**: cumul `snowfall_sum`, în **cm zăpadă proaspătă**;
- **ultimele 90 de zile**: percentila cumulului, referință ±5 zile calendaristice.

**Regula de la început de an.** ERA5 are întârziere ~3 zile; pe 1–3 ianuarie anul
„curent" nu are date, deci se raportează anul precedent, complet. O iarnă de două
luni nu se compară cu ierni de cinci.

**Ce NU afirmă.** Cele 6 puncte **nu** sunt medii de bazin. Sunt celule ERA5
individuale, etichetate cu numele unei regiuni. Zăpada în cm zăpadă proaspătă
**nu** e echivalent în apă și nu se însumează cu ploaia.

---

## 13. Bilanțul „unde e apa" — `water_budget()`

Cel mai fragil detector din aplicație. Se citește ultimul.

**Perimetru.** Bazinul Dunării la **Achleiten/Passau**, `AREA = 76 650 km²`
(valoarea publicată e 76 653 km²).

**Ploaia.** Media **aritmetică neponderată** a **6 puncte ERA5**:

| punct | lat, lon | caracter | P medie 2000–2025 |
|---|---|---|---|
| Passau | 48,57 / 13,45 | valea Dunării, câmpie | 889 mm/an |
| Regensburg | 49,02 / 12,10 | Bavaria de nord | 802 mm/an |
| Ulm | 48,40 / 10,00 | Dunărea șvabă | 903 mm/an |
| Salzburg | 47,80 / 13,04 | prealpin | 1 806 mm/an |
| Inn-Tirol | 47,27 / 11,40 | valea Innului, alpin | 1 662 mm/an |
| Inn-sud | 46,95 / 10,50 | Alpii înalți | 1 401 mm/an |

Cumul de la 1 ianuarie (fără 29 feb.), înmulțit cu aria: `1 mm pe bazin = 0,07665 km³`.

**Râul.** GloFAS la Passau, cumulat pe **exact aceeași fereastră calendaristică**;
`Σ Q × 86 400 / 10⁹` km³. Anii incompleți sunt refuzați explicit (altfel orice gol
ar mări artificial „apa lipsă").

**Rezidualul.** `rest = P − Q`, publicat ca `atmosfera_sol_km3`.

**Ce NU afirmă — declarat explicit în payload.**
> „rezidual P−Q = evapotranspirație + variația stocurilor + schimburi neobservate
> + eroarea proxy/model"
>
> „captările, transferurile și variația stocurilor nu sunt cuantificate separat în
> datele publice folosite aici"

### Limite de care depinde interpretarea

1. **Acoperire spațială: ~4 %.** 6 celule ERA5 de 0,25° (~500 km² fiecare la 48°N)
   pe 76 650 km². Densitatea minimă recomandată de OMM pentru rețele
   pluviometrice în regiuni muntoase temperate e **1 stație / 100–250 km²**.
   Atenuant real: o celulă ERA5 e deja o medie areală a unui câmp fizic coerent,
   nu o măsurătoare punctuală — 6 mostre dintr-un câmp neted valorează mult mai
   mult decât 6 pluviometre. Bazinul Innului, Lechul, Isarul și Naab/Regenul nu
   au însă niciun punct propriu.
2. **Ponderare neponderată.** Cele 3 puncte alpine primesc 50 % din pondere, deși
   bazinul Inn (26 130 km²) e ~34 % din suprafață. Efect estimat: **+10…+20 % pe
   nivelul P**.
3. **Verificare de închidere (2000–2025, medii anuale):**
   - P din cele 6 puncte = **1 244 mm/an**
   - Q GloFAS la Passau = **596 mm/an** (concordant cu media publicată de
     1 420 m³/s ⇒ 584 mm/an)
   - ⇒ ET implicit = **648 mm/an**, coeficient de scurgere **Q/P = 0,48**.

   Valorile publicate pentru bazinul superior sunt în jur de 0,55–0,60. **Cele 6
   puncte supraestimează probabil ploaia areală cu ~15–20 %.**
4. **Nivelul absolut e nesigur, anomalia e robustă.** Test jackknife pe YTD 2026
   (1 ian – 4 aug):

   | set de puncte | P curent | mediana | anomalie |
   |---|---|---|---|
   | toate 6 (metoda aplicației) | 42,1 km³ | 58,7 km³ | −16,6 km³ |
   | scoțând un punct pe rând | 38,3…45,7 | 52,6…62,2 | −14,2…−17,3 |
   | ponderat pe suprafață (0,34 alpin) | 37,4 km³ | 52,3 km³ | −14,9 km³ |
   | numai cele 3 de câmpie | 27,2 km³ | 40,0 km³ | −12,8 km³ |
   | numai cele 3 alpine | 57,1 km³ | 75,9 km³ | −18,9 km³ |

   **Concluzie: „ploaia e cu ~28 % sub normal" e o afirmație solidă. „Ploaia a fost
   42,1 km³" și „atmosfera și solul au luat 24,3 km³" NU sunt.**
5. **`atmosfera_sol_km3` nu e evapotranspirație.** Pe o fereastră 1 ian – august,
   variația stocurilor (zăpadă, umiditatea solului, freatic) e mare și
   sistematic negativă — stratul de zăpadă se topește în interiorul ferestrei.
   Denumirea câmpului sugerează o mărime fizică pe care rezidualul nu o izolează.
6. **`lipsa_km3` la Baziaș** = mediana istorică minus anul curent, ambele din
   GloFAS. E un **deficit față de normala modelului**, nu apă sustrasă.
7. **GRACE.** Se atașează dacă e disponibil; nota din sursă spune corect
   „rezoluția reală GRACE e ~300 km, semnalul se amestecă între casete vecine;
   decalaj de publicare ~1 an". Bazinul superior (76 650 km²) e la limita de
   rezoluție a produsului.

---

## 14. Ce NU face aplicația (deloc)

Enumerat explicit, ca să nu fie căutat degeaba:

- **Nu convertește cotă în debit** și nu folosește nicio curbă cotă-debit.
- **Nu convertește cm la miră în mdMB** și nu compară cota AFDJ Cernavodă cu
  pragurile bazinului de aspirație al CNE (care sunt în mdMB).
- **Nu convertește cm zăpadă proaspătă în echivalent apă.**
- **Nu calculează timp de revenire.** Nu există MAM7, 7Q10, Q95, curbă de durată,
  ajustare de distribuție.
- **Nu calculează volum de deficit** (`Σ (Q_prag − Q) · Δt`) — indicatorul standard
  al metodei pragului, deși aplicația calculează deja km³ în alte locuri.
- **Nu grupează episoadele de secetă** (fără criteriu de timp inter-eveniment,
  fără algoritm sequent-peak); publică o singură serie neîntreruptă, plafonată la
  45 de zile.
- **Nu exclude perioadele cu fenomene de gheață.**
- **Nu publică intervale de încredere** pe percentile, pe P10 sau pe z.
- **Nu distinge regim natural de regim influențat** (regularizare Porțile de Fier,
  captări, folosințe). Toate seriile sunt „influențate", niciuna nu e naturalizată.
- **Nu emite acuzații.** Niciun detector nu produce o concluzie despre o țară, un
  operator sau o instituție; toate produc cifre cu numitorul lor la vedere.

---

## 15. Listă de verificare rapidă (15 minute)

Pentru un hidrolog care vrea să valideze sau să respingă:

1. **Perioada de referință.** Verificați `reference_period.effective_start` în
   `/api/statistici`. Trebuie să fie **1997**, nu 1991. Dacă textul din interfață
   spune „1991", e o descriere a intenției, nu a datelor.
2. **Percentila 0,0.** Dacă vedeți `percentila: 0.0`, citiți `ani_mai_mici` și
   `ani_referinta` — afirmația reală e „sub minimul celor 29 de ani", nu „exact
   percentila zero".
3. **`zile_sub_p10 = 45`.** E plafonul, nu durata. Cereți seria completă.
4. **`n_etalon` la bilanț și la INHGA↔model.** Împărțiți la lungimea ferestrei
   (14, respectiv 7) ca să obțineți ordinul de mărime al mostrelor independente.
5. **Toate percentilele GloFAS.** Comparați-le, dacă puteți, cu percentila
   calculată pe seria măsurată la aceeași secțiune. Diferența dintre ele e
   *singura* măsură a erorii de model pe care aplicația nu o poate produce singură.
6. **Bilanțul de apă.** Comparați P = 1 244 mm/an (cele 6 puncte) cu valoarea
   areală din studiile de bilanț ale bazinului Dunării superioare. Dacă e mai mică
   de ~1 100 mm/an, toate mărimile absolute din secțiunea „bazin superior" sunt
   umflate proporțional; anomaliile procentuale rămân valide.
7. **Reziduul Baziaș→Gruia.** Media multianuală de +3,74 % ar trebui să fie
   compatibilă cu aportul afluenților dintre km 1071 și km 851. Dacă la INHGA/AFDJ
   există o cifră pentru acest aport, e testul cel mai direct al coerenței
   modelului pe sectorul românesc.

---

## 16. Referințe de metodă

- OMM — *Guide to Hydrological Practices* (WMO-No. 168), Vol. I–II —
  densități minime de rețea, analiza apelor mici.
- OMM — *Guidelines on the Calculation of Climate Normals* (WMO-No. 1203) —
  normale standard pe 30 de ani, actualizate decadal (1991–2020 este cea în vigoare).
- Tallaksen & Van Lanen (ed.) — *Hydrological Drought: Processes and Estimation
  Methods for Streamflow and Groundwater*, ed. a 2-a, Elsevier — metoda pragului,
  praguri variabile, volum de deficit, gruparea episoadelor.
- USGS WaterWatch — clasele de percentile ale debitului zilnic (<10 = „much below
  normal"), cerința de ≥30 de ani de înregistrări.
- Comisia Dunării — definiția ENN/LNWL: nivelul atins sau depășit în medie 94 %
  din zilele anului, pe o perioadă de referință de mai multe decenii,
  **exclusiv perioadele cu gheață**.
- Harrigan et al. (2020), *GloFAS-ERA5 operational global river discharge
  reanalysis 1979–present*, Earth Syst. Sci. Data 12, 2043–2060 — skill, biasuri,
  limitele reprezentării lacurilor de acumulare.
- Copernicus EMS — GloFAS v4 calibration and hydrological model performance.
- Open-Meteo — documentația Historical Weather API (unități: `precipitation_sum`
  în mm inclusiv echivalentul ninsorii; `snowfall_sum` în cm zăpadă proaspătă;
  ERA5 la 0,25°).

---

*Document generat ca parte a auditului de metodă. Cifrele-exemplu sunt din
7 august 2026 și pot fi recalculate din cache-ul local sau din sursele publice
enumerate.*
