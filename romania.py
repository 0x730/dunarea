"""Analiză deterministă de proporționalitate pentru România și Cernavodă.

Separă fenomenul hidrologic, consecința operațională și criticitatea SEN.
Nu încearcă să demonstreze o narațiune; fiecare concluzie se schimbă odată cu
intrările și revine la „date insuficiente” când lipsește veriga necesară.
"""

from datetime import date


MODEL_START_YEAR = 1991
ROMANIA_PRECIP_IDS = ("oltenia", "muntenia", "moldova_sud", "delta")
REFERENCE_YEARS = (2003, 2011, 2015, 2022, 2025)

SNN_2011_LOW_WATER_URL = (
    "https://nuclearelectrica.ro/snn/2011/09/15/"
    "comunicat-de-presa-referitor-la-nivelul-dunarii/")
SNN_2015_LOW_WATER_URL = (
    "https://nuclearelectrica.ro/snn/en/2015/07/22/"
    "the-operation-of-cernavoda-npp-within-normal-parameters-despite-the-decrease-of-the-level-of-the-danube-river/")
SNN_2022_ANNUAL_REPORT_URL = (
    "https://nuclearelectrica.ro/ir/wp-content/uploads/sites/9/2023/03/"
    "SNN_RO_Raport-Anual-CA-2022.pdf")


HISTORICAL_CNE_OPERATIONS = (
    {
        "year": 2003,
        "reference_date": "retrospectivă SNN; data exactă nu este dată în comunicatul folosit",
        "hydrology": "ape foarte scăzute; cazul este confirmat retrospectiv de SNN",
        "plant_action": "Unitatea 1 a fost oprită preventiv",
        "classification": "water_shutdown",
        "interpretation": "precedent confirmat de oprire pentru nivelul scăzut al Dunării",
        "source": {"label": "SNN — retrospectiva publicată în 2011",
                   "url": SNN_2011_LOW_WATER_URL},
        "source_scope": "contextul și oprirea preventivă, relatate retrospectiv",
    },
    {
        "year": 2011,
        "reference_date": "2011-09-15",
        "hydrology": "bazin de aspirație 3,00 mdMB; SNN îl compara cu situația din 2003",
        "plant_action": "ambele unități funcționau normal la data comunicatului",
        "classification": "operating",
        "interpretation": "o situație comparabilă nu a produs automat aceeași decizie operațională",
        "source": {"label": "SNN — comunicat 15.09.2011",
                   "url": SNN_2011_LOW_WATER_URL},
        "source_scope": "nivelul bazinului, pragurile istorice și starea unităților",
    },
    {
        "year": 2015,
        "reference_date": "2015-07-22",
        "hydrology": "nivel scăzut al Dunării, urmărit prin prognoza operatorului",
        "plant_action": "ambele unități funcționau normal la data comunicatului",
        "classification": "operating",
        "interpretation": "operatorul nu anticipa atunci atingerea nivelurilor de oprire",
        "source": {"label": "SNN — comunicat 22.07.2015",
                   "url": SNN_2015_LOW_WATER_URL},
        "source_scope": "nivelul scăzut și funcționarea unităților la data comunicatului",
    },
    {
        "year": 2022,
        "reference_date": "2022-08-26–31",
        "hydrology": "an foarte jos în comparația GloFAS folosită de monitor",
        "plant_action": "U1 oprită controlat 26–31 august pentru repararea sistemului de filtrare din bazinul de aspirație",
        "classification": "other_cause",
        "interpretation": "raportul oficial nu clasifică oprirea drept atingere a unui prag de apă scăzută",
        "source": {"label": "SNN — Raport anual 2022",
                   "url": SNN_2022_ANNUAL_REPORT_URL},
        "source_scope": "acțiunea și cauza oficială; contextul hidrologic este GloFAS din monitor",
    },
)


