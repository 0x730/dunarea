"""Analiză deterministă de proporționalitate pentru România și Cernavodă.

Separă fenomenul hidrologic, consecința operațională și criticitatea SEN.
Nu încearcă să demonstreze o narațiune; fiecare concluzie se schimbă odată cu
intrările și revine la „date insuficiente” când lipsește veriga necesară.
"""

import re
import unicodedata
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
DC_2003_HYDRO_YEARBOOK_URL = (
    "https://www.danubecommission.org/uploads/doc/Library_scan/"
    "hydro_yearbooks/5.1.51_fr_ru_de.pdf")
DC_2011_WATERWAY_REPORT_URL = (
    "https://www.danubecommission.org/uploads/doc/2017/"
    "EG_Hydro_5_6_09_2017/yearbook_2011.pdf")
DC_2015_WATERWAY_REPORT_URL = (
    "https://www.danubecommission.org/uploads/doc/2021/yearbook_2015.pdf")
AFDJ_2020_2025_LEVELS_URL = (
    "https://www.danubecommission.org/uploads/doc/2026/"
    "20260305_EG_HYDRO/01_RO_AFDJ.pdf")
AFDJ_CURRENT_LEVELS_URL = "https://www.afdj.ro/ro/cotele-dunarii"


# Rezumate factuale, nu copii ale tabelelor zilnice. Cotele sunt observații la
# mira hidrometrică/de navigație Cernavodă și rămân în centimetri față de zero-ul
# local al mirei. Nu sunt convertite în mdMB și nu sunt prezentate ca nivel al
# bazinului de aspirație al centralei.
HISTORICAL_CERNAVODA_GAUGE_CONTEXT = {
    2003: {
        "available": True,
        "station": "Cernavodă",
        "period": "aug.–sept. 2003",
        "facts": [
            {"label": "minim în fereastră", "value": -237, "unit": "cm",
             "date": "2003-09-10"},
            {"label": "minim în august", "value": -213, "unit": "cm",
             "date": "2003-08-31"},
        ],
        "source": {
            "label": "Comisia Dunării — Anuar hidrologic 2003, p. 39",
            "url": DC_2003_HYDRO_YEARBOOK_URL,
        },
        "source_scope": "rezumat derivat din cotele zilnice măsurate publicate pentru Cernavodă",
    },
    2011: {
        "available": True,
        "station": "Cernavodă",
        "period": "15–30 sept. 2011",
        "facts": [
            {"label": "minim în fereastră", "value": -138, "unit": "cm",
             "dates": ["2011-09-26", "2011-09-27"]},
            {"label": "media lunii septembrie", "value": -101, "unit": "cm"},
        ],
        "source": {
            "label": "Comisia Dunării — Raport anual 2011, p. 190",
            "url": DC_2011_WATERWAY_REPORT_URL,
        },
        "source_scope": "minimul ferestrei și media lunară din cotele zilnice măsurate",
    },
    2015: {
        "available": True,
        "station": "Cernavodă",
        "period": "22 iul.–30 sept. 2015",
        "facts": [
            {"label": "minim în fereastră", "value": -119, "unit": "cm",
             "date": "2015-09-12"},
            {"label": "minim august", "value": -98, "unit": "cm",
             "date": "2015-08-27"},
        ],
        "source": {
            "label": "Comisia Dunării — Raport anual 2015, p. 190",
            "url": DC_2015_WATERWAY_REPORT_URL,
        },
        "source_scope": "rezumat derivat din cotele zilnice măsurate publicate pentru Cernavodă",
    },
    2022: {
        "available": True,
        "station": "Cernavodă",
        "period": "anul 2022; context august",
        "facts": [
            {"label": "minim anual", "value": -195, "unit": "cm"},
            {"label": "zile sub LNWL în august", "value": 31, "unit": "zile"},
            {"label": "LNWL publicat pentru 2022", "value": -71, "unit": "cm"},
        ],
        "source": {
            "label": "AFDJ / Comisia Dunării — prezentare 2020–2025, slide 15–16",
            "url": AFDJ_2020_2025_LEVELS_URL,
        },
        "source_scope": "minim anual și numărul lunar de zile sub LNWL; nu o serie zilnică",
    },
}

