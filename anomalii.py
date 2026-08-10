"""Detectoare de anomalii pe datele disponibile.

Fiecare detector întoarce un verdict cu metoda și limitele lui — scopul e
separarea onestă între „anormal față de istoric, dar explicabil" (secetă)
și „nu se leagă în bilanț" (de investigat). Nicio acuzație automată:
detectoarele produc cifre verificabile, nu concluzii.
"""

import math
import threading
from datetime import date, timedelta
from statistics import mean, median, pstdev

import connectors as C

CLIM_START = 1991      # referința climatologică
BAL_START = 2015       # referința pentru bilanțul Baziaș→Gruia

# Versiunile fac imposibil ca rezultate calculate cu metoda veche (de ex.
# seria meteo „Best Match”) să rămână șase ore în cache după deploy.
# v12: seria sub P10 nu mai e plafonată de fereastra de afișare (Passau era
# publicat 45 în loc de 53), se adaugă volumul deficitului sub prag, iar
# câmpurile bilanțului sunt redenumite ca să nu mai afirme mai mult decât pot.
# v11: rapoartele expun acum `surse_din_cache`/`intrari_din_cache` — schema
# payloadului s-a schimbat, deci un răspuns vechi ar masca tăcut câmpurile noi.
# v10: textul CLIM_NOTES pentru Cernavodă nu mai afirmă generic, la prezent, o
# limitare a funcționării — afirmațiile vizibile fac parte din payload, deci o
# corecție de text cere la fel de mult o cheie nouă ca o schimbare de calcul.
# v9/v5/v4: etalonul z-scorului e acum distribuția mediilor pe fereastră (nu a
# valorilor zilnice), zăpada e raportată în cm, streak-ul numără zile
# calendaristice, bilanțul km³ refuză anii incompleți, iar percentilele cer un
# minim de eșantioane. Rezultatele vechi nu mai sunt comparabile.
REPORT_CACHE_KEY = "anomalii_report:v12"
STATS_CACHE_KEY = "statistici:v7"
BUDGET_CACHE_KEY = "bilant_apa:v6"


# ------------------------------------------------------------- utilitare ---

# Rapoartele derivate despachetau fiecare conector cu `["data"]` și aruncau
# flagul `stale`. Rezultatul: `stale: false` pe /api/anomalii însemna doar
# „recalculat în ultimele 6 ore" — rămânea fals și când TOATE intrările veneau
# din cache expirat. Colectorul de mai jos reține ce sursă a venit din cache,
# ca verdictele să poată declara pe ce s-au sprijinit.
_stale_ctx = threading.local()


def _track_reset():
    _stale_ctx.names = set()


def _tracked():
    return sorted(getattr(_stale_ctx, "names", set()) or set())


def _d(result, name):
    """Despachetează un rezultat de conector, reținând dacă era din cache."""
    if isinstance(result, dict) and "data" in result:
        if result.get("stale"):
            names = getattr(_stale_ctx, "names", None)
            if names is None:
                names = set()
                _stale_ctx.names = names
            names.add(name)
        return result["data"]
    return result


def _series_map(arch):
    """arhivă GloFAS → dict {data_iso: valoare}, fără None."""
    return {t: v for t, v in zip(arch["time"], arch["discharge"]) if v is not None}


def _mmdd(ds):
    return ds[5:]


def _as_cm(blk):
    """Redenumește câmpurile `_mm` ale unui bloc în `_cm`, fără conversie."""
    if not isinstance(blk, dict):
        return blk
    return {(k[:-3] + "_cm" if k.endswith("_mm") else k): v for k, v in blk.items()}


# Sub acest prag distribuția de referință e prea săracă pentru a susține o
# percentilă: o singură valoare ar produce P0 sau P100 „încrezător".
MIN_REF_SAMPLES = 30


def _rank(value, ref):
    """percentila empirică a valorii în distribuția de referință (0–100)."""
    if not ref or len(ref) < MIN_REF_SAMPLES:
        return None
    below = sum(1 for r in ref if r <= value)
    return round(100.0 * below / len(ref), 1)


def _rolling_window_means(smap, window, month=None, exclude_year=None):
    """Mediile ferestrelor de `window` zile CALENDARISTIC consecutive.

    Etalonul corect pentru media unei ferestre nu e abaterea standard a
    valorilor zilnice, ci distribuția mediilor de aceeași lungime: media a k
    zile corelate variază mult mai puțin decât o zi, iar raportarea la SD-ul
    zilnic comprimă z-ul spre zero de câteva ori.
    Ferestrele cu o zi lipsă se sar — altfel „media pe 14 zile" ar fi media a
    14 valori disponibile, întinsă peste mai multe săptămâni.
    """
    out = []
    days = sorted(smap)
    for i in range(window - 1, len(days)):
        block = days[i - window + 1:i + 1]
        first, last = date.fromisoformat(block[0]), date.fromisoformat(block[-1])
        if (last - first).days != window - 1:
            continue
        if month is not None and last.month != month:
            continue
        if exclude_year is not None and last.year == exclude_year:
            continue
        out.append(mean(smap[d] for d in block))
    return out


def _contiguous_tail(smap, window):
    """Ultimele `window` zile calendaristic consecutive, sau None dacă lipsesc."""
    days = sorted(smap)
    if len(days) < window:
        return None
    block = days[-window:]
    first, last = date.fromisoformat(block[0]), date.fromisoformat(block[-1])
    if (last - first).days != window - 1:
        return None
    return [smap[d] for d in block]


def _doy_reference(smap, exclude_year, window=7):
    """Pentru fiecare zi calendaristică: valorile istorice din fereastra
    ±window zile, din toți anii în afară de cel exclus."""
    from collections import defaultdict
    bydate = defaultdict(list)
    dates = sorted(smap)
    for ds in dates:
        if ds.startswith(str(exclude_year)) or _mmdd(ds) == "02-29":
            continue
        bydate[_mmdd(ds)].append(smap[ds])

    mmdds = sorted(bydate)
    idx = {m: i for i, m in enumerate(mmdds)}
    n = len(mmdds)
    ref = {}
    for m in mmdds:
        vals = []
        for off in range(-window, window + 1):
            vals.extend(bydate[mmdds[(idx[m] + off) % n]])
        ref[m] = vals
    return bydate, ref


# --------------------------------------- indici standard de ape mici -------

# Anul hidrologic începe la 1 aprilie. NU e o convenție luată din obișnuință:
# măsurat pe seria proprie (3 secțiuni × 29 de ani), 75% dintre minimele anuale
# pe 7 zile cad în decembrie–martie, cu ianuarie dominant. Un an calendaristic
# ar rupe exact sezonul de ape mici în două — minimul din decembrie și cel din
# ianuarie următor sunt același episod, dar ar fi numărate ca doi ani diferiți.
WATER_YEAR_START_MONTH = 4
# Sub atâția ani compleți nu publicăm indicii: 7Q10 cere o perioadă de revenire
# de 10 ani, iar regula uzuală e ca T să nu depășească aproximativ n/2.
MIN_YEARS_LOW_FLOW = 20
# Cuantila normală standard pentru 10% neîncadrare (adică T = 10 ani).
_Z_10 = -1.2815515655446004


def _water_year(ds):
    """Anul hidrologic al unei date ISO (începe la 1 aprilie)."""
    year, month = int(ds[:4]), int(ds[5:7])
    return year if month >= WATER_YEAR_START_MONTH else year - 1


