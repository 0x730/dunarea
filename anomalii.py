"""Detectoare de anomalii pe datele disponibile.

Fiecare detector întoarce un verdict cu metoda și limitele lui — scopul e
separarea onestă între „anormal față de istoric, dar explicabil" (secetă)
și „nu se leagă în bilanț" (de investigat). Nicio acuzație automată:
detectoarele produc cifre verificabile, nu concluzii.
"""

from datetime import date, timedelta
from statistics import mean, median, pstdev

import connectors as C

CLIM_START = 1991      # referința climatologică
BAL_START = 2015       # referința pentru bilanțul Baziaș→Gruia


# ------------------------------------------------------------- utilitare ---

def _series_map(arch):
    """arhivă GloFAS → dict {data_iso: valoare}, fără None."""
    return {t: v for t, v in zip(arch["time"], arch["discharge"]) if v is not None}


def _mmdd(ds):
    return ds[5:]


def _rank(value, ref):
    """percentila empirică a valorii în distribuția de referință (0–100)."""
    if not ref:
        return None
    below = sum(1 for r in ref if r <= value)
    return round(100.0 * below / len(ref), 1)


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


# ------------------------------------------------- 1. climatologie puncte ---

# transect complet: dacă seceta „vine pe apă", percentilele joase încep amonte
CLIM_POINTS = ["regensburg", "passau", "linz", "viena", "bratislava",
               "budapesta", "mohacs", "novi_sad",
               "bazias", "gruia", "zimnicea", "cernavoda", "braila",
               "ceatal_izmail"]

# context special pe secțiuni — afișat în celula respectivă
CLIM_NOTES = {
    "cernavoda": ("CNE Cernavodă — apa de răcire vine din Dunăre; la ape foarte "
                  "scăzute funcționarea se limitează (precedent: vara 2003). "
                  "Atenție: nivelul local e influențat și de lucrările oficiale "
                  "de la brațul Bala (vezi buletinul INHGA)."),
}


def climatology(point_id):
    arch = C.glofas_archive(point_id, CLIM_START)["data"]
    smap = _series_map(arch)
    if not smap:
        raise RuntimeError("serie goală")
    cur_year = date.today().year
    exact, ref = _doy_reference(smap, cur_year)

    # ultimele 45 de zile calendaristice, indiferent de anul lor — altfel
    # seria „zile sub P10" s-ar reseta artificial pe 1 ianuarie
    azi = date.today().isoformat()
    days = sorted(d for d in smap if d <= azi)[-45:]
    recent = []
    for ds in days:
        r = _rank(smap[ds], ref.get(_mmdd(ds), []))
        recent.append({"date": ds, "value": round(smap[ds], 1), "pct": r})

    streak = 0
    for item in reversed(recent):
        if item["pct"] is not None and item["pct"] < 10:
            streak += 1
        else:
            break

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

    p = C.GLOFAS_POINTS[point_id]
    return {
        "id": point_id, "name": p["name"], "km": p["km"],
        "azi": last, "streak_sub_p10": streak,
        "ani_mai_mici": ani_mai_mici, "ani_referinta": n_ani,
        "mediana_zilei": mediana_zilei, "abatere_pct": abatere_pct,
        "recent": recent,
    }


# ------------------------------------- 2. bilanțul Baziaș→Gruia (cu lag) ---

def balance():
    b = _series_map(C.glofas_archive("bazias", BAL_START)["data"])
    g = _series_map(C.glofas_archive("gruia", BAL_START)["data"])
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
    hist = [v for ds, v in rel.items()
            if int(ds[5:7]) == cur_month and not ds.startswith(str(cur_year))]
    last14 = [rel[ds] for ds in sorted(rel)[-14:]]
    if not hist or not last14:
        raise RuntimeError("date insuficiente pentru reziduu")

    mu, sd = mean(hist), pstdev(hist)
    cur = mean(last14)
    z = (cur - mu) / sd if sd > 0 else 0.0

    return {
        "lag_zile": best_lag, "corelatie": round(best_c, 3),
        "reziduu_curent_pct": round(100 * cur, 2),
        "reziduu_istoric_pct": round(100 * mu, 2),
        "sd_pct": round(100 * sd, 2),
        "z": round(z, 2),
        "fereastra": "media ultimelor 14 zile vs. aceeași lună, 2015–anul trecut",
    }