HISTORICAL_2011_THRESHOLDS = {
    "published": "2011-09-15",
    "source": {"label": "SNN — comunicat privind nivelul Dunării",
               "url": SNN_2011_LOW_WATER_URL},
    "intake_basin_level_mdmb": 3.0,
    "usual_level_mdmb": {"min": 4.5, "max": 5.0},
    "shutdown_levels_mdmb": [
        {"scope": "oprirea unei unități", "value": 2.50},
        {"scope": "oprirea celei de-a doua unități", "value": 2.35},
    ],
    "special_cooling_pumps_around_mdmb": 1.4,
    "validity": ("reper istoric publicat în 2011; nu este prezentat drept limită "
                 "operațională valabilă în 2026"),
}


def _number(value):
    return value if isinstance(value, (int, float)) else None


def _series_map(archive):
    return {
        stamp: value for stamp, value in zip(
            archive.get("time") or [], archive.get("discharge") or [])
        if _number(value) is not None
    }


def historical_cernavoda(archive, as_of):
    """Comparație echitabilă pe aceeași zi și aceeași fereastră sezonieră.

    „Minimul verii până la data curentă” folosește exact aceeași limită
    calendaristică pentru fiecare an. Pentru anii încheiați adăugăm separat
    minimul întregii luni august; anul curent rămâne explicit parțial.
    """
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    values = _series_map(archive)
    month_day = as_of.strftime("%m-%d")
    exact = []
    years = sorted({int(stamp[:4]) for stamp in values})
    for year in years:
        value = values.get(f"{year:04d}-{month_day}")
        if value is not None:
            exact.append({"year": year, "value_m3s": round(value, 1)})

    current = next((row for row in exact if row["year"] == as_of.year), None)
    ordered = sorted(exact, key=lambda row: row["value_m3s"])
    rank = (next((i for i, row in enumerate(ordered, 1)
                  if row["year"] == as_of.year), None) if current else None)
    lower = [row for row in ordered if current and row["value_m3s"] < current["value_m3s"]]

    start_md = "06-01" if month_day >= "06-01" else "01-01"
    rows = []
    selected = set(REFERENCE_YEARS) | {as_of.year} | {row["year"] for row in lower}
    for year in sorted(selected):
        if year not in years:
            continue
        same_day = values.get(f"{year:04d}-{month_day}")
        to_date = [(stamp, value) for stamp, value in values.items()
                   if stamp.startswith(f"{year:04d}-")
                   and start_md <= stamp[5:] <= month_day]
        august = [(stamp, value) for stamp, value in values.items()
                  if stamp.startswith(f"{year:04d}-08-")
                  and (year < as_of.year or stamp <= as_of.isoformat())]

        def minimum(items):
            if not items:
                return None
            stamp, value = min(items, key=lambda item: item[1])
            return {"date": stamp, "value_m3s": round(value, 1)}

        rows.append({
            "year": year,
            "same_day_m3s": round(same_day, 1) if same_day is not None else None,
            "summer_to_date_min": minimum(to_date),
            "august_min": minimum(august),
            "august_partial": year == as_of.year,
        })

    return {
        "as_of": as_of.isoformat(),
        "same_calendar_day": month_day,
        "current_m3s": current["value_m3s"] if current else None,
        "rank_low_to_high": rank,
        "years_compared": len(exact),
        "lower_years": lower,
        "rows": rows,
        "method": ("GloFAS/Copernicus, aceeași celulă și aceeași metodă în toți anii; "
                   "rangul nu este record hidrometric și nu măsoară bazinul de aspirație CNE"),
    }


def _find_station(afdj, needle):
    needle = needle.lower()
    return next((station for station in (afdj or {}).get("statii", [])
                 if needle in (station.get("statie") or "").lower()), None)


def _claim(key, label, status, conclusion, evidence, limit):
    return {"key": key, "label": label, "status": status,
            "conclusion": conclusion, "evidence": evidence, "limit": limit}