def _moving_mean_map(smap, window):
    """Medii mobile pe `window` zile CALENDARISTIC contigue, cheiate pe ultima zi."""
    zile = sorted(smap)
    out = {}
    for i in range(window - 1, len(zile)):
        blok = zile[i - window + 1:i + 1]
        if (date.fromisoformat(blok[-1])
                - date.fromisoformat(blok[0])).days != window - 1:
            continue
        out[blok[-1]] = sum(smap[d] for d in blok) / window
    return out


def low_flow_indices(point_id, window=7):
    """MAM7, 7Q10 și curba duratei debitelor — indicii standard din practică.

    Toți se calculează pe seria GloFAS, deci descriu regimul MODELULUI. Sunt
    debite INFLUENȚATE (Porțile de Fier, captări), nu regim natural, iar o
    perioadă de revenire de 10 ani e la limita a ce pot susține ~29 de ani.
    """
    smap = _series_map(C.glofas_archive(point_id, CLIM_START)["data"])
    if not smap:
        raise RuntimeError("serie goală")
    cur_wy = _water_year(date.today().isoformat())
    ma = _moving_mean_map(smap, window)

    zile_pe_an = {}
    for ds in smap:
        wy = _water_year(ds)
        zile_pe_an[wy] = zile_pe_an.get(wy, 0) + 1

    minime, minime_calde = {}, {}
    for ds, val in ma.items():
        wy = _water_year(ds)
        if wy >= cur_wy or zile_pe_an.get(wy, 0) < 364:
            continue
        if _mmdd(ds) == "02-29":
            continue
        if val < minime.get(wy, (1e18,))[0]:
            minime[wy] = (val, ds)
        # Varianta fără sezonul rece: convenția Comisiei Dunării exclude
        # perioadele cu gheață când definește niveluri de referință, iar GloFAS
        # nu modelează gheața deloc — minimele lui de iarnă nu sunt comparabile
        # cu un minim măsurat, afectat de zăpor.
        if 4 <= int(ds[5:7]) <= 11 and val < minime_calde.get(wy, (1e18,))[0]:
            minime_calde[wy] = (val, ds)

    ani = sorted(minime)
    if len(ani) < MIN_YEARS_LOW_FLOW:
        raise RuntimeError(
            f"prea puțini ani hidrologici compleți ({len(ani)}) pentru indici de ape mici")

    serie = [minime[y][0] for y in ani]
    mam = mean(serie)
    serie_calda = [minime_calde[y][0] for y in sorted(minime_calde)]

    # 7Q10 = cuantila de 10% neîncadrare a minimelor anuale, potrivire
    # log-normală cu 2 parametri. Fără scipy; log-normala e alegerea uzuală
    # pentru ape mici, iar log-Pearson III ar cere și estimarea asimetriei.
    q10 = None
    if all(v > 0 for v in serie):
        logs = [math.log(v) for v in serie]
        mu, sd = mean(logs), pstdev(logs)
        q10 = math.exp(mu + _Z_10 * sd)

    # Curba duratei: Qx = debitul DEPĂȘIT x% din timp. Convenție INVERSĂ față
    # de „percentila" din restul aplicației: Q95 e un debit MIC, percentila 95
    # e o valoare MARE. Confuzia dintre ele e clasică.
    ref = sorted(val for ds, val in smap.items()
                 if _water_year(ds) < cur_wy and _mmdd(ds) != "02-29")
    fdc = {}
    for exc in (5, 10, 50, 70, 90, 95, 99):
        idx = min(len(ref) - 1,
                  max(0, int(round((100 - exc) / 100 * (len(ref) - 1)))))
        fdc[f"Q{exc}"] = round(ref[idx], 1)

    curent = [val for ds, val in ma.items() if _water_year(ds) == cur_wy]
    min_curent = min(curent) if curent else None

    return {
        "id": point_id, "name": C.GLOFAS_POINTS[point_id]["name"],
        "tip_proba": "model_hidrologic",
        "sursa": "GloFAS v4 — indici calculați aici din seria modelului",
        "an_hidrologic": "1 aprilie – 31 martie",
        "ani_folositi": len(ani),
        "perioada": {"start": ani[0], "end": ani[-1]},
        f"MAM{window}_m3s": round(mam, 1),
        f"MAM{window}_fara_sezon_rece_m3s": (
            round(mean(serie_calda), 1)
            if len(serie_calda) >= MIN_YEARS_LOW_FLOW else None),
        f"{window}Q10_m3s": round(q10, 1) if q10 else None,
        "minim_an_curent_m3s": round(min_curent, 1) if min_curent is not None else None,
        "sub_7Q10_acum": (bool(q10 and min_curent is not None and min_curent < q10)
                          if q10 else None),
        "curba_duratei_m3s": fdc,
        "minime_anuale": [{"an_hidrologic": y, "m3s": round(minime[y][0], 1),
                           "data": minime[y][1]} for y in ani],
        "metoda": (
            f"MAM{window} = media minimelor anuale ale mediei mobile pe {window} "
            f"zile; {window}Q10 = cuantila de 10% neîncadrare a acelorași minime, "
            "potrivire log-normală; curba duratei = debitul depășit x% din timp "
            "(Q95 e debit MIC — convenție inversă față de percentilele din restul "
            "aplicației). Anul hidrologic începe la 1 aprilie fiindcă 75% dintre "
            "minimele acestei serii cad în decembrie–martie."),
        "limite": (
            "Serie de MODEL, nu măsurători, și regim INFLUENȚAT (Porțile de Fier, "
            "captări), nu natural. Perioada de revenire de 10 ani e la limita a ce "
            "pot susține ~29 de ani. GloFAS nu modelează gheața, deci minimele de "
            "iarnă nu sunt comparabile cu minime măsurate afectate de zăpor — de "
            "aceea publicăm și varianta fără sezonul rece, mai apropiată de "
            "convenția Comisiei Dunării pentru niveluri de referință."),
    }


# ------------------------------------------------- 1. climatologie puncte ---

# transect complet: dacă seceta „vine pe apă", percentilele joase încep amonte
CLIM_POINTS = ["regensburg", "passau", "linz", "viena", "bratislava",
               "budapesta", "mohacs", "novi_sad",
               "bazias", "gruia", "zimnicea", "cernavoda", "braila",
               "ceatal_izmail"]

# context special pe secțiuni — afișat în celula respectivă
CLIM_NOTES = {
    "cernavoda": ("CNE Cernavodă — apa de răcire vine din Dunăre; la ape foarte "
                  "scăzute operatorul a limitat în trecut funcționarea "
                  "(precedent confirmat: 2003; episoadele recente sunt în "
                  "tabelul operațional). "
                  "Atenție: nivelul local e influențat și de lucrările oficiale "
                  "de la brațul Bala (vezi buletinul INHGA)."),
}