# ------------------------------------ 3. măsurat (INHGA) vs model (GloFAS) --

def measured_vs_model():
    official = C.inhga_series(days=90)
    if len(official) < 10:
        return {"insuficient": True,
                "n": len(official),
                "nota": "arhiva buletinelor INHGA încă se descarcă în fundal — "
                        "revino în câteva minute"}
    m = _series_map(C.glofas_archive("bazias", date.today().year - 1)["data"])
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
    mu, sd = mean(rs), pstdev(rs)
    last7 = mean(rs[-7:])
    z = (last7 - mu) / sd if sd > 0 else 0.0
    return {
        "n": len(ratios), "raport_mediu": round(mu, 3), "sd": round(sd, 3),
        "raport_ultimele7": round(last7, 3), "z": round(z, 2),
        "serie": ratios[-30:],
    }


# ------------------------------------- 4. coerența precipitații ↔ debit ----

PRECIP_VS = [("bazin_superior", "amonte: Germania/Austria"),
             ("bazin_mijlociu", "amonte: Ungaria")]


def precip_coherence(discharge_pct):
    """Percentila cumulului de precipitații pe 90 de zile vs. istoric
    (aceeași fereastră calendaristică din anii anteriori)."""
    out = []
    today = date.today()
    for pid, label in PRECIP_VS:
        try:
            d = C.era5_precip(pid, 2000)["data"]
            smap = {t: v for t, v in zip(d["time"], d["precip"]) if v is not None}
            dates = sorted(smap)
            end = dates[-1]
            cum90 = {}
            vals = [smap[ds] for ds in dates]
            for i in range(90, len(dates)):
                cum90[dates[i]] = sum(vals[i - 89:i + 1])
            cur = cum90.get(end)
            if cur is None:
                continue
            ref = [v for ds, v in cum90.items()
                   if _mmdd(ds) == _mmdd(end) and ds != end]
            # fereastra calendaristică ±5 zile pentru mai multe mostre
            # fereastra ±5 zile trebuie să se învârtă peste Anul Nou
            refw = [v for ds, v in cum90.items()
                    if abs(_doy_diff(_mmdd(ds), _mmdd(end))) <= 5
                    and not ds.startswith(str(today.year))]
            pct = _rank(cur, refw or ref)
            out.append({"zona": C.PRECIP_POINTS[pid]["name"], "eticheta": label,
                        "cum90_mm": round(cur, 1), "pct": pct,
                        "pana_la": end})
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
    for pid, p in C.PRECIP_POINTS.items():
        try:
            d = C.era5_precip(pid, PRECIP_START)["data"]
        except Exception:
            continue
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

        cur = ytd.get(cy)
        hist = [ytd[y] for y in range(PRECIP_START, cy) if y in ytd]
        wcur = winter.get(cy)
        whist = [winter[y] for y in range(PRECIP_START + 1, cy) if y in winter]

        vals = [v for _, v, _ in pairs]
        cum90 = sum(vals[-90:])
        ref90 = []
        dates = [t for t, _, _ in pairs]
        idx = {t: i for i, t in enumerate(dates)}
        for y in range(PRECIP_START, cy):
            key = f"{y}-{cutoff}"
            i = idx.get(key)
            if i is not None and i >= 90:
                ref90.append(sum(vals[i - 89:i + 1]))

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
            "ian_azi": block(cur, hist),
            "iarna": block(wcur, whist),
            "zapada_iarna": block(wsnow.get(cy),
                                  [wsnow[y] for y in range(PRECIP_START + 1, cy)
                                   if y in wsnow]),
            "ultimele90": {"cumul_mm": round(cum90, 1),
                           "pct": _rank(cum90, ref90)},
        })
    return out