def _operational_history(as_of, snn_status, snn_stale, cern, cern_gauge,
                         measured, monthly_mean):
    """Leagă anii hidrologici de acțiunea publicată de operator.

    Intrările istorice sunt constatări datate, nu stări curente. Ultimul rând
    este construit exclusiv din raportul SNN curent acceptat de conector.
    """
    rows = [dict(row) for row in HISTORICAL_CNE_OPERATIONS]
    report = snn_status.get("latest_report") or {}
    source = ({"label": report.get("title") or "SNN — raport operațional curent",
               "url": report.get("url")} if report.get("url") else None)
    u1 = snn_status.get("u1")
    u2 = snn_status.get("u2")
    accepted = (snn_status.get("status_available")
                and snn_status.get("status_fresh")
                and not snn_status.get("needs_review")
                and not snn_stale)
    stopped_for_water = (accepted and snn_status.get("water_related")
                         and "oprit" in (u1 or "").lower())

    if stopped_for_water:
        classification = "current_water_shutdown"
        plant_action = f"U1: {u1}; U2: {u2 or 'stare neprecizată'}"
        interpretation = ("oprirea U1 asociată apei este confirmată de operator; "
                          "valorile tehnice care au declanșat decizia nu sunt publicate")
    elif accepted and (u1 or u2):
        classification = "current_official"
        plant_action = "; ".join(part for part in (
            f"U1: {u1}" if u1 else None, f"U2: {u2}" if u2 else None) if part)
        interpretation = "starea curentă este cea publicată de operator; cauza nu este dedusă din debit"
    else:
        classification = "unknown"
        plant_action = "stare curentă neverificabilă dintr-un raport acceptat și proaspăt"
        interpretation = "nu se păstrează ca fapt o stare veche sau un raport nou încă nerevizuit"

    current_hydrology = []
    if _number(cern.get("percentila")) is not None:
        current_hydrology.append(f"GloFAS Cernavodă P{cern['percentila']}")
    if _number((cern_gauge or {}).get("cota_cm")) is not None:
        current_hydrology.append(f"miră AFDJ {cern_gauge['cota_cm']} cm")
    if measured is not None:
        bazias = f"Baziaș {measured:g} m³/s"
        if monthly_mean:
            bazias += f" ({100 * measured / monthly_mean:.1f}% din media lunii)"
        current_hydrology.append(bazias)

    rows.append({
        "year": as_of.year,
        "current": True,
        "reference_date": snn_status.get("date") or as_of.isoformat(),
        "hydrology": ("; ".join(current_hydrology) if current_hydrology else
                      "valorile hidrologice curente nu sunt disponibile"),
        "plant_action": plant_action,
        "classification": classification,
        "interpretation": interpretation,
        "source": source,
        "source_scope": "starea CNE; contextul hidrologic vine din fluxurile curente ale monitorului",
    })
    return rows