def climatology(point_id):
    arch = _d(C.glofas_archive(point_id, CLIM_START), f"glofas:{point_id}")
    smap = _series_map(arch)
    if not smap:
        raise RuntimeError("serie goală")
    cur_year = date.today().year
    reference_years = sorted({int(ds[:4]) for ds in smap
                              if not ds.startswith(str(cur_year))})
    exact, ref = _doy_reference(smap, cur_year)

    # ultimele 45 de zile calendaristice, indiferent de anul lor — altfel
    # seria „zile sub P10" s-ar reseta artificial pe 1 ianuarie
    azi = date.today().isoformat()
    toate = sorted(d for d in smap if d <= azi)
    # Fereastra de 45 de zile e pentru AFIȘARE. Seria se numără pe toată
    # arhiva: plafonată la fereastră, „45 de zile sub P10" nu era o durată, ci
    # limita graficului — la Passau adevărul era 53. Un număr care nu poate
    # crește peste o constantă de randare nu e o măsurătoare.
    days = toate[-45:]
    recent = []
    for ds in days:
        r = _rank(smap[ds], ref.get(_mmdd(ds), []))
        recent.append({"date": ds, "value": round(smap[ds], 1), "pct": r})

    # Zile calendaristic consecutive, nu „valori disponibile consecutive": cu un
    # gol în arhivă, „a 23-a zi la rând sub P10" putea acoperi 30 de zile reale.
    streak = 0
    asteptata = None
    for ds in reversed(toate):
        zi = date.fromisoformat(ds)
        if asteptata is not None and zi != asteptata:
            break
        r = _rank(smap[ds], ref.get(_mmdd(ds), []))
        if r is not None and r < 10:
            streak += 1
            asteptata = zi - timedelta(days=1)
        else:
            break

    # O valoare sub tot ce conține referința nu are percentilă empirică: „P0,0"
    # sugerează o precizie pe care 29 de ani nu o pot da. O marcăm ca atare.
    for item in recent:
        item["sub_minimul_referintei"] = (item["pct"] is not None and item["pct"] == 0.0)
    last = recent[-1] if recent else None
    mediana_zilei = abatere_pct = None
    if last:
        refv = ref.get(_mmdd(last["date"]), [])
        if refv:
            mediana_zilei = round(median(refv), 1)
            if mediana_zilei:
                abatere_pct = round(100 * (last["value"] - mediana_zilei)
                                    / mediana_zilei, 1)
    ani_mai_mici = None
    n_ani = None
    if last:
        exact_vals = exact.get(_mmdd(last["date"]), [])
        n_ani = len(exact_vals)
        ani_mai_mici = sum(1 for v in exact_vals if v < smap[last["date"]])

    # Volumul deficitului sub prag — ieșirea standard a metodei „threshold
    # level" din literatura de secetă hidrologică (Tallaksen & Van Lanen), pe
    # care aplicația o folosea fără s-o raporteze. Σ(P10 − Q)·Δt pe seria
    # curentă, adică „câtă apă lipsește față de prag", nu doar „de câte zile".
    prag_deficit_km3 = None
    zile_deficit = 0
    for ds in reversed(toate):
        refv = ref.get(_mmdd(ds), [])
        if len(refv) < MIN_REF_SAMPLES:
            break
        prag = sorted(refv)[max(0, int(len(refv) * 0.10) - 1)]
        if smap[ds] >= prag:
            break
        zile_deficit += 1
        prag_deficit_km3 = (prag_deficit_km3 or 0.0) + (prag - smap[ds]) * 86400 / 1e9

    p = C.GLOFAS_POINTS[point_id]
    result = {
        "id": point_id, "name": p["name"], "km": p["km"],
        "tip_proba": "model_hidrologic",
        "sursa": "GloFAS v4 via Open-Meteo Flood API",
        "rezolutie_spatiala_aprox_km": 5,
        "azi": last, "streak_sub_p10": streak,
        "deficit_sub_p10": ({"km3": round(prag_deficit_km3, 3), "zile": zile_deficit,
                             "metoda": "Σ(P10 − Q)·Δt pe seria curentă sub prag "
                                       "(threshold level method); volum modelat, "
                                       "nu apă măsurată ca lipsă"}
                            if prag_deficit_km3 else None),
        "ani_mai_mici": ani_mai_mici, "ani_referinta": n_ani,
        "reference_period": ({"requested_start": CLIM_START,
                              "effective_start": reference_years[0],
                              "effective_end": reference_years[-1]}
                             if reference_years else None),
        "mediana_zilei": mediana_zilei, "abatere_pct": abatere_pct,
        "recent": recent,
    }
    cell = arch.get("cell")
    if cell:
        result["celula_model"] = {k: cell.get(k) for k in ("lat", "lon")}
    return result


# ------------------------------------- 2. bilanțul Baziaș→Gruia (cu lag) ---

def balance():
    b = _series_map(_d(C.glofas_archive("bazias", BAL_START), "glofas:bazias"))
    g = _series_map(_d(C.glofas_archive("gruia", BAL_START), "glofas:gruia"))
    dates = sorted(set(b) & set(g))
    if len(dates) < 400:
        raise RuntimeError("serii prea scurte pentru bilanț")

    # decalajul de propagare: corelația maximă a variațiilor zilnice
    def daily_changes(smap, ds_list):
        return [smap[ds_list[i]] - smap[ds_list[i - 1]] for i in range(1, len(ds_list))]

    def corr(xs, ys):
        mx, my = mean(xs), mean(ys)
        sx, sy = pstdev(xs), pstdev(ys)
        if sx == 0 or sy == 0:
            return 0.0
        return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) * sx * sy)

    tail = dates[-1500:]
    db = daily_changes(b, tail)
    dg = daily_changes(g, tail)
    best_lag, best_c = 0, -2
    for lag in range(0, 5):
        n = len(db) - lag
        c = corr(db[:n], dg[lag:lag + n])
        if c > best_c:
            best_lag, best_c = lag, c
    # Pornind de la -2, ORICE lag era adoptat, chiar cu o corelație de 0,03,
    # și publicat ca „corelatie". Sub prag folosim decalajul documentat.
    if best_c < 0.3:
        best_lag = 1

    # reziduul relativ: (ieșire - intrare decalată) / intrare
    rel = {}
    for i, ds in enumerate(dates):
        j = i - best_lag
        if j < 0:
            continue
        bi = b[dates[j]]
        if bi > 200:
            rel[ds] = (g[ds] - bi) / bi

    today = date.today()
    cur_month = today.month
    cur_year = today.year
    # Etalon: mediile ferestrelor de 14 zile care se ÎNCHEIE în aceeași lună,
    # din anii anteriori. Fereastra curentă e comparată cu obiecte de aceeași
    # natură — nu cu abaterea standard a valorilor zilnice.
    hist_means = _rolling_window_means(rel, 14, month=cur_month,
                                       exclude_year=cur_year)
    cur_window = _contiguous_tail(rel, 14)
    if len(hist_means) < 10 or cur_window is None:
        raise RuntimeError("date insuficiente pentru reziduu")

    mu, sd = mean(hist_means), pstdev(hist_means)
    cur = mean(cur_window)
    z = (cur - mu) / sd if sd > 0 else 0.0

    return {
        "lag_zile": best_lag, "corelatie": round(best_c, 3),
        "reziduu_curent_pct": round(100 * cur, 2),
        "reziduu_istoric_pct": round(100 * mu, 2),
        "sd_pct": round(100 * sd, 2),
        "z": round(z, 2),
        # Ferestrele de 14 zile se suprapun pe 13 din 14 zile, iar reziduul are
        # autocorelație mare: numărul de ferestre NU e numărul de observații
        # independente. Publicăm și anii distincți, care sunt.
        "n_etalon_ferestre": len(hist_means),
        "n_ani_independenti": len({int(d[:4]) for d in rel
                                   if int(d[5:7]) == cur_month and int(d[:4]) < cur_year}),
        "tip_proba": "model_hidrologic",
        "sursa": "GloFAS v4 — ambele secțiuni (Baziaș și Gruia)",
        "fereastra": "media ultimelor 14 zile consecutive vs. mediile pe 14 zile "
                     "din aceeași lună, 2015–anul trecut",
        "metoda": "reziduu relativ (Gruia − Baziaș decalat) / Baziaș, standardizat "
                  "față de distribuția mediilor pe 14 zile din aceeași lună. "
                  "AMBELE serii vin din același model GloFAS: testul detectează "
                  "o schimbare a consistenței interne a modelului, NU poate "
                  "detecta o captare sau o deviere reală de apă la Porțile de Fier.",
    }