GAUGE_CONTEXT_LIMIT = (
    "cotă la mira Cernavodă, în cm față de zero-ul local; nu este debit și nu se "
    "convertește aici în nivelul bazinului de aspirație exprimat în mdMB")


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
        "model_window": {
            "start": "2003-08-01", "end": "2003-09-30",
            "label": "aug.–sept. 2003",
            "basis": "fereastră de context; comunicatul SNN folosit nu publică ziua exactă a opririi",
        },
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
        "model_window": {
            "start": "2011-09-15", "end": "2011-09-30",
            "label": "15–30 sept. 2011",
            "basis": "de la data comunicatului până la sfârșitul lunii",
        },
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
        "model_window": {
            "start": "2015-07-22", "end": "2015-09-30",
            "label": "22 iul.–30 sept. 2015",
            "basis": "intervalul de prognoză discutat de comunicatul SNN",
        },
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
        "model_window": {
            "start": "2022-08-26", "end": "2022-08-31",
            "label": "26–31 aug. 2022",
            "basis": "intervalul opririi consemnat în raportul anual SNN",
        },
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
    # bool e subclasă de int: True ar trece drept nivel/debit valid.
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _series_map(archive):
    return {
        stamp: value for stamp, value in zip(
            archive.get("time") or [], archive.get("discharge") or [])
        if _number(value) is not None
    }