def _parameter_transparency(cern, cern_gauge, measured, monthly_mean,
                            nuclear, snn_status):
    """Separă proxy-urile publice de mărimile necesare deciziei CNE."""
    intake_level = _number(snn_status.get("intake_basin_level_mdmb"))
    current_thresholds = snn_status.get("current_operating_thresholds_mdmb")
    thresholds_verified = isinstance(current_thresholds, dict) and bool(current_thresholds)
    decision_reproducible = intake_level is not None and thresholds_verified
    measured_share = (round(100 * measured / monthly_mean, 1)
                      if measured is not None and monthly_mean else None)

    public_signals = [
        {
            "key": "bazias",
            "label": "debit Baziaș măsurat",
            "value": measured,
            "unit": "m³/s",
            "context": (f"{measured_share}% din media lunii" if measured_share is not None
                        else "reper lunar indisponibil"),
            "what_it_proves": "stresul hidrologic la intrarea în România, nu pragul prizei CNE",
        },
        {
            "key": "cernavoda_gauge",
            "label": "cotă miră AFDJ Cernavodă",
            "value": _number((cern_gauge or {}).get("cota_cm")),
            "unit": "cm",
            "context": (cern_gauge or {}).get("actualizat") or "dată indisponibilă",
            "what_it_proves": "nivelul la mira de navigație; nu se convertește aici în mdMB-ul bazinului",
        },
        {
            "key": "cernavoda_model",
            "label": "GloFAS Cernavodă",
            "value": _number(cern.get("azi_m3s")),
            "unit": "m³/s",
            "context": (f"P{cern.get('percentila')}" if cern.get("percentila") is not None
                        else "percentilă indisponibilă"),
            "what_it_proves": "raritatea în model, nu nivelul sau debitul captat de centrală",
        },
        {
            "key": "sen_nuclear",
            "label": "producție nucleară SEN",
            "value": nuclear,
            "unit": "MW",
            "context": "agregat Transelectrica",
            "what_it_proves": "consecința energetică, nu cauza tehnică a opririi",
        },
    ]

    parameters = [
        {
            "name": "nivelul bazinului de aspirație, în mdMB",
            "status": "available" if intake_level is not None else "missing",
            "current_value": intake_level,
            "unit": "mdMB",
            "basis": "publicat explicit de SNN în 2011; valoarea curentă nu apare în raportul ingerat",
            "kind": "parametru publicat istoric de operator",
        },
        {
            "name": "pragurile operaționale curente, separat pe unități",
            "status": "available" if thresholds_verified else "missing",
            "current_value": current_thresholds if thresholds_verified else None,
            "unit": "mdMB",
            "basis": "valorile din 2011 sunt numai reper istoric și nu sunt presupuse valabile astăzi",
            "kind": "necesar pentru reproducerea deciziei",
        },
        {
            "name": "debitul de răcire și pierderile prin grătare/filtre",
            "status": "missing",
            "current_value": None,
            "unit": None,
            "basis": "variabile hidraulice de verificat; comunicările 2026 nu le enumeră și nu le cuantifică",
            "kind": "inferență inginerească, nu valoare declarată de SNN pentru 2026",
        },
        {
            "name": "marja de aspirație a pompelor și starea echipamentelor",
            "status": "missing",
            "current_value": None,
            "unit": None,
            "basis": "SNN menționează evaluarea parametrilor și echipamentelor, fără valori publice",
            "kind": "categorie generală confirmată; detaliile tehnice rămân nepublice",
        },
    ]

    if decision_reproducible:
        verdict = ("Nivelul bazinului și pragurile curente sunt disponibile; relația cu decizia "
                   "poate fi verificată pe aceeași cotă de referință.")
    else:
        verdict = ("Datele publice confirmă presiunea hidrologică și consecința operațională, "
                   "dar nu permit reproducerea independentă a pragului care a determinat decizia CNE.")
    return {
        "historical_2011": HISTORICAL_2011_THRESHOLDS,
        "public_signals": public_signals,
        "decision_parameters": parameters,
        "decision_reproducible": decision_reproducible,
        "verdict": verdict,
    }