# ------------------------------------ 3. măsurat (INHGA) vs model (GloFAS) --

def measured_vs_model():
    official = C.inhga_series(days=90)
    if len(official) < 10:
        return {"insuficient": True,
                "n": len(official),
                "nota": "arhiva buletinelor INHGA încă se descarcă în fundal — "
                        "revino în câteva minute"}
    m = _series_map(_d(C.glofas_archive("bazias", date.today().year - 1), "glofas:bazias"))
    ratios = []
    for row in official:
        mv = m.get(row["date"])
        if mv and mv > 0:
            ratios.append({"date": row["date"], "oficial": row["debit_m3s"],
                           "model": round(mv, 1),
                           "raport": round(row["debit_m3s"] / mv, 3)})
    if len(ratios) < 10:
        return {"insuficient": True, "n": len(ratios),
                "nota": "prea puține zile suprapuse între buletine și model"}
    rs = [r["raport"] for r in ratios]
    # Ultimele șapte zile sunt fereastra testată, nu parte din etalon;
    # includerea lor în medie și abatere ar micșora artificial ruptura.
    baseline = rs[:-7]
    # Etalonul e distribuția mediilor pe 7 observații, nu a valorilor
    # individuale: altfel media testată se compară cu o SD prea mare și
    # ruptura nu se declanșează practic niciodată.
    baseline_means = [mean(baseline[i - 6:i + 1]) for i in range(6, len(baseline))]
    if len(baseline_means) < 10:
        return {"insuficient": True, "n": len(ratios),
                "nota": "prea puține zile anterioare ferestrei testate"}
    mu, sd = mean(baseline_means), pstdev(baseline_means)
    last7 = mean(rs[-7:])
    z = (last7 - mu) / sd if sd > 0 else 0.0
    return {
        "n": len(ratios), "n_etalon": len(baseline_means),
        "raport_mediu": round(mu, 3), "sd": round(sd, 3),
        "raport_ultimele7": round(last7, 3), "z": round(z, 2),
        "ultima_pereche_aceeasi_data": ratios[-1],
        "evaluare": ("relatie_in_limitele_biasului_istoric"
                     if abs(z) <= 1.5 else "relatie_recent_schimbata"),
        "regula_interpretare": ("diferența absolută măsurat–model nu este anomalie; "
                                "semnalul este ruptura recentă a raportului"),
        "serie": ratios[-30:],
    }


# ------------------------------------- 4. coerența precipitații ↔ debit ----

PRECIP_VS = [("bazin_superior", "amonte: Germania/Austria"),
             ("bazin_mijlociu", "amonte: Ungaria")]


def _rolling90_percentile(dates, vals, end, exclude_year, window=5):
    """Același estimator pentru toate suprafețele: cumul de 90 zile și
    referință din ferestrele calendaristice ±window ale anilor anteriori."""
    # `dates` conține doar zilele cu valoare: însumarea a 90 de POZIȚII putea
    # întinde tăcut fereastra peste 91+ zile calendaristice și umfla cumulul,
    # în timp ce ferestrele istorice, complete, rămâneau de 90.
    cum90 = {}
    for i in range(89, len(dates)):
        first = date.fromisoformat(dates[i - 89])
        last = date.fromisoformat(dates[i])
        if (last - first).days != 89:
            continue
        cum90[dates[i]] = sum(vals[i - 89:i + 1])
    cur = cum90.get(end)
    if cur is None:
        return None, None, 0
    ref = [v for ds, v in cum90.items()
           if abs(_doy_diff(_mmdd(ds), _mmdd(end))) <= window
           and not ds.startswith(str(exclude_year))]
    return cur, _rank(cur, ref), len(ref)


def precip_coherence(discharge_pct):
    """Percentila cumulului de precipitații pe 90 de zile vs. istoric
    (aceeași fereastră calendaristică din anii anteriori)."""
    out = []
    all_points = _d(C.era5_precip_all(2000), "era5")
    for pid, label in PRECIP_VS:
        try:
            d = all_points[pid]
            smap = {t: v for t, v in zip(d["time"], d["precip"]) if v is not None}
            dates = sorted(smap)
            end = dates[-1]
            vals = [smap[ds] for ds in dates]
            cur, pct, n_ref = _rolling90_percentile(
                dates, vals, end, int(end[:4]), window=5)
            if cur is None:
                continue
            out.append({"zona": C.PRECIP_POINTS[pid]["name"], "eticheta": label,
                        "cum90_mm": round(cur, 1), "pct": pct,
                        "pana_la": end,
                        "fereastra_referinta": "aceeași dată calendaristică ±5 zile",
                        "mostre_referinta": n_ref})
        except Exception:
            continue
    return {"debit_pct": discharge_pct, "zone": out}


# ------------------------------------------------------ statistici complete --

PRECIP_START = 2000