def full_stats():
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
            })
        except Exception:
            continue
    return {"generat": date.today().isoformat(),
            "debit": debit, "precipitatii": precip_stats(),
            "metoda": {
                "debit": "GloFAS/Copernicus (model), referință 1991–anul trecut; "
                         "normala zilei = mediana ferestrei calendaristice ±7 zile",
                "precipitatii": f"ERA5/Copernicus (reanaliză), referință "
                                f"{PRECIP_START}–anul trecut, aceeași fereastră "
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
            for s in C.afdj_cote()["data"]["statii"] if s.get("cota_cm") is not None}
    portal = C.danubeportal_gauges()["data"]["mire"]
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


# ------------------------------------------ context GRDC (istoric secular) --

def grdc_context():
    """Așază valoarea de azi (model, Ceatal Izmail) în istoricul MĂSURAT
    secular de la GRDC — cu eticheta de proveniență mixtă la vedere."""
    g = C.grdc_series()
    if not g.get("activ"):
        return g
    serie = g.pop("_serie")
    r = C.glofas_recent("ceatal_izmail", past_days=7, forecast_days=0)
    azi = C._latest_valid(r["data"]["time"], r["data"]["discharge"])
    if not azi:
        g["nota"] = "fără valoare curentă de comparat"
        return g

    mmdd = azi[0][5:]
    ref = [v for ds, v in serie.items()
           if abs(_doy_diff(ds[5:], mmdd)) <= 7]
    pct = _rank(azi[1], ref)
    rec_min = min(((v, ds) for ds, v in serie.items() if ds[5:] == mmdd),
                  default=None)
    g.update({
        "azi_model_m3s": round(azi[1], 1), "azi_data": azi[0],
        "percentila_vs_masurat": pct, "mostre_referinta": len(ref),
        "record_minim_zi": {"m3s": rec_min[0], "data": rec_min[1]} if rec_min else None,
        "nota": "valoarea de azi e din model (GloFAS); istoricul e măsurat (GRDC) "
                "— comparație orientativă, bias-ul model/măsurat e ~0,7–0,8",
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
    """Satelitul (hydroweb) vs. râul: percentilele altimetrice ar trebui să
    spună aceeași poveste ca percentilele de debit."""
    h = C.hydroweb_danube()
    data = h.get("data") if isinstance(h, dict) and "data" in h else h
    if not data or not data.get("activ", True):
        raise RuntimeError(data.get("motiv", "hydroweb inactiv") if data else "hydroweb inactiv")
    pcts = [s["percentila_lunii"] for s in data["statii"]
            if s.get("percentila_lunii") is not None]
    if len(pcts) < 3:
        raise RuntimeError("prea puține stații satelitare cu percentile")
    pcts.sort()
    med = pcts[len(pcts) // 2]
    sub10 = sum(1 for p in pcts if p < 10)
    return {"statii": len(pcts), "mediana_pct": med, "sub_p10": sub10,
            "metoda": "percentila lunară a fiecărei stații virtuale în propria "
                      "serie satelitară (hydroweb.next/CNES)"}


def germany_check():
    """Mira măsurată german (PEGELONLINE, Hofkirchen) vs. modelul în același
    punct — a treia pereche măsurat/model, pe alt teritoriu și alt operator."""
    st = C.pegelonline_stations()["data"]
    hof = next((s for s in st if "hofkirchen" in s["name"].lower() and s.get("q")), None)
    if not hof:
        raise RuntimeError("stația Hofkirchen fără debit în PEGELONLINE")
    masurat = hof["q"]["value"]
    r = C.glofas_recent("hofkirchen", past_days=7, forecast_days=0)
    latest = C._latest_valid(r["data"]["time"], r["data"]["discharge"])
    if not latest or not masurat:
        raise RuntimeError("valori lipsă pentru comparație")
    raport = masurat / latest[1]
    return {"masurat_m3s": masurat, "model_m3s": round(latest[1], 1),
            "raport": round(raport, 2),
            "coerent": 0.4 <= raport <= 2.5,
            "metoda": "debit orar WSV la Hofkirchen vs. GloFAS în aceeași "
                      "secțiune; bias-ul stabil de model e normal, ruptura nu"}


def serbia_check():
    """Debitele măsurate sârbești (RHMZ) vs. modelul la Novi Sad."""
    rs = C.hidmet_report()["data"]["statii"]
    ns = next((s for s in rs if s["statie"] == "Novi Sad" and s.get("debit_m3s")), None)
    if not ns:
        raise RuntimeError("Novi Sad fără debit publicat azi")
    r = C.glofas_recent("novi_sad", past_days=7, forecast_days=0)
    latest = C._latest_valid(r["data"]["time"], r["data"]["discharge"])
    if not latest:
        raise RuntimeError("GloFAS Novi Sad indisponibil")
    raport = ns["debit_m3s"] / latest[1]
    return {"masurat_m3s": ns["debit_m3s"], "model_m3s": round(latest[1], 1),
            "raport": round(raport, 2),
            "coerent": 0.4 <= raport <= 2.5,
            "metoda": "debit zilnic RHMZ la Novi Sad vs. GloFAS în aceeași "
                      "secțiune — a patra pereche măsurat/model, alt stat"}


# ------------------------- 9. Austria sub lupă (test de retenție) -----------

def austria_check():
    """Testul direct al ipotezei „Austria stochează apa": dacă barajele
    austriece ar reține apă, mirele din lacurile lor de fir ar CREȘTE în timp
    ce intrarea dinspre Germania scade. Mirele VIA DONAU sunt orare, publice,
    prin PEGELONLINE."""
    st = C.pegelonline_stations()["data"]
    at = [s for s in st if s.get("agency") == "VIA DONAU" and s.get("w")]
    if len(at) < 4:
        raise RuntimeError("prea puține mire austriece disponibile")

    trends = []
    for s in at:
        try:
            ser = C.pegelonline_series(s["uuid"], "W", 30)["data"]
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
        q = C.pegelonline_series(hof["uuid"], "Q", 30)["data"]
        qv = [v for v in q["values"] if v is not None]
        n = max(24, len(qv) // 30)
        q0, q1 = sum(qv[:n]) / n, sum(qv[-n:]) / n
        intrare_pct = round(100 * (q1 - q0) / q0, 1) if q0 else None
    except Exception:
        pass

    retentie = mediana > 15 and (intrare_pct is None or intrare_pct <= 0)
    return {
        "statii": trends, "mediana_trend_cm": mediana,
        "intrare_trend_pct": intrare_pct,
        "suspiciune_retentie": retentie,
        "metoda": "variația nivelului pe 30 de zile la toate mirele austriece "
                  "(VIA DONAU, orar, via PEGELONLINE) vs. debitul măsurat la "
                  "intrarea dinspre Germania (Hofkirchen). Retenția ar apărea "
                  "ca niveluri în creștere în lacurile de fir austriece pe "
                  "fond de intrare stabilă/în scădere. Capacitatea fizică de "
                  "stocare pe firul apei rămâne oricum de ordinul orelor–zilelor. "
                  "Atenție la iluzia din imagini/webcam-uri: un lac de fir e "
                  "ținut la aceeași cotă-țintă tot anul, pentru navigație — "
                  "Dunărea «arată plină» la Viena și la 800, și la 2.000 m³/s; "
                  "nivelul menținut nu înseamnă apă acumulată, contează debitul "
                  "care trece și trendul, exact ce se măsoară aici.",
    }


# ------------------------------------------------- bilanțul „unde e apa" ----
# Contabilitatea apei pentru bazinul superior (deasupra Passau), unde punctul
# ERA5 e un proxy defensabil: ce a plouat, ce a curs prin râu, restul =
# atmosferă + sol. Plus volumul trecut la Baziaș față de un an normal.

AREA_PASSAU_KM2 = 76650  # bazinul hidrografic al Dunării la Achleiten/Passau


def water_budget():
    cy = date.today().year

    # ploaia în bazinul superior: media a 6 puncte-proxy (câmpie + alpin),
    # cumulată de la 1 ianuarie, fără 29 februarie (ferestre identice)
    per_punct = []
    end = None
    for tag, lat, lon in C.UPPER_BASIN_POINTS:
        d = C.era5_point(tag, lat, lon, PRECIP_START)["data"]
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
        m = _series_map(C.glofas_archive(pid, CLIM_START)["data"])
        cum = {}
        for ds, v in m.items():
            md = _mmdd(ds)
            if md == "02-29" or md > cutoff:
                continue
            y = int(ds[:4])
            cum[y] = cum.get(y, 0.0) + v * 86400 / 1e9
        hist = [cum[y] for y in range(CLIM_START, cy) if y in cum]
        return cum.get(cy), (median(hist) if hist else None)

    q_pas_cur, q_pas_med = ytd_volume("passau")
    q_baz_cur, q_baz_med = ytd_volume("bazias")
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
        "pana_la": end,
        "bazin_superior": {
            "arie_km2": AREA_PASSAU_KM2,
            "ploaie_km3": r1(p_cur), "ploaie_normal_km3": r1(p_med),
            "rau_passau_km3": r1(q_pas_cur), "rau_normal_km3": r1(q_pas_med),
            "atmosfera_sol_km3": r1(rest_cur), "atmosfera_sol_normal_km3": r1(rest_med),
        },
        "bazias": {
            "volum_km3": r1(q_baz_cur), "normal_km3": r1(q_baz_med),
            "lipsa_km3": r1(q_baz_med - q_baz_cur),
        },
        "grace": grace,
        "consum_uman_nota": "consumul uman net al întregului bazin este de "
                            "ordinul câtorva km³/an (referință EEA) — cu două "
                            "ordine de mărime sub rândurile de mai sus",
        "metoda": "ploaie: ERA5, media a 6 puncte-proxy distribuite "
                  "(câmpie+alpin) × aria bazinului la Achleiten; râu: GloFAS "
                  "cumulat de la 1 ianuarie; normal = mediana exact acelorași "
                  "ferestre calendaristice (fără 29 feb), 1991/2000–anul "
                  "trecut; atmosferă+sol = rezidualul P−Q",
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
            rep["erori"][f"clim:{pid}"] = str(exc)
    rep["climatologie"].sort(key=lambda c: -(c["km"] or 0))

    try:
        rep["bilant"] = balance()
    except Exception as exc:
        rep["erori"]["bilant"] = str(exc)

    try:
        rep["mire_crosscheck"] = crosscheck_mire()
    except Exception as exc:
        rep["erori"]["mire_crosscheck"] = str(exc)

    for key, fn in (("satelit", satellite_check),
                    ("germania", germany_check),
                    ("serbia", serbia_check),
                    ("austria", austria_check)):
        try:
            rep[key] = fn()
        except Exception as exc:
            rep["erori"][key] = str(exc)

    try:
        rep["masurat_vs_model"] = measured_vs_model()
    except Exception as exc:
        rep["erori"]["masurat_vs_model"] = str(exc)

    try:
        baz = next((c for c in rep["climatologie"] if c["id"] == "bazias"), None)
        dp = baz["azi"]["pct"] if baz and baz.get("azi") else None
        rep["precipitatii"] = precip_coherence(dp)
    except Exception as exc:
        rep["erori"]["precipitatii"] = str(exc)

    return rep