def build_report(stats, archive, afdj, inhga, sen, snn, as_of=None):
    as_of = date.fromisoformat(as_of or stats.get("generat") or date.today().isoformat())
    hist = historical_cernavoda(archive, as_of)
    debit_rows = stats.get("debit") or []
    cern = next((row for row in debit_rows if "Cernavod" in row.get("name", "")), {})
    bazias_model = next((row for row in debit_rows if "Bazia" in row.get("name", "")), {})
    cern_gauge = _find_station(afdj, "cernavoda")

    measured = _number(inhga.get("debit_bazias_m3s"))
    monthly_mean = _number(inhga.get("media_multianuala_m3s"))
    measured_ratio = measured / monthly_mean if measured is not None and monthly_mean else None
    cern_pct = _number(cern.get("percentila"))

    claims = []
    physical_evidence = {
        "cernavoda_model_percentile": cern_pct,
        "cernavoda_model_m3s": _number(cern.get("azi_m3s")),
        "model_days_below_p10": cern.get("zile_sub_p10"),
        "bazias_measured_m3s": measured,
        "bazias_monthly_mean_m3s": monthly_mean,
        "bazias_measured_share_pct": round(measured_ratio * 100, 1) if measured_ratio is not None else None,
        "bazias_bulletin_date": inhga.get("data_buletin"),
    }
    if cern_pct is not None and cern_pct < 10 and measured_ratio is not None and measured_ratio < 0.8:
        strength = "foarte sever" if cern_pct < 2 and measured_ratio < 0.5 else "sever"
        claims.append(_claim(
            "physical", "Fenomenul hidrologic", "confirmed",
            f"Debitul scăzut al Dunării este confirmat și {strength} față de reperele disponibile.",
            physical_evidence,
            "Percentila Cernavodă este modelată; valoarea Baziaș este măsurată în altă secțiune."))
    elif cern_pct is not None and cern_pct < 10:
        claims.append(_claim(
            "physical", "Fenomenul hidrologic", "model_signal",
            "Modelul indică ape scăzute la Cernavodă, dar confirmarea oficială comparabilă este incompletă.",
            physical_evidence,
            "Un semnal GloFAS nu este singur o măsurătoare locală."))
    elif cern_pct is not None:
        claims.append(_claim(
            "physical", "Fenomenul hidrologic", "not_supported",
            "Datele curente nu susțin un episod rar de ape mici la Cernavodă.",
            physical_evidence,
            "Concluzia se referă la data și seria modelată curente."))
    else:
        claims.append(_claim(
            "physical", "Fenomenul hidrologic", "insufficient",
            "Nu sunt suficiente date pentru clasificarea hidrologică la Cernavodă.",
            physical_evidence,
            "Lipsește percentila modelului pentru aceeași zi."))

    if hist["rank_low_to_high"] is None:
        rarity_status = "insufficient"
        rarity_text = "Comparația istorică pe aceeași dată nu este disponibilă."
    elif hist["rank_low_to_high"] == 1:
        rarity_status = "model_record"
        rarity_text = (f"Este cea mai mică valoare GloFAS pentru {as_of.strftime('%d.%m')} "
                       f"în cei {hist['years_compared']} ani disponibili ai modelului.")
    elif hist["rank_low_to_high"] <= 3:
        rarity_status = "rare_not_unprecedented"
        rarity_text = (f"Este a {hist['rank_low_to_high']}-a cea mai mică valoare GloFAS "
                       f"pentru {as_of.strftime('%d.%m')}; există {len(hist['lower_years'])} "
                       "an(i) mai jos în aceeași comparație.")
    else:
        rarity_status = "not_exceptional"
        rarity_text = (f"Valoarea ocupă locul {hist['rank_low_to_high']} din "
                       f"{hist['years_compared']} pentru aceeași zi calendaristică.")
    claims.append(_claim(
        "rarity", "Este fără precedent?", rarity_status, rarity_text,
        {"rank": hist["rank_low_to_high"], "years": hist["years_compared"],
         "lower_years": hist["lower_years"]},
        "Comparația este în istoricul aceluiași model, nu în seria nivelului bazinului de aspirație CNE."))

    precip = {row.get("id"): row for row in stats.get("precipitatii") or []}
    ro_precip = [precip[pid] for pid in ROMANIA_PRECIP_IDS if pid in precip]
    dry90 = [row for row in ro_precip
             if _number((row.get("ultimele90") or {}).get("pct")) is not None
             and row["ultimele90"]["pct"] < 10]
    above_ytd = [row for row in ro_precip
                 if _number((row.get("ian_azi") or {}).get("abatere_pct")) is not None
                 and row["ian_azi"]["abatere_pct"] > 0]
    rain_evidence = {
        "romania_proxy_points": len(ro_precip),
        "points_below_p10_last90": len(dry90),
        "points_above_ytd_median": len(above_ytd),
        "points": [{"id": row.get("id"), "zone": row.get("zona"),
                    "last90_percentile": (row.get("ultimele90") or {}).get("pct"),
                    "ytd_deviation_pct": (row.get("ian_azi") or {}).get("abatere_pct")}
                   for row in ro_precip],
    }
    if len(ro_precip) < 3:
        ro_status = "insufficient"
        ro_text = "Nu avem suficiente puncte-proxy pentru a evalua precipitațiile din România."
    elif len(dry90) >= 3:
        ro_status = "supported_component"
        ro_text = "Punctele-proxy indică un deficit pluviometric larg în România în ultimele 90 de zile."
    elif not dry90 and len(above_ytd) >= 3:
        ro_status = "not_supported"
        ro_text = ("Punctele-proxy din România nu susțin o secetă pluviometrică uniformă: "
                   "niciunul nu este sub P10 în ultimele 90 de zile, iar majoritatea sunt peste mediana anuală.")
    else:
        ro_status = "mixed"
        ro_text = "Semnalul de precipitații din România este mixt și nu susține o etichetă națională unică."
    claims.append(_claim(
        "romania_scope", "Criză hidrologică în toată România?", ro_status, ro_text,
        rain_evidence,
        "ERA5 este reanaliză în patru puncte-proxy; lipsesc o sinteză curentă a acumulărilor, râurilor interioare și umidității solului."))

    snn_status = snn.get("data", snn) if snn else {}
    snn_stale = bool(snn.get("stale")) if snn and "data" in snn else False
    nuclear = _number(sen.get("nuclear_mw"))
    consumption = _number(sen.get("consum_mw"))
    imports = _number(sen.get("sold_mw"))
    unit_equivalent = ("aproximativ două unități" if nuclear is not None and nuclear >= 1100
                       else "aproximativ o unitate" if nuclear is not None and 400 <= nuclear <= 900
                       else "aproape zero" if nuclear is not None and nuclear < 150
                       else "stare intermediară sau neclară")
    energy_evidence = {
        "snn_report_date": snn_status.get("date"),
        "snn_report_url": (snn_status.get("latest_report") or {}).get("url"),
        "snn_report_title": (snn_status.get("latest_report") or {}).get("title"),
        "snn_pdf_sha256": snn_status.get("pdf_sha256"),
        "snn_status_fresh": snn_status.get("status_fresh"),
        "snn_needs_review": snn_status.get("needs_review"),
        "snn_stale_cache": snn_stale,
        "u1": snn_status.get("u1"), "u2": snn_status.get("u2"),
        "water_related": snn_status.get("water_related"),
        "nuclear_mw": nuclear, "nuclear_unit_equivalent": unit_equivalent,
        "sen_updated": sen.get("actualizat"),
        "imports_mw": imports,
        "imports_share_consumption_pct": (round(100 * imports / consumption, 1)
                                           if imports is not None and imports > 0 and consumption else None),
        "hydro_mw": _number(sen.get("hidro_mw")),
    }
    stopped_for_water = (snn_status.get("status_available")
                         and snn_status.get("status_fresh")
                         and snn_status.get("water_related")
                         and "oprit" in (snn_status.get("u1") or ""))
    if stopped_for_water:
        cne_status = "confirmed"
        cne_text = ("Există o consecință energetică reală: SNN raportează U1 oprită din cauza "
                    f"parametrilor legați de apă, iar SEN arată {unit_equivalent} nucleară în producție.")
    elif snn_status.get("needs_review") or snn_stale or not snn_status.get("status_fresh"):
        cne_status = "insufficient"
        cne_text = "Starea CNE nu poate fi afirmată curent: raportul SNN este nou, vechi sau servit din cache stale."
    elif nuclear is not None and nuclear >= 1100:
        cne_status = "not_current"
        cne_text = "SEN indică producție compatibilă cu două unități; nu apare acum o pierdere nucleară de o unitate."
    else:
        cne_status = "insufficient"
        cne_text = "Producția SEN nu explică singură ce unitate este oprită și din ce cauză."
    claims.append(_claim(
        "cernavoda_impact", "Impact operațional la Cernavodă", cne_status, cne_text,
        energy_evidence,
        "Transelectrica publică agregatul nuclear, nu cauza; cauza este acceptată numai dintr-un raport SNN revizuit și proaspăt."))

    if stopped_for_water:
        crisis_text = ("Pierderea unei unități este materială, dar datele disponibile nu demonstrează "
                       "o criză energetică națională critică.")
    elif nuclear is not None and nuclear >= 1100:
        crisis_text = "Nu apare acum o pierdere nucleară; o criză energetică produsă de Cernavodă nu este susținută."
    else:
        crisis_text = "Criticitatea energetică națională nu poate fi evaluată din fotografia SEN disponibilă."
    claims.append(_claim(
        "national_energy_crisis", "Criză energetică națională critică?", "not_demonstrated",
        crisis_text, energy_evidence,
        "Lipsesc comparațiile istorice pentru cerere, importuri, rezerve, prețuri și eventuale măsuri de urgență; un instantaneu SEN nu este un test de criză."))

    physical_confirmed = claims[0]["status"] == "confirmed"
    cne_confirmed = cne_status == "confirmed"
    if physical_confirmed and cne_confirmed:
        headline = ("Fenomenul și efectul asupra U1 sunt reale; caracterizarea drept "
                    "«criză energetică națională critică» nu este demonstrată de datele curente.")
    elif physical_confirmed:
        headline = ("Fenomenul hidrologic este real, dar efectul energetic curent și amploarea "
                    "națională nu sunt suficient demonstrate.")
    else:
        headline = "Datele curente nu permit validarea simultană a fenomenului, impactului CNE și criticității naționale."

    operational_history = _operational_history(
        as_of, snn_status, snn_stale, cern, cern_gauge, measured, monthly_mean)
    parameter_transparency = _parameter_transparency(
        cern, cern_gauge, measured, monthly_mean, nuclear, snn_status)
    current_intake_level = _number(snn_status.get("intake_basin_level_mdmb"))

    return {
        "generated": as_of.isoformat(),
        "headline": headline,
        "claims": claims,
        "cernavoda": {
            "model": cern,
            "gauge": cern_gauge,
            "gauge_warning": ("Cota AFDJ este raportată la zero-ul mirei locale; nu se compară direct "
                              "cu pragurile bazinului de aspirație în mdMB."),
            "intake_basin": {
                "available": current_intake_level is not None,
                "level_mdmb": current_intake_level,
                "reason": (None if current_intake_level is not None else
                           "nivelul curent și pragurile operaționale comparabile nu sunt publicate în fluxul ingerat"),
            },
            "history": hist,
            "operational_history": operational_history,
            "parameter_transparency": parameter_transparency,
        },
        "energy": energy_evidence,
        "missing_for_national_verdict": [
            "gradul curent de umplere și volumele utile ale acumulărilor, într-o serie națională comparabilă",
            "debite și restricții curente pe râurile interioare, agregate pe bazine",
            "umiditate a solului și secetă agricolă validate spațial pentru România",
            "serii SEN pentru cerere, importuri, rezerve și prețuri înainte și după oprirea unității",
            "nivelul bazinului de aspirație CNE, aceeași cotă de referință și pragurile operaționale curente",
        ],
        "method": ("Reguli deterministe; nicio afirmație despre cauză sau criticitate nu este "
                   "dedusă dintr-o singură valoare și nicio analiză AI nu rulează."),
    }