def precip_stats():
    """Pentru fiecare zonă: cumul ian→azi vs. istoricul aceleiași ferestre,
    iarna nov–mar vs. istoricul ei, plus percentila ultimelor 90 de zile."""
    out = []
    today = date.today()
    cy = today.year
    all_points = _d(C.era5_precip_all(PRECIP_START), "era5")
    for pid, p in C.PRECIP_POINTS.items():
        d = all_points[pid]
        snow_raw = d.get("snow") or []
        pairs = [(t, v, (snow_raw[i] if i < len(snow_raw) else None))
                 for i, (t, v) in enumerate(zip(d["time"], d["precip"]))
                 if v is not None]
        if not pairs:
            continue
        end = pairs[-1][0]
        cutoff = _mmdd(end)

        # iarna = nov(an-1) → mar(an). Dacă iarna curentă e în desfășurare
        # (suntem în ian–mar), o comparăm cu ACEEAȘI porțiune din iernile
        # istorice — altfel am pune o iarnă pe jumătate lângă ierni întregi.
        iarna_cutoff = cutoff if cutoff <= "03-31" else "03-31"
        ytd, winter, wsnow = {}, {}, {}
        for ts, v, sn in pairs:
            y, m = int(ts[:4]), int(ts[5:7])
            md = _mmdd(ts)
            if md == "02-29":
                continue
            if md <= cutoff:
                ytd[y] = ytd.get(y, 0.0) + v
            if m >= 11:
                winter[y + 1] = winter.get(y + 1, 0.0) + v
                if sn is not None:
                    wsnow[y + 1] = wsnow.get(y + 1, 0.0) + sn
            elif md <= iarna_cutoff:
                winter[y] = winter.get(y, 0.0) + v
                if sn is not None:
                    wsnow[y] = wsnow.get(y, 0.0) + sn

        # 1–3 ianuarie: ERA5 (întârziat ~3 zile) e încă în anul trecut, deci
        # anul „curent" n-are date — raportăm anul precedent, complet
        # `winter[cy]` există deja pe 1–3 ianuarie cu doar noiembrie+decembrie
        # în el; dacă l-am lăsa să voteze, o iarnă de două luni s-ar compara cu
        # ierni istorice de cinci.
        an = cy if cy in ytd else cy - 1
        cur = ytd.get(an)
        hist_years = [y for y in sorted(ytd) if y < an]
        hist = [ytd[y] for y in hist_years]
        wcur = winter.get(an)
        whist = [winter[y] for y in range(PRECIP_START + 1, an) if y in winter]

        vals = [v for _, v, _ in pairs]
        dates = [t for t, _, _ in pairs]
        cum90, pct90, n_ref90 = _rolling90_percentile(
            dates, vals, end, int(end[:4]), window=5)

        def block(c, h):
            if c is None or not h:
                return None
            med = median(h)
            return {
                "cumul_mm": round(c, 1), "mediana_mm": round(med, 1),
                "abatere_pct": round(100 * (c - med) / med, 1) if med else None,
                "ani_mai_uscati": sum(1 for x in h if x < c), "ani": len(h),
            }

        out.append({
            "id": pid, "zona": p["name"],
            "pana_la": end,
            "reference_period": ({"requested_start": PRECIP_START,
                                  "effective_start": hist_years[0],
                                  "effective_end": hist_years[-1]}
                                 if hist_years else None),
            "ian_azi": block(cur, hist),
            "iarna": block(wcur, whist),
            # snowfall_sum de la Open-Meteo e în CENTIMETRI de zăpadă proaspătă:
            # helperul generic ar fi etichetat câmpurile `_mm`, greșit cu un
            # factor de 10 pentru orice consumator al /api/statistici.
            "zapada_iarna": _as_cm(block(
                wsnow.get(an),
                [wsnow[y] for y in range(PRECIP_START + 1, an) if y in wsnow])),
            "ultimele90": {"cumul_mm": round(cum90, 1) if cum90 is not None else None,
                           "pct": pct90,
                           "fereastra_referinta": "aceeași dată calendaristică ±5 zile",
                           "mostre_referinta": n_ref90},
        })
    return out


def full_stats():
    _track_reset()
    debit = []
    for pid in CLIM_POINTS:
        try:
            c = climatology(pid)
            debit.append({
                "name": c["name"], "km": c["km"],
                "azi_m3s": c["azi"]["value"] if c["azi"] else None,
                "data": c["azi"]["date"] if c["azi"] else None,
                "normala_zilei_m3s": c["mediana_zilei"],
                "abatere_pct": c["abatere_pct"],
                "percentila": c["azi"]["pct"] if c["azi"] else None,
                "zile_sub_p10": c["streak_sub_p10"],
                "ani_mai_mici": c["ani_mai_mici"],
                "ani_referinta": c["ani_referinta"],
                "reference_period": c.get("reference_period"),
            })
        except Exception:
            continue
    periods = [row["reference_period"] for row in debit
               if row.get("reference_period")]
    debit_period = ({
        "requested_start": CLIM_START,
        "effective_start": min(p["effective_start"] for p in periods),
        "effective_end": max(p["effective_end"] for p in periods),
    } if periods else None)
    debit_reference = (f"{debit_period['effective_start']}–{debit_period['effective_end']}"
                       if debit_period else "perioadă indisponibilă")
    precipitation = precip_stats()
    precip_periods = [row["reference_period"] for row in precipitation
                      if row.get("reference_period")]
    precip_period = ({
        "requested_start": PRECIP_START,
        "effective_start": min(p["effective_start"] for p in precip_periods),
        "effective_end": max(p["effective_end"] for p in precip_periods),
    } if precip_periods else None)
    precip_reference = (f"{precip_period['effective_start']}–{precip_period['effective_end']}"
                        if precip_period else "perioadă indisponibilă")
    return {"generat": date.today().isoformat(),
            "surse_din_cache": _tracked(),
            "intrari_din_cache": bool(_tracked()),
            "debit": debit, "precipitatii": precipitation,
            "reference_periods": {"glofas": debit_period,
                                  "era5": precip_period},
            "metoda": {
                "debit": f"GloFAS/Copernicus (model), referință efectivă {debit_reference}; "
                         "normala zilei = mediana ferestrei calendaristice ±7 zile",
                "precipitatii": f"ERA5/Copernicus (reanaliză), referință efectivă "
                                f"{precip_reference}, aceeași fereastră "
                                "calendaristică; puncte-proxy pe zone",
            }}


# --------------------------- 5. mire încrucișate: AFDJ vs DanubeSTREAM ------