def _plain(value):
    """Minuscule fără diacritice — potriviri robuste pe textul buletinelor."""
    decomposed = unicodedata.normalize("NFKD", value if isinstance(value, str) else "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


# „bala" ca subșir prinde „balastiere", termen de rutină în buletinele despre
# albie. Cerem numele brațului ca sintagmă, cu limite de cuvânt.
_BALA_BRANCH_RE = re.compile(r"\bbratul\s+bala\b")


def _iso_date(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


SNN_FRESH_MAX_AGE_DAYS = 3


def _snn_accepted(snn_status, snn_stale, as_of=None):
    """Raportul SNN poate fi citat ca stare CURENTĂ a unităților.

    `status_fresh` e calculat de conector la momentul preluării și îngheață în
    cache: dacă sursa cade, un raport vechi de săptămâni continuă să se declare
    proaspăt. Recalculăm din data raportului și cerem explicit ca payload-ul să
    nu fie servit din cache expirat.
    """
    if not snn_status.get("status_available") or snn_status.get("needs_review"):
        return False
    if snn_stale:
        return False
    published = _iso_date(snn_status.get("date"))
    if published is None:
        return False
    age = ((as_of or date.today()) - published).days
    return 0 <= age <= SNN_FRESH_MAX_AGE_DAYS


def _model_evidence_date(cern, archive, generated_on):
    """Data reală a probei GloFAS, niciodată data ceasului prin presupunere."""
    stated = _iso_date(cern.get("data"))
    if stated is not None:
        return stated
    archive_dates = [_iso_date(stamp) for stamp in (archive.get("time") or [])]
    available = [stamp for stamp in archive_dates
                 if stamp is not None and stamp <= generated_on]
    return max(available) if available else None


def historical_cernavoda(archive, as_of):
    """Comparație echitabilă pe aceeași zi și aceeași fereastră sezonieră.

    „Minimul verii până la data curentă” folosește exact aceeași limită
    calendaristică pentru fiecare an. Pentru anii încheiați adăugăm separat
    minimul întregii luni august; anul curent rămâne explicit parțial.
    """
    if as_of is None:
        return {
            "as_of": None, "same_calendar_day": None, "current_m3s": None,
            "rank_low_to_high": None, "years_compared": 0,
            "lower_years": [], "rows": [],
            "method": ("Comparația nu rulează fără data reală a probei GloFAS; "
                       "data generării nu este folosită ca substitut."),
        }
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


def _model_window_context(archive, window):
    """Rezumat GloFAS într-o fereastră explicită, fără a inventa valori lipsă."""
    values = _series_map(archive)
    start, end = window["start"], window["end"]
    items = sorted((stamp, value) for stamp, value in values.items()
                   if start <= stamp <= end)
    expected_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if not items:
        return {
            **window,
            "available": False,
            "days_available": 0,
            "days_expected": expected_days,
            "complete": False,
            "method": "GloFAS/Copernicus · aceeași celulă Cernavodă",
        }
    minimum_stamp, minimum_value = min(items, key=lambda item: item[1])
    return {
        **window,
        "available": True,
        "start_value_m3s": (round(values[start], 1) if start in values else None),
        "end_value_m3s": (round(values[end], 1) if end in values else None),
        "minimum": {"date": minimum_stamp, "value_m3s": round(minimum_value, 1)},
        "days_available": len(items),
        "days_expected": expected_days,
        "complete": len(items) == expected_days,
        "method": "GloFAS/Copernicus · aceeași celulă Cernavodă",
        "limit": "debit modelat al fluviului; nu este debit măsurat la priza CNE",
    }


def _operational_history(generated_on, model_as_of, archive, snn_status,
                         snn_stale, cern, cern_gauge, measured, monthly_mean):
    """Leagă anii hidrologici de acțiunea publicată de operator.

    Intrările istorice sunt constatări datate, nu stări curente. Ultimul rând
    este construit exclusiv din raportul SNN curent acceptat de conector.
    """
    rows = [dict(row) for row in HISTORICAL_CNE_OPERATIONS]
    for row in rows:
        row["model_context"] = _model_window_context(archive, row["model_window"])
        gauge_context = HISTORICAL_CERNAVODA_GAUGE_CONTEXT.get(row["year"])
        row["gauge_context"] = ({**gauge_context, "limit": GAUGE_CONTEXT_LIMIT}
                                if gauge_context else {
                                    "available": False,
                                    "period": row["model_window"]["label"],
                                    "facts": [],
                                    "limit": GAUGE_CONTEXT_LIMIT,
                                })
    report = snn_status.get("latest_report") or {}
    source = ({"label": report.get("title") or "SNN — raport operațional curent",
               "url": report.get("url")} if report.get("url") else None)
    u1 = snn_status.get("u1")
    u2 = snn_status.get("u2")
    accepted = _snn_accepted(snn_status, snn_stale, generated_on)
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
    if _number((cern_gauge or {}).get("cota_cm")) is not None:
        current_hydrology.append(f"miră AFDJ {cern_gauge['cota_cm']} cm")
    if _number((cern_gauge or {}).get("temp_apa_c")) is not None:
        current_hydrology.append(f"apă la mira AFDJ {cern_gauge['temp_apa_c']}°C")
    if measured is not None:
        bazias = f"Baziaș {measured:g} m³/s"
        if monthly_mean:
            bazias += f" ({100 * measured / monthly_mean:.1f}% din media lunii)"
        current_hydrology.append(bazias)

    if model_as_of is not None:
        current_model = _model_window_context(archive, {
            "start": model_as_of.isoformat(), "end": model_as_of.isoformat(),
            "label": model_as_of.strftime("%d.%m.%Y"),
            "basis": "data reală a ultimei probe GloFAS folosite",
        })
    else:
        current_model = {
            "start": None, "end": None, "label": "dată GloFAS indisponibilă",
            "basis": "nicio dată a probei nu a putut fi stabilită",
            "available": False, "days_available": 0, "days_expected": 0,
            "complete": False,
            "method": "GloFAS/Copernicus · aceeași celulă Cernavodă",
        }
    current_model["percentile"] = _number(cern.get("percentila"))
    current_model["days_below_p10"] = cern.get("zile_sub_p10")
    if (model_as_of is not None and not current_model["available"]
            and _number(cern.get("azi_m3s")) is not None):
        current_model.update({
            "available": True,
            "start_value_m3s": round(cern["azi_m3s"], 1),
            "end_value_m3s": round(cern["azi_m3s"], 1),
            "minimum": {"date": model_as_of.isoformat(),
                        "value_m3s": round(cern["azi_m3s"], 1)},
            "days_available": 1,
            "complete": True,
        })

    current_gauge_value = _number((cern_gauge or {}).get("cota_cm"))
    current_water_temperature = _number((cern_gauge or {}).get("temp_apa_c"))
    current_gauge_facts = []
    if current_gauge_value is not None:
        current_gauge_facts.append({
            "label": "ultima citire", "value": current_gauge_value,
            "unit": "cm", "date": (cern_gauge or {}).get("actualizat"),
        })
    if current_water_temperature is not None:
        current_gauge_facts.append({
            "label": "temperatura apei la miră", "value": current_water_temperature,
            "unit": "°C", "date": (cern_gauge or {}).get("actualizat"),
        })
    gauge_forecast = [
        {"hours": hours, "value_cm": value}
        for hours in (24, 48, 72, 96, 120)
        if (value := _number(((cern_gauge or {}).get("tendinte_cm") or {}).get(
            f"{hours}h"))) is not None
    ]
    current_gauge = {
        "available": current_gauge_value is not None,
        "station": "Cernavodă",
        "period": ((cern_gauge or {}).get("actualizat") or "data citirii indisponibilă"),
        "facts": current_gauge_facts,
        "forecast": gauge_forecast,
        "source": {"label": "AFDJ — cotele Dunării",
                   "url": AFDJ_CURRENT_LEVELS_URL},
        "source_scope": ("cota și temperatura sunt observații la miră; valorile +24…+120 h "
                         "sunt prognoza AFDJ și se schimbă odată cu sursa"),
        "limit": GAUGE_CONTEXT_LIMIT,
    }

    rows.append({
        "year": generated_on.year,
        "current": True,
        "reference_date": snn_status.get("date") or generated_on.isoformat(),
        "hydrology": ("; ".join(current_hydrology) if current_hydrology else
                      "valorile hidrologice curente nu sunt disponibile"),
        "plant_action": plant_action,
        "classification": classification,
        "interpretation": interpretation,
        "source": source,
        "source_scope": "starea CNE; contextul hidrologic vine din fluxurile curente ale monitorului",
        "model_window": ({"start": model_as_of.isoformat(),
                          "end": model_as_of.isoformat()}
                         if model_as_of is not None else None),
        "model_context": current_model,
        "gauge_context": current_gauge,
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
            "key": "cernavoda_water_temperature",
            "label": "temperatura apei AFDJ Cernavodă",
            "value": _number((cern_gauge or {}).get("temp_apa_c")),
            "unit": "°C",
            "context": (cern_gauge or {}).get("actualizat") or "dată indisponibilă",
            "what_it_proves": ("context termic măsurat la mira AFDJ; nu este temperatura "
                               "certificată în bazinul de aspirație CNE"),
        },
        {
            "key": "cernavoda_gauge_forecast_120h",
            "label": "prognoză cotă AFDJ +120 h",
            "value": _number((((cern_gauge or {}).get("tendinte_cm") or {}).get("120h"))),
            "unit": "cm",
            "context": ("prognoză de nivel, nu observație și nu prag operațional CNE"),
            "what_it_proves": "direcția proiectată la mira de navigație în următoarele cinci zile",
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


def _tributary_context(source, generated_on):
    """Selectează numai luna raportului, fără a relabela altă lună ca prezent."""
    if not isinstance(source, dict) or not source.get("available"):
        return {
            "available": False,
            "reason": (source or {}).get("reason", "prognoza lunară INHGA nu este disponibilă"),
        }
    wanted = generated_on.strftime("%Y-%m")
    selected = next((item for item in (source.get("months") or [])
                     if item.get("month") == wanted), None)
    base = {key: value for key, value in source.items() if key != "months"}
    if selected is None:
        return {
            **base,
            "available": False,
            "reason": f"ultimul buletin nu conține prognoza pentru {wanted}",
            "forecast_month": None,
            "horizon": [item.get("month") for item in (source.get("months") or [])],
        }
    basins = ((selected.get("upstream_cernavoda") or [])
              + (selected.get("downstream_cernavoda") or []))
    low = []
    very_low = []
    for basin in basins:
        band = basin.get("band_pct") or {}
        maximum = _number(band.get("max"))
        if maximum is not None and maximum <= 50:
            low.append(basin.get("id"))
        if band.get("operator") == "lt" and maximum is not None and maximum <= 30:
            very_low.append(basin.get("id"))
    return {
        **base,
        "available": True,
        "forecast_month": selected,
        "horizon": [item.get("month") for item in (source.get("months") or [])],
        "selected_systems": len(basins),
        "systems_at_most_50pct": low,
        "systems_explicit_below_30pct": very_low,
    }


def _tributary_observation_context(source, generated_on):
    """Păstrează numai secțiuni datate, fără a le echivala cu aportul la gură."""
    if not isinstance(source, dict) or not source.get("available"):
        return {
            "available": False,
            "reason": (source or {}).get(
                "reason", "secțiunile măsurate DanubeHIS nu sunt disponibile"),
        }
    accepted = []
    for section in source.get("sections") or []:
        latest = (section.get("latest") or {}).get("date")
        try:
            latest_day = date.fromisoformat(latest)
        except (TypeError, ValueError):
            continue
        if latest_day > generated_on:
            continue
        accepted.append({**section, "lag_days": (generated_on - latest_day).days})
    if not accepted:
        return {
            **{key: value for key, value in source.items() if key != "sections"},
            "available": False,
            "reason": "nicio secțiune măsurată nu are o dată acceptabilă",
            "sections": [],
        }
    newest = max((row.get("latest") or {}).get("date") for row in accepted)
    # Sistemele fără secțiune publică vin de la conector; ținta e suma dintre
    # secțiunile urmărite și acestea, calculată din date, nu scrisă de mână.
    missing = list(source.get("missing_systems") or [])
    target = len(source.get("sections") or []) + len(missing)
    return {
        **{key: value for key, value in source.items() if key != "sections"},
        "available": True,
        "sections": accepted,
        "latest_date": newest,
        "sections_available": len(accepted),
        # Ținta reală de sisteme MĂSURATE (secțiuni livrate + sisteme fără
        # secțiune publică), nu numărul de bazine din prognoza lunară INHGA:
        # sunt două mulțimi diferite, iar confuzia dintre ele declara închisă
        # lacuna „debite măsurate aproape de confluențe".
        "measured_systems_target": target,
        "missing_systems": list(missing),
    }


def _tributary_model_context(source, generated_on):
    """Acceptă numai percentile GloFAS datate și cu referință multidecenală."""
    if not isinstance(source, dict) or not source.get("available"):
        return {
            "available": False,
            "reason": (source or {}).get(
                "reason", "climatologia GloFAS a afluenților nu este disponibilă"),
        }
    accepted = []
    for section in source.get("sections") or []:
        try:
            model_day = date.fromisoformat(section.get("model_date"))
            years = int(section.get("reference_years") or 0)
            samples = int(section.get("reference_samples") or 0)
        except (TypeError, ValueError):
            continue
        if model_day > generated_on or years < 20 or samples < 300:
            continue
        accepted.append({**section, "lag_days": (generated_on - model_day).days})
    if not accepted:
        return {
            **{key: value for key, value in source.items() if key != "sections"},
            "available": False,
            "reason": "nicio secțiune modelată nu are dată și referință acceptabile",
            "sections": [],
        }
    return {
        **{key: value for key, value in source.items() if key != "sections"},
        "available": True,
        "sections": accepted,
        "latest_date": max(row["model_date"] for row in accepted),
        "sections_available": len(accepted),
        "sections_below_p10": [
            row["river_id"] for row in accepted
            if _number(row.get("percentile")) is not None
            and row["percentile"] < 10
        ],
    }


def build_report(stats, archive, afdj, inhga, sen, snn, as_of=None,
                 tributaries=None, tributary_observations=None,
                 tributary_model_climatology=None, water_resources=None,
                 sen_history=None, energy_market=None):
    # Raportul se generează ACUM. `stats["generat"]` e data instantaneului de
    # statistici (TTL 6 h, servit și stale): folosit ca „azi", rămâne în urmă
    # noaptea și respinge ca „din viitor" surse care livrează legitim date de
    # azi. `as_of` rămâne pentru teste și pentru regenerări istorice.
    generated_on = date.fromisoformat(as_of or date.today().isoformat())
    stats_generated_on = _iso_date(stats.get("generat")) or generated_on
    debit_rows = stats.get("debit") or []
    cern = next((row for row in debit_rows if "Cernavod" in row.get("name", "")), {})
    model_as_of = _model_evidence_date(cern, archive, generated_on)
    hist = historical_cernavoda(archive, model_as_of)
    cern_gauge = _find_station(afdj, "cernavoda")
    tributary_context = _tributary_context(tributaries, generated_on)
    tributary_observed = _tributary_observation_context(
        tributary_observations, generated_on)
    tributary_model = _tributary_model_context(
        tributary_model_climatology, generated_on)
    tributary_context["observed_sections"] = tributary_observed
    tributary_context["model_climatology"] = tributary_model
    water_resources = water_resources if isinstance(water_resources, dict) else {
        "available": False, "current": False,
        "reason": "contextul ANAR nu a fost disponibil",
    }
    sen_history = sen_history if isinstance(sen_history, dict) else {
        "available": False, "enough_for_comparison": False,
        "days": 0, "minimum_days": 14,
    }
    energy_market = energy_market if isinstance(energy_market, dict) else {
        "available_components": 0, "component_count": 4,
    }

    measured = _number(inhga.get("debit_bazias_m3s"))
    monthly_mean = _number(inhga.get("media_multianuala_m3s"))
    measured_ratio = measured / monthly_mean if measured is not None and monthly_mean else None
    cern_pct = (_number(cern.get("percentila"))
                if model_as_of is not None else None)

    claims = []
    physical_evidence = {
        "cernavoda_model_date": model_as_of.isoformat() if model_as_of else None,
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
        rarity_text = (f"Este cea mai mică valoare GloFAS pentru {model_as_of.strftime('%d.%m')} "
                       f"în cei {hist['years_compared']} ani disponibili ai modelului.")
    elif hist["rank_low_to_high"] <= 3:
        rarity_status = "rare_not_unprecedented"
        rarity_text = (f"Este a {hist['rank_low_to_high']}-a cea mai mică valoare GloFAS "
                       f"pentru {model_as_of.strftime('%d.%m')}; există {len(hist['lower_years'])} "
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
        "data_through": max((row.get("pana_la") or "" for row in ro_precip),
                            default=None) or None,
        "points": [{"id": row.get("id"), "zone": row.get("zona"),
                    "data_through": row.get("pana_la"),
                    "ytd_mm": (row.get("ian_azi") or {}).get("cumul_mm"),
                    "ytd_median_mm": (row.get("ian_azi") or {}).get("mediana_mm"),
                    "last90_percentile": (row.get("ultimele90") or {}).get("pct"),
                    "last90_mm": (row.get("ultimele90") or {}).get("cumul_mm"),
                    "ytd_deviation_pct": (row.get("ian_azi") or {}).get("abatere_pct")}
                   for row in ro_precip],
        "inhga_selected_tributaries": tributary_context,
        "glofas_tributary_climatology": tributary_model,
        "anar_water_resources": water_resources,
    }
    selected_count = tributary_context.get("selected_systems") or 0
    low_tributaries = tributary_context.get("systems_at_most_50pct") or []
    broad_low_forecast = (tributary_context.get("available") and selected_count > 0
                          and len(low_tributaries) >= max(1, selected_count // 2 + 1))
    model_count = tributary_model.get("sections_available") or 0
    model_low = tributary_model.get("sections_below_p10") or []
    broad_low_model = (tributary_model.get("available") and model_count > 0
                       and len(model_low) >= max(1, model_count // 2 + 1))
    broad_hydro_signal = broad_low_forecast or broad_low_model
    hydro_signals = []
    if broad_low_forecast:
        hydro_signals.append(
            "Prognoza INHGA indică regimuri reduse pe majoritatea afluenților selectați")
    if broad_low_model:
        hydro_signals.append(
            f"GloFAS plasează {len(model_low)} din {model_count} secțiuni modelate sub P10")
    hydro_text = "; ".join(hydro_signals)
    if broad_hydro_signal and len(dry90) >= 3:
        ro_status = "supported_component"
        ro_text = (hydro_text + ", iar ERA5 "
                   "arată și deficit pluviometric larg. Este confirmată o componentă "
                   "hidrologică extinsă, nu automat o criză uniformă în toată România.")
    elif broad_hydro_signal:
        ro_status = "mixed"
        ro_text = (hydro_text + ", dar punctele ERA5 nu "
                   "arată un deficit pluviometric uniform. Semnalul este mai larg decât "
                   "fluviul, fără a demonstra o criză în toată România.")
    elif len(ro_precip) < 3:
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
    anar_current = bool(water_resources.get("available") and
                        water_resources.get("current"))
    anar_reservoirs = water_resources.get("reservoirs") or {}
    anar_restrictions = water_resources.get("restrictions") or {}
    if anar_current:
        # Fiecare clauză se emite numai din flagul ei, explicit. `None` înseamnă
        # „ANAR nu a spus", niciodată „ANAR a spus că nu" — garda de dinainte era
        # un SAU, iar textul afirma ambele fapte în numele instituției.
        supply = anar_reservoirs.get("sufficient_for_centralized_supply")
        drinking = anar_restrictions.get("drinking_water")
        reported = []
        if supply is True:
            reported.append("volume suficiente pentru alimentarea centralizată")
        elif supply is False:
            reported.append("volume insuficiente pentru alimentarea centralizată")
        if drinking is False:
            reported.append("nicio restricție pentru apa populației")
        elif drinking is True:
            reported.append("restricții în vigoare pentru apa populației")
        if reported:
            ro_text += " ANAR raportează totodată " + " și ".join(reported) + "."
            if supply is not False and drinking is not True:
                ro_text += (" Aceasta limitează eticheta de criză națională uniformă, "
                            "fără a anula restricțiile sectoriale.")
    claims.append(_claim(
        "romania_scope", "Criză hidrologică în toată România?", ro_status, ro_text,
        rain_evidence,
        "INHGA oferă prognoze în benzi; DanubeHIS aduce numai cinci secțiuni măsurate parțiale; percentilele afluenților sunt GloFAS față de istoricul propriului model, nu climatologie măsurată; ERA5 rămâne reanaliză în patru puncte-proxy; ANAR este context oficial, nu serie zilnică omogenă."))

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
        "history": sen_history,
        "market": energy_market,
    }
    snn_accepted = _snn_accepted(snn_status, snn_stale, generated_on)
    stopped_for_water = (snn_accepted and snn_status.get("water_related")
                         and "oprit" in (snn_status.get("u1") or "").lower())
    if stopped_for_water:
        cne_status = "confirmed"
        cne_text = ("Există o consecință energetică reală: SNN raportează U1 oprită din cauza "
                    f"parametrilor legați de apă, iar SEN arată {unit_equivalent} nucleară în producție.")
    elif not snn_accepted:
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
        crisis_text = ("Pierderea unei unități este materială, dar acoperirea disponibilă nu permite "
                       "confirmarea sau excluderea unei crize energetice naționale critice.")
    elif nuclear is not None and nuclear >= 1100:
        crisis_text = ("Nu apare acum o pierdere nucleară; o criză produsă de Cernavodă nu este "
                       "susținută, iar criticitatea energetică națională cere probe separate.")
    else:
        crisis_text = "Criticitatea energetică națională nu poate fi evaluată din fotografia SEN disponibilă."
    history_days = int(sen_history.get("days") or 0)
    history_minimum = int(sen_history.get("minimum_days") or 14)
    history_limit = ("Istoricul local are "
                     f"{history_days}/{history_minimum} zile și încă nu este folosit comparativ. "
                     if not sen_history.get("enough_for_comparison") else
                     f"Istoricul local are {history_days} zile, dar fotografiile nu sunt medii zilnice. ")
    market_components = int(energy_market.get("available_components") or 0)
    market_limit = (
        f"Sunt disponibile {market_components}/4 componente oficiale de piață și echilibrare. "
        if market_components else
        "Fluxurile oficiale de piață și echilibrare nu sunt disponibile acum. ")
    crisis_coverage = {
        "sen_history_comparable": bool(sen_history.get("enough_for_comparison")),
        "market_components_complete": market_components >= 4,
        "operational_reserve_margin_available": False,
        "official_emergency_measures_available": False,
    }
    energy_evidence["national_crisis_assessment"] = {
        "ready": all(crisis_coverage.values()),
        "coverage": crisis_coverage,
        "rule": ("Verdictul național cere simultan un baseline SEN comparabil, "
                 "contextul pieței, marja operațională rămasă și măsuri oficiale "
                 "de urgență sau consum întrerupt."),
    }
    claims.append(_claim(
        "national_energy_crisis", "Criză energetică națională critică?", "insufficient",
        crisis_text, energy_evidence,
        history_limit + market_limit +
        "Capacitatea contractată și rezerva activată nu arată marja operațională rămasă; "
        "lipsesc și eventualele măsuri de urgență. Un instantaneu SEN sau un preț mare nu este singur un test de criză."))

    physical_confirmed = claims[0]["status"] == "confirmed"
    cne_confirmed = cne_status == "confirmed"
    if physical_confirmed and cne_confirmed:
        headline = ("Fenomenul și efectul asupra U1 sunt reale; caracterizarea drept "
                    "«criză energetică națională critică» nu poate fi verificată cu acoperirea curentă.")
    elif physical_confirmed:
        headline = ("Fenomenul hidrologic este real, dar efectul energetic curent și amploarea "
                    "națională nu sunt suficient demonstrate.")
    else:
        headline = "Datele curente nu permit validarea simultană a fenomenului, impactului CNE și criticității naționale."

    operational_history = _operational_history(
        generated_on, model_as_of, archive, snn_status, snn_stale, cern,
        cern_gauge, measured, monthly_mean)
    parameter_transparency = _parameter_transparency(
        cern, cern_gauge, measured, monthly_mean, nuclear, snn_status)
    current_intake_level = _number(snn_status.get("intake_basin_level_mdmb"))
    official_bulletin_text = [str(item) for item in inhga.get("text_oficial") or []]
    # Evaluat pe textul unit: avertismentul real poate fi împărțit pe două
    # paragrafe, iar varianta per-paragraf îl rata.
    bulletin_blob = _plain(" ".join(official_bulletin_text))
    bala_caveat = bool(_BALA_BRANCH_RE.search(bulletin_blob)) and "cernavod" in bulletin_blob

    missing = []
    reservoir_complete = (anar_current and
                          anar_reservoirs.get("fill_pct") is not None and
                          anar_reservoirs.get("volume_billion_m3") is not None)
    if not reservoir_complete:
        if anar_current:
            missing.append(
                "ANAR oferă context curent despre acumulări și restricții, dar nu publică în comunicatul ingerat simultan coeficientul de umplere și volumul util național")
        else:
            missing.append(
                "gradul curent de umplere și volumele utile ale acumulărilor, într-o serie națională comparabilă")
    missing.append(
        "debite măsurate aproape de confluență pentru toate cele nouă sisteme; cele cinci secțiuni DanubeHIS integrate au acoperire parțială")
    missing.append(
        "umiditate a solului și secetă agricolă validate spațial pentru România")
    available_market = [key for key in ("consumption", "reserve_procurement",
                                        "balancing", "day_ahead")
                        if (energy_market.get(key) or {}).get("available")]
    if not sen_history.get("enough_for_comparison"):
        missing.append(
            f"baseline SEN comparabil: arhiva locală acumulează {history_days}/{history_minimum} zile; "
            f"fluxuri oficiale curente disponibile {len(available_market)}/4")
    elif len(available_market) < 4:
        missing.append(
            f"acoperire incompletă a fluxurilor oficiale de piață și echilibrare: {len(available_market)}/4 disponibile")
    missing.append(
        "marja operațională de rezervă rămasă și eventualele măsuri de urgență; rezervele contractate și activate nu o reproduc")
    if not parameter_transparency.get("decision_reproducible"):
        missing.append(
            "nivelul bazinului de aspirație CNE, aceeași cotă de referință și pragurile operaționale curente")

    return {
        "generated": generated_on.isoformat(),
        "data_as_of": {
            "glofas": model_as_of.isoformat() if model_as_of else None,
            "glofas_lag_days": ((generated_on - model_as_of).days
                                if model_as_of is not None else None),
            "inhga": inhga.get("data_buletin"),
            "inhga_tributaries": tributary_context.get("published"),
            "danubehis_ro_tributaries": tributary_observed.get("latest_date"),
            "glofas_ro_tributaries": tributary_model.get("latest_date"),
            "afdj": (cern_gauge or {}).get("actualizat"),
            "snn": snn_status.get("date"),
            "sen": sen.get("actualizat"),
            "damas_consumption": (energy_market.get("consumption") or {}).get("delivery_date"),
            "damas_reserves": (energy_market.get("reserve_procurement") or {}).get("delivery_date"),
            "damas_balancing": (energy_market.get("balancing") or {}).get("delivery_date"),
            "opcom_delivery": (energy_market.get("day_ahead") or {}).get("delivery_date"),
        },
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
        "water_resources": water_resources,
        "official_danube_bulletin": {
            "date": inhga.get("data_buletin"),
            "url": inhga.get("url"),
            "cernavoda_bala_caveat": bala_caveat,
            "scope": ("INHGA marchează prognoza Cernavodă drept orientativă din cauza "
                      "intervențiilor din zona brațului Bala" if bala_caveat else None),
        },
        "romanian_tributaries": tributary_context,
        "missing_for_national_verdict": missing,
        "method": ("Reguli deterministe; nicio afirmație despre cauză sau criticitate nu este "
                   "dedusă dintr-o singură valoare și nicio analiză AI nu rulează."),
    }