def _norm_name(s):
    import unicodedata
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def crosscheck_mire():
    """Aceeași miră, raportată de două sisteme (AFDJ zilnic vs. rețeaua de
    navigație DanubeSTREAM, cvasi-orar). Diferențe mari persistente ar însemna
    că unul dintre sisteme greșește sau raportează altceva."""
    afdj = {_norm_name(s["statie"]): s
            for s in _d(C.afdj_cote(), "afdj")["statii"] if s.get("cota_cm") is not None}
    portal = _d(C.danubeportal_gauges(), "danubeportal")["mire"]
    perechi = []
    for m in portal:
        if m["tara"] != "RO":
            continue
        a = afdj.get(_norm_name(m["statie"]))
        if not a:
            continue
        perechi.append({
            "statie": m["statie"],
            "afdj_cm": a["cota_cm"], "portal_cm": m["cota_cm"],
            "diferenta_cm": round(m["cota_cm"] - a["cota_cm"], 1),
            "portal_ora_utc": m["masurat_utc"],
        })
    if not perechi:
        raise RuntimeError("nicio stație comună AFDJ/DanubeSTREAM")
    difs = sorted(abs(p["diferenta_cm"]) for p in perechi)
    perechi.sort(key=lambda p: -abs(p["diferenta_cm"]))
    return {
        "statii_comune": len(perechi),
        "mediana_abatere_cm": difs[len(difs) // 2],
        "max_abatere_cm": difs[-1],
        "top": perechi[:3],
        "metoda": "cote AFDJ (citirea de dimineață) vs. aceeași miră în rețeaua "
                  "DanubeSTREAM (cvasi-orar); diferențele mici țin de ora citirii "
                  "și de variația zilei",
    }


# ------------------------------------------ context GRDC (istoric măsurat) --

def grdc_context():
    """Așază valoarea de azi (model, Ceatal Izmail) în istoricul MĂSURAT
    GRDC de la aceeași stație — cu proveniența mixtă la vedere."""
    g = C.grdc_series()
    if not g.get("activ"):
        return g
    serie = g.pop("_serie")
    r = C.glofas_recent("ceatal_izmail", past_days=7, forecast_days=0)
    _d(r, "glofas:ceatal_izmail")
    azi = C._latest_valid(r["data"]["time"], r["data"]["discharge"])
    if not azi:
        g["nota"] = "fără valoare curentă de comparat"
        return g

    mmdd = azi[0][5:]
    ref = [v for ds, v in serie.items()
           if abs(_doy_diff(ds[5:], mmdd)) <= 7]
    pct = _rank(azi[1], ref)
    exact_zi = [(v, ds) for ds, v in serie.items() if ds[5:] == mmdd]
    rec_min = min(exact_zi, default=None)
    # Superlativul se publică numai cu numitorul lui adevărat: `mostre_referinta`
    # numără fereastra ±7 zile, un set complet diferit.
    if len(exact_zi) < 10:
        rec_min = None
    g.update({
        "azi_model_m3s": round(azi[1], 1), "azi_data": azi[0],
        "percentila_vs_masurat": pct, "mostre_referinta": len(ref),
        "record_minim_zi": ({"m3s": rec_min[0], "data": rec_min[1],
                             "ani_cu_aceasta_zi": len(exact_zi)} if rec_min else None),
        "ani_cu_aceasta_zi": len(exact_zi),
        "nota": "valoarea de azi e din model (GloFAS); istoricul e măsurat (GRDC) "
                "— comparație orientativă între produse necalibrate unul față de celălalt",
    })
    return g


def _doy_diff(mmdd_a, mmdd_b):
    dim = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    def doy(mmdd):
        m, d = int(mmdd[:2]), int(mmdd[3:])
        return sum(dim[:m - 1]) + d
    diff = doy(mmdd_a) - doy(mmdd_b)
    if diff > 182: diff -= 365
    if diff < -182: diff += 365
    return diff


# ----------------- 6/7/8. restul surselor, băgate în verificări -------------

def satellite_check():
    """Altimetrie ca probă secundară, filtrată și spațial distribuită.

    Nu numărăm stații apropiate drept surse independente și nu folosim
    observații vechi/nesigure. Semnalul rămâne context fizic, nu „vot de
    adevăr” și nici validare a debitului derivat/modelat.
    """
    h = C.hydroweb_danube()
    data = h.get("data") if isinstance(h, dict) and "data" in h else h
    if not data or not data.get("activ", True):
        raise RuntimeError(data.get("motiv", "hydroweb inactiv") if data else "hydroweb inactiv")
    eligible = [s for s in data["statii"] if s.get("eligibila_detector")]
    excluded = [s for s in data["statii"] if not s.get("eligibila_detector")]
    pcts = [s["percentila_lunii"] for s in eligible]
    segments = sorted({s.get("segment") for s in eligible if s.get("segment")})
    sufficient = len(pcts) >= 6 and len(segments) >= 3
    med = round(median(pcts), 1) if pcts else None
    sub10 = sum(1 for p in pcts if p < 10)
    return {
        "status": "shadow_coerent" if sufficient and med <= 15 else
                  "shadow_fara_semnal" if sufficient else "insuficient",
        "poate_sustine_context": sufficient,
        "statii": len(pcts), "statii_total": len(data["statii"]),
        "statii_excluse": len(excluded), "segmente": segments,
        "acoperire_km": data.get("acoperire_km"),
        "mediana_pct": med, "sub_p10": sub10,
        "excluderi": [{"statie": s.get("statie"), "km": s.get("km"),
                       "quality_flags": s.get("quality_flags") or ["date_incomplete"]}
                      for s in excluded],
        "familie_evidenta": data.get("product_family"),
        "metoda": "selecție stratificată pe cursul principal; mediana "
                  "percentilelor lunare numai pentru observații proaspete, "
                  "cu incertitudine acceptabilă și istoric suficient",
        "limita": "probă secundară din orbită; nu transformă nivelul în debit "
                  "și nu este numărată ca mai multe surse independente",
    }


def germany_check():
    """Mira măsurată german (PEGELONLINE, Hofkirchen) vs. modelul în același
    punct — a treia pereche măsurat/model, pe alt teritoriu și alt operator."""
    st = _d(C.pegelonline_stations(), "pegelonline")
    hof = next((s for s in st if "hofkirchen" in s["name"].lower() and s.get("q")), None)
    if not hof:
        raise RuntimeError("stația Hofkirchen fără debit în PEGELONLINE")
    masurat = hof["q"]["value"]
    r = C.glofas_recent("hofkirchen", past_days=7, forecast_days=0)
    _d(r, "glofas:hofkirchen")
    latest = C._latest_valid(r["data"]["time"], r["data"]["discharge"])
    if not latest or not masurat:
        raise RuntimeError("valori lipsă pentru comparație")
    raport = masurat / latest[1]
    return {"masurat_m3s": masurat, "model_m3s": round(latest[1], 1),
            "raport": round(raport, 2),
            "coerent": 0.4 <= raport <= 2.5,
            "verification_family": "gauge_de_at",
            "integrity_eligible": True,
            "metoda": "debit orar WSV la Hofkirchen vs. GloFAS în aceeași "
                      "secțiune. Se testează DOAR incompatibilitatea grosieră "
                      "(bandă 0,4–2,5): o deplasare lentă a raportului rămâne "
                      "înăuntru și nu este detectată aici."}


def serbia_check():
    """Debitele măsurate sârbești (RHMZ) vs. modelul la Novi Sad."""
    rhmz = _d(C.hidmet_report(), "rhmz")
    rs = rhmz["statii"]
    rhmz_ns = next((s for s in rs if s["statie"] == "Novi Sad"
                    and s.get("debit_m3s")), None)
    ns = rhmz_ns if rhmz.get("transport_verified") else None
    source = "RHMZ"
    family = "gauge_rs_rhmz"
    integrity_eligible = bool(ns)
    if not ns:
        # RHMZ are un lanț TLS incomplet. Preferăm livrarea HTTPS verificabilă
        # OVF/Hydroinfo; dacă lipsește, valoarea RHMZ rămâne doar context.
        try:
            hu = _d(C.hydroinfo_danube(), "hydroinfo")["statii"]
            ns = next((s for s in hu if s["statie"] == "Novi Sad"
                       and s.get("debit_m3s")), None)
        except Exception:
            ns = None
        if ns:
            source = "OVF/Hydroinfo"
            family = "gauge_hu_ovf"
            integrity_eligible = True
        elif rhmz_ns:
            ns = rhmz_ns
            source = "RHMZ (transport TLS neverificat)"
            integrity_eligible = False
    if not ns:
        raise RuntimeError("Novi Sad fără debit publicat azi în RHMZ/Hydroinfo")
    r = C.glofas_recent("novi_sad", past_days=7, forecast_days=0)
    _d(r, "glofas:novi_sad")
    latest = C._latest_valid(r["data"]["time"], r["data"]["discharge"])
    if not latest:
        raise RuntimeError("GloFAS Novi Sad indisponibil")
    raport = ns["debit_m3s"] / latest[1]
    return {"masurat_m3s": ns["debit_m3s"], "model_m3s": round(latest[1], 1),
            "raport": round(raport, 2),
            "coerent": 0.4 <= raport <= 2.5,
            "source": source,
            "verification_family": family,
            "integrity_eligible": integrity_eligible,
            "limit": (None if integrity_eligible else
                      "valoarea RHMZ este doar context: transportul TLS nu poate fi verificat"),
            "metoda": f"debit zilnic publicat pentru Novi Sad de {source} "
                      "vs. GloFAS în aceeași secțiune"}


def hungary_check():
    """Debit măsurat în Ungaria vs. model, în secțiunea Budapesta."""
    direct = None
    normalized = None
    try:
        stations = _d(C.hydroinfo_danube(), "hydroinfo")["statii"]
        direct = next((s for s in stations if s["statie"] == "Budapest"
                       and s.get("debit_m3s")), None)
    except Exception:
        pass
    try:
        stations = _d(C.danubehis_danube(), "danubehis")["statii"]
        normalized = next((s for s in stations if s["statie"] == "Budapest"
                           and s.get("debit_m3s")), None)
    except Exception:
        pass
    budapest = direct or normalized
    if not budapest:
        raise RuntimeError("Budapest fără debit în Hydroinfo și DanubeHIS")
    r = C.glofas_recent("budapesta", past_days=7, forecast_days=0)
    _d(r, "glofas:budapesta")
    latest = C._latest_valid(r["data"]["time"], r["data"]["discharge"])
    if not latest or not latest[1]:
        raise RuntimeError("GloFAS Budapesta indisponibil")
    ratio = budapest["debit_m3s"] / latest[1]
    delivery_diff = None
    if direct and normalized and direct["debit_m3s"]:
        delivery_diff = 100 * (normalized["debit_m3s"] - direct["debit_m3s"]) / direct["debit_m3s"]
    delivery_ok = delivery_diff is None or abs(delivery_diff) <= 15
    return {
        "masurat_m3s": budapest["debit_m3s"],
        "sursa_masurata": "Hydroinfo direct" if direct else "ICPDR DanubeHIS",
        "hydroinfo_m3s": direct["debit_m3s"] if direct else None,
        "danubehis_m3s": normalized["debit_m3s"] if normalized else None,
        "diferenta_livrari_pct": round(delivery_diff, 1) if delivery_diff is not None else None,
        "livrari_coerente": delivery_ok,
        "model_m3s": round(latest[1], 1),
        "raport": round(ratio, 2),
        "coerent": 0.4 <= ratio <= 2.5 and delivery_ok,
        "verification_family": "gauge_hu_ovf",
        "integrity_eligible": True,
        "data": budapest.get("data"),
        "metoda": "debitul OVF la Budapesta, livrat direct prin Hydroinfo și "
                  "normalizat prin ICPDR DanubeHIS, vs. GloFAS în aceeași "
                  "secțiune. Cele două portaluri nu sunt măsurători "
                  "independente; banda largă testează doar incompatibilități "
                  "evidente, nu validează modelul",
    }


# ------------------------- 9. Austria sub lupă (test de retenție) -----------

def austria_check():
    """Screening al tendințelor nivelurilor austriece, nu bilanț de stocare.

    Mirele VIA DONAU sunt orare și publice prin PEGELONLINE, dar fără curbe
    cotă-volum și debite intrare/ieșire nu pot demonstra ori exclude retenția.
    """
    st = _d(C.pegelonline_stations(), "pegelonline")
    at = [s for s in st if s.get("agency") == "VIA DONAU" and s.get("w")]
    if len(at) < 4:
        raise RuntimeError("prea puține mire austriece disponibile")

    trends = []
    for s in at:
        try:
            ser = _d(C.pegelonline_series(s["uuid"], "W", 30), "pegelonline")
            vals = [v for v in ser["values"] if v is not None]
            if len(vals) < 200:
                continue
            n = max(24, len(vals) // 30)  # ~prima și ultima zi
            start = sum(vals[:n]) / n
            end = sum(vals[-n:]) / n
            trends.append({"statie": s["name"], "km": s["km"],
                           "trend_cm_30z": round(end - start, 1)})
        except Exception:
            continue
    if len(trends) < 4:
        raise RuntimeError("serii insuficiente pentru mirele austriece")
    trends.sort(key=lambda t: -(t["km"] or 0))

    tvals = sorted(t["trend_cm_30z"] for t in trends)
    mediana = tvals[len(tvals) // 2]

    # intrarea dinspre Germania: debitul măsurat la Hofkirchen, aceleași 30 zile
    intrare_pct = None
    try:
        hof = next(s for s in st if "hofkirchen" in s["name"].lower())
        q = _d(C.pegelonline_series(hof["uuid"], "Q", 30), "pegelonline")
        qv = [v for v in q["values"] if v is not None]
        n = max(24, len(qv) // 30)
        q0, q1 = sum(qv[:n]) / n, sum(qv[-n:]) / n
        intrare_pct = round(100 * (q1 - q0) / q0, 1) if q0 else None
    except Exception:
        pass

    # Un fetch eșuat lăsa intrare_pct=None, iar condiția trata NECUNOSCUTUL
    # drept îndeplinit: o eroare de rețea producea un semnal apropiat de o
    # acuzație la adresa unei țări.
    retentie = (mediana > 15 and intrare_pct is not None and intrare_pct <= 0)
    stare_intrare = "necunoscuta" if intrare_pct is None else (
        "in_crestere" if intrare_pct > 0 else "stationara_sau_in_scadere")
    return {
        "statii": trends, "mediana_trend_cm": mediana,
        "intrare_trend_pct": intrare_pct,
        "suspiciune_retentie": retentie,
        "metoda": "screening: variația nivelului pe 30 de zile la mirele austriece "
                  "(VIA DONAU, orar, via PEGELONLINE) vs. debitul măsurat la "
                  "intrarea dinspre Germania (Hofkirchen). Creșteri larg "
                  "răspândite pe intrare în scădere ar merita investigate, "
                  "dar niveluri stabile nu exclud manevre: lipsesc curbele "
                  "cotă-volum și debitele de intrare/ieșire ale fiecărui baraj.",
    }


# ------------------------------------------------- bilanțul „unde e apa" ----
# Screening pentru bazinul superior (deasupra Passau): șase puncte ERA5 drept
# proxy rar pentru precipitații, debit GloFAS și un rezidual care amestecă
# stocuri, schimburi neobservate și erori. Nu este un bilanț hidrologic închis.

AREA_PASSAU_KM2 = 76650  # bazinul hidrografic al Dunării la Achleiten/Passau


def water_budget():
    _track_reset()
    cy = date.today().year

    # ploaia în bazinul superior: media a 6 puncte-proxy (câmpie + alpin),
    # cumulată de la 1 ianuarie, fără 29 februarie (ferestre identice)
    per_punct = []
    end = None
    all_points = _d(C.era5_upper_basin(PRECIP_START), "era5")
    for tag, lat, lon in C.UPPER_BASIN_POINTS:
        d = all_points[tag]
        pairs = [(t, v) for t, v in zip(d["time"], d["precip"]) if v is not None]
        if not pairs:
            continue
        end = min(end, pairs[-1][0]) if end else pairs[-1][0]
        per_punct.append(pairs)
    if len(per_punct) < 4 or not end:
        raise RuntimeError("serii ERA5 insuficiente pentru bazinul superior")
    cutoff = _mmdd(end)

    ytd_pp = []  # câte un dict {an: mm} pentru fiecare punct
    for pairs in per_punct:
        acc = {}
        for ts, v in pairs:
            md = _mmdd(ts)
            if md == "02-29" or md > cutoff:
                continue
            acc[int(ts[:4])] = acc.get(int(ts[:4]), 0.0) + v
        ytd_pp.append(acc)
    ani = set.intersection(*(set(a) for a in ytd_pp))
    ytd_mm = {y: sum(a[y] for a in ytd_pp) / len(ytd_pp) for y in ani}

    # în primele zile ale anului, ERA5 (întârziat ~3 zile) încă e în anul
    # trecut: bilanțul se raportează atunci la anul precedent, complet
    if cy not in ytd_mm and (cy - 1) in ytd_mm:
        cy = cy - 1
    p_hist = [ytd_mm[y] for y in range(PRECIP_START, cy) if y in ytd_mm]
    if cy not in ytd_mm or not p_hist:
        raise RuntimeError("serie ERA5 incompletă")
    mm_to_km3 = AREA_PASSAU_KM2 / 1e6  # 1 mm pe bazin = atâtea km³
    p_cur = ytd_mm[cy] * mm_to_km3
    p_med = median(p_hist) * mm_to_km3

    # volumul scurs prin râu până la aceeași dată (km³)
    def ytd_volume(pid):
        m = _series_map(_d(C.glofas_archive(pid, CLIM_START), f"glofas:{pid}"))
        cum, zile = {}, {}
        for ds, v in m.items():
            md = _mmdd(ds)
            if md == "02-29" or md > cutoff:
                continue
            y = int(ds[:4])
            cum[y] = cum.get(y, 0.0) + v * 86400 / 1e9
            zile[y] = zile.get(y, 0) + 1
        # _series_map elimină tăcut zilele fără valoare: anul curent ar însuma
        # ZILELE DISPONIBILE, iar mediana istorică ani compleți. Distorsiunea e
        # într-o singură direcție — orice gol mărește „apa lipsă".
        asteptate = max(zile.values()) if zile else 0
        complet = [y for y, n in zile.items() if n >= asteptate]
        hist_years = [y for y in sorted(cum) if y < cy and y in complet]
        hist = [cum[y] for y in hist_years]
        period = ({"requested_start": CLIM_START,
                   "effective_start": hist_years[0],
                   "effective_end": hist_years[-1],
                   "zile_asteptate": asteptate,
                   "zile_an_curent": zile.get(cy, 0)}
                  if hist_years else None)
        curent = cum.get(cy) if zile.get(cy, 0) >= asteptate else None
        return curent, (median(hist) if hist else None), period

    q_pas_cur, q_pas_med, q_pas_period = ytd_volume("passau")
    q_baz_cur, q_baz_med, q_baz_period = ytd_volume("bazias")
    if None in (q_pas_cur, q_pas_med, q_baz_cur, q_baz_med):
        raise RuntimeError("serii GloFAS incomplete pentru bilanț")

    # restul = ce au luat atmosfera și solul (rezidualul bilanțului)
    rest_cur = p_cur - q_pas_cur
    rest_med = p_med - q_pas_med

    grace = None
    try:
        g = C.hydroweb_gravimetry()
        data = g.get("data") if isinstance(g, dict) and "data" in g else g
        if data and data.get("activ", True) and data.get("ultima"):
            grace = {**data["ultima"],
                     "ani_mai_seci": data.get("ani_mai_seci_aceeasi_luna"),
                     "ani_comparati": data.get("ani_comparati")}
    except Exception:
        pass

    r1 = lambda x: round(x, 1)
    return {
        "surse_din_cache": _tracked(),
        "intrari_din_cache": bool(_tracked()),
        "pana_la": end,
        "bazin_superior": {
            "arie_km2": AREA_PASSAU_KM2,
            "ploaie_km3": r1(p_cur), "ploaie_normal_km3": r1(p_med),
            "rau_passau_km3": r1(q_pas_cur), "rau_normal_km3": r1(q_pas_med),
            # NU e „ce au luat atmosfera și solul": e rezidualul P−Q pe o fereastră care
            # cuprinde tot ciclul de topire, deci conține și variația stocului.
            "avertisment_nivel": (
                "Valorile ABSOLUTE în km³ depind puternic de alegerea celor 6 "
                "puncte-proxy: un test jackknife dă un interval de ~2× pe nivel "
                "(punctele alpine supraeșantionează ploaia bazinului). ANOMALIA "
                "față de mediana aceleiași ferestre e însă stabilă la ~±15%, "
                "deci abaterea procentuală e cifra de folosit, nu volumul brut."),
            "rezidual_p_minus_q_km3": r1(rest_cur),
            "rezidual_p_minus_q_normal_km3": r1(rest_med),
        },
        "bazias": {
            "volum_km3": r1(q_baz_cur), "normal_km3": r1(q_baz_med),
            # Deficit față de mediana ACELUIAȘI model, nu apă care lipsește de undeva.
            "deficit_fata_de_normala_km3": r1(q_baz_med - q_baz_cur),
        },
        "grace": grace,
        "reference_periods": {
            "precipitation_era5": {
                "requested_start": PRECIP_START,
                "effective_start": min(y for y in ytd_mm if y < cy),
                "effective_end": max(y for y in ytd_mm if y < cy),
            },
            "glofas_passau": q_pas_period,
            "glofas_bazias": q_baz_period,
        },
        "consum_uman_nota": "captările, transferurile și variația stocurilor "
                            "nu sunt cuantificate separat în datele publice "
                            "folosite aici",
        "metoda": "ploaie: ERA5, media a 6 puncte-proxy distribuite "
                  "(câmpie+alpin) × aria bazinului la Achleiten; râu: GloFAS "
                  "cumulat de la 1 ianuarie; normal = mediana exact acelorași "
                  "ferestre calendaristice (fără 29 feb); perioadele efective "
                  "sunt raportate separat pentru fiecare serie; rezidual P−Q = evapotranspirație + variația "
                  "stocurilor + schimburi neobservate + eroarea proxy/model",
    }


# ----------------------------------------------------------------- raport --

def _sev_clim(c):
    if not c.get("azi") or c["azi"]["pct"] is None:
        return "info"
    p = c["azi"]["pct"]
    if p < 2 or p > 98:
        return "extrem"
    if p < 10 or p > 90:
        return "sever"
    if p < 25 or p > 75:
        return "atentie"
    return "normal"


def report():
    _track_reset()
    rep = {"generat": date.today().isoformat(), "climatologie": [],
           "bilant": None, "masurat_vs_model": None, "precipitatii": None,
           "erori": {}}

    for pid in CLIM_POINTS:
        try:
            c = climatology(pid)
            c["severitate"] = _sev_clim(c)
            if pid in CLIM_NOTES:
                c["nota"] = CLIM_NOTES[pid]
            rep["climatologie"].append(c)
        except Exception as exc:
            rep["erori"][f"clim:{pid}"] = C.public_error(exc)
    rep["climatologie"].sort(key=lambda c: -(c["km"] or 0))

    try:
        rep["bilant"] = balance()
    except Exception as exc:
        rep["erori"]["bilant"] = C.public_error(exc)

    try:
        rep["mire_crosscheck"] = crosscheck_mire()
    except Exception as exc:
        rep["erori"]["mire_crosscheck"] = C.public_error(exc)

    for key, fn in (("satelit", satellite_check),
                    ("germania", germany_check),
                    ("ungaria", hungary_check),
                    ("serbia", serbia_check),
                    ("austria", austria_check)):
        try:
            rep[key] = fn()
        except Exception as exc:
            rep["erori"][key] = C.public_error(exc)

    try:
        rep["masurat_vs_model"] = measured_vs_model()
    except Exception as exc:
        rep["erori"]["masurat_vs_model"] = C.public_error(exc)

    try:
        baz = next((c for c in rep["climatologie"] if c["id"] == "bazias"), None)
        dp = baz["azi"]["pct"] if baz and baz.get("azi") else None
        rep["precipitatii"] = precip_coherence(dp)
    except Exception as exc:
        rep["erori"]["precipitatii"] = C.public_error(exc)

    # Ce intrări au venit din cache expirat. Fără asta, `stale: false` pe
    # răspuns însemna doar „raportul e recalculat recent", nu „datele sunt
    # proaspete" — două lucruri diferite, iar al doilea e cel citit de om.
    rep["surse_din_cache"] = _tracked()
    rep["intrari_din_cache"] = bool(rep["surse_din_cache"])
    return rep
