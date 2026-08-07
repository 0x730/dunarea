#!/usr/bin/env python3
"""Monitor Dunărea — server local.

Pornire:  python3 server.py   (implicit http://localhost:7300)
Opțional: PORT=8080 python3 server.py
          ENTSOE_TOKEN=... python3 server.py   (activează datele PF I/II)
"""

import base64
import datetime
import json
import os
import re
import sys
import threading
import time
import traceback
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

# Toate noțiunile de „zi curentă” ale monitorului (buletin INHGA, DAMAS,
# snapshot local) urmează România, indiferent de fusul orar implicit al VPS.
os.environ["TZ"] = os.environ.get("MONITOR_TZ", "Europe/Bucharest")
if hasattr(time, "tzset"):
    time.tzset()

import analiza_ai
import anomalii
import connectors as C
import romania

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# realpath: candidatul e comparat tot cu realpath. Pe un deploy cu director de
# release prin symlink, o cale nerezolvată aici ar face containment-ul să pice
# pentru ORICE fișier, iar staticele ar da 404 în tăcere.
STATIC_DIR = os.path.realpath(os.path.join(BASE_DIR, "static"))
PORT = int(os.environ.get("PORT", "7300"))

# Jurnal: erorile și cererile lente se scriu întotdeauna; access log-ul complet
# se activează la cerere, ca să nu inunde consola supervisorului.
ACCESS_LOG = os.environ.get("MONITOR_ACCESS_LOG", "").strip().lower() in {
    "1", "true", "yes", "on",
}
SLOW_REQUEST_MS = float(os.environ.get("MONITOR_SLOW_MS", "5000"))
STARTED_AT = time.time()

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class BadRequest(ValueError):
    """Parametru public invalid; nu este o defecțiune a sursei externe."""


_SENSITIVE_QUERY_KEYS = {
    "token", "api_key", "apikey", "access_token", "securitytoken",
    "signature", "x-amz-signature", "x-api-key",
}
# Lista de nume exacte e o listă de respingere: o sursă viitoare care numește
# altfel parametrul ar trece pe lângă ea. O completăm cu potrivire pe CUVINTE
# din numele parametrului, nu pe subșiruri — „keywords" nu trebuie confundat cu
# „key", altfel am tăia parametri legitimi dintr-un URL-sursă. Nu filtrăm după
# forma valorii: un identificator lung și legitim (UUID, nume de strat) ar fi
# șters, iar un link de sursă care nu mai reproduce pagina originală strică
# exact proveniența pe care aplicația o promite.
_SENSITIVE_KEY_WORDS = frozenset({
    "token", "key", "secret", "signature", "sign", "auth", "passwd",
    "password", "credential", "credentials", "session", "sig",
})
_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+")


def _key_words(key):
    """Cuvintele dintr-un nume de parametru: separatoare ȘI camelCase."""
    words = set()
    for part in re.split(r"[^A-Za-z0-9]+", key or ""):
        words.update(word.lower() for word in _WORD_RE.findall(part))
    return words


def _is_sensitive_param(key, value):
    lowered = (key or "").lower()
    return (lowered in _SENSITIVE_QUERY_KEYS
            or bool(_key_words(key) & _SENSITIVE_KEY_WORDS))


def _public_payload(value):
    """Ultima barieră înainte de răspuns: elimină credențiale din URL-uri.

    Conectorii nu trebuie să serializeze URL-uri semnate. Această funcție
    apără însă și împotriva unei viitoare surse care ar întoarce accidental
    un token în query string.
    """
    if isinstance(value, dict):
        return {key: _public_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_public_payload(item) for item in value)
    if not isinstance(value, str) or not value.lower().startswith(("http://", "https://")):
        return value
    try:
        parts = urlsplit(value)
        query = [(key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True)
                 if not _is_sensitive_param(key, item)]
        # user:parolă@host e tot o credențială; netloc-ul se reconstruiește
        # fără ea, nu se copiază ca atare.
        host = parts.hostname or ""
        if ":" in host:                      # IPv6 literal
            host = f"[{host}]"
        netloc = f"{host}:{parts.port}" if parts.port else host
        return urlunsplit((parts.scheme, netloc, parts.path,
                           urlencode(query, doseq=True), parts.fragment))
    except ValueError:
        return value


def api_health(q):
    """Stare a procesului, ieftină și fără efecte.

    Deliberat NU atinge nicio sursă externă și nu declanșează niciun fetch: un
    endpoint de sănătate care poate provoca muncă e o pârghie de amplificare,
    iar unul care așteaptă după un upstream căzut raportează „bolnav" când de
    fapt doar sursa e jos. Citește numai starea locală.
    """
    warm = C.cache_get("warmup_done", max_age=10 ** 9)
    report = C.cache_get(anomalii.REPORT_CACHE_KEY, max_age=10 ** 9)
    now = time.time()

    def age_s(entry):
        age = (entry or {}).get("age")
        return round(age, 1) if isinstance(age, (int, float)) else None

    return {
        "status": "ok",
        "uptime_s": round(now - STARTED_AT, 1),
        "warmup_done": bool(warm),
        "anomaly_report_age_s": age_s(report),
        "generated": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "nota": ("stare locală a procesului; nu interoghează sursele externe. "
                 "Prospețimea fiecărei surse se vede în /api/overview."),
    }


def api_overview(q):
    return C.overview()


# Parametrii din URL determină chei de cache și cereri către sursele oficiale.
# Mărginirea pe interval limitează VALOAREA, nu numărul de valori distincte —
# fiecare valoare acceptată e o cheie de cache nouă și o cerere nouă către
# sursă, deci un interval larg rămâne enumerabil (≈6.000 de chei, sute de MB).
# Repo-ul fiind public, spațiul acela se citește direct din sursă. Acceptăm
# doar liste scurte, care acoperă tot ce cere interfața.
GLOFAS_RECENT_DAYS = frozenset({10, 30, 60, 90, 92})
GLOFAS_START_YEARS = frozenset({1984, 1991, 2000, 2015})
PRECIP_START_YEARS = frozenset({1950, 1991, 2015})
PEGEL_SERIES_DAYS = frozenset({10, 30})
INHGA_SERIES_DAYS = frozenset({30, 90, 92, 365})


def _int_choice(q, name, default, allowed):
    """Acceptă doar valorile din listă; orice altceva e cerere invalidă."""
    raw = q.get(name, [None])[0]
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise BadRequest(f"{name} invalid")
    if value not in allowed:
        raise BadRequest(
            f"{name} acceptă doar: {', '.join(str(v) for v in sorted(allowed))}")
    return value


def api_glofas_recent(q):
    pid = q.get("point", ["bazias"])[0]
    if pid not in C.GLOFAS_POINTS:
        raise BadRequest(f"punct necunoscut: {pid}")
    days = _int_choice(q, "days", 60, GLOFAS_RECENT_DAYS)
    r = C.glofas_recent(pid, past_days=days, forecast_days=7)
    return {"point": C.GLOFAS_POINTS[pid] | {"id": pid}, **r["data"],
            "stale": r["stale"]}


def api_glofas_years(q):
    pid = q.get("point", ["bazias"])[0]
    if pid not in C.GLOFAS_POINTS:
        raise BadRequest(f"punct necunoscut: {pid}")
    start_year = _int_choice(q, "start", 2015, GLOFAS_START_YEARS)
    r = C.glofas_archive(pid, start_year)
    return {"point": C.GLOFAS_POINTS[pid] | {"id": pid}, **r["data"],
            "stale": r["stale"]}


def api_precip(q):
    pid = q.get("point", ["oltenia"])[0]
    if pid not in C.PRECIP_POINTS:
        raise BadRequest(f"punct necunoscut: {pid}")
    start_year = _int_choice(q, "start", 2015, PRECIP_START_YEARS)
    r = C.era5_precip(pid, start_year)
    return {**r["data"], "stale": r["stale"]}


def api_pegel_stations(q):
    r = C.pegelonline_stations()
    return {"stations": r["data"], "stale": r["stale"]}


def api_pegel_series(q):
    uuid = q.get("uuid", [""])[0]
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                        r"[0-9a-f]{4}-[0-9a-f]{12}", uuid):
        raise BadRequest("uuid invalid")
    known = {station.get("uuid") for station in C.pegelonline_stations()["data"]}
    if uuid not in known:
        raise BadRequest("uuid necunoscut pentru Dunăre")
    param = q.get("param", ["W"])[0]
    days = _int_choice(q, "days", 10, PEGEL_SERIES_DAYS)
    r = C.pegelonline_series(uuid, param, days)
    return {**r["data"], "stale": r["stale"]}


def api_afdj(q):
    r = C.afdj_cote()
    return {**r["data"], "stale": r["stale"]}


def api_inhga(q):
    r = C.inhga_bulletin()
    return {**r["data"], "stale": r["stale"],
            "cache_age_s": r.get("cache_age_s")}


def api_inhga_tributaries(q):
    r = C.inhga_danube_tributaries()
    return {**r["data"], "stale": r["stale"],
            "cache_age_s": r.get("cache_age_s")}


def api_danubehis_ro_tributaries(q):
    r = C.danubehis_romanian_tributaries()
    return {**r["data"], "stale": r["stale"],
            "cache_age_s": r.get("cache_age_s")}


def api_glofas_ro_tributaries(q):
    r = C.glofas_romanian_tributary_climatology()
    return {**r["data"], "stale": r["stale"],
            "cache_age_s": r.get("cache_age_s")}


def api_hidmet(q):
    r = C.hidmet_report()
    return {**r["data"], "stale": r["stale"]}


def api_hydroinfo(q):
    r = C.hydroinfo_danube()
    return {**r["data"], "stale": r["stale"]}


def api_danubehis(q):
    r = C.danubehis_danube()
    return {**r["data"], "stale": r["stale"]}


def api_edo(q):
    r = C.edo_status()
    return {**r["data"], "stale": r["stale"]}


def api_edo_map(q):
    kind = q.get("layer", ["cdi"])[0]
    if kind not in C.EDO_MAP_SPECS:
        raise BadRequest("strat EDO necunoscut")
    r = C.edo_map(kind)
    return (base64.b64decode(r["data"]["png_base64"]), "image/png")


def api_entsoe(q):
    # Când integrarea E activă, conectorul întoarce învelișul cached()
    # {data, stale}, iar interfața citește `activ` de la rădăcină — deci ar fi
    # raportat „neactivat" tocmai când funcționează. Același tipar ca api_hydroweb.
    r = C.entsoe_irongates()
    if isinstance(r, dict) and not r.get("activ", True):
        return r
    return {**r["data"], "stale": r["stale"], "cache_age_s": r.get("cache_age_s")}


def api_delta(q):
    """Distribuția pe brațe la intrarea în deltă, din celulele GloFAS —
    validată doar dacă suma brațelor se apropie de total."""
    out = {"puncte": {}, "distributie": None, "stale": False}
    for pid in ("ceatal_izmail", "brat_chilia", "brat_tulcea",
                "brat_sulina", "brat_sf_gheorghe"):
        try:
            r = C.glofas_recent(pid, past_days=10, forecast_days=0)
            out["stale"] = out["stale"] or bool(r.get("stale"))
            latest = C._latest_valid(r["data"]["time"], r["data"]["discharge"])
            out["puncte"][pid] = {
                "name": C.GLOFAS_POINTS[pid]["name"],
                "date": latest[0] if latest else None,
                "discharge_m3s": latest[1] if latest else None,
            }
        except Exception as exc:
            out["puncte"][pid] = {"error": str(exc)}

    tot = out["puncte"].get("ceatal_izmail", {}).get("discharge_m3s")
    chi = out["puncte"].get("brat_chilia", {}).get("discharge_m3s")
    tul = out["puncte"].get("brat_tulcea", {}).get("discharge_m3s")
    # valid doar dacă suma brațelor ≈ totalul ȘI niciun braț nu "fură" tot
    # debitul (rețeaua GloFAS poate ruta întregul fluviu pe un singur braț)
    if (tot and chi and tul and 0.75 <= (chi + tul) / tot <= 1.25
            and 0.15 <= chi / (chi + tul) <= 0.85):
        out["distributie"] = {
            "chilia_pct": round(100 * chi / (chi + tul), 1),
            "tulcea_pct": round(100 * tul / (chi + tul), 1),
            "valid": True,
        }
        sul = out["puncte"].get("brat_sulina", {}).get("discharge_m3s")
        sfg = out["puncte"].get("brat_sf_gheorghe", {}).get("discharge_m3s")
        if sul and sfg and 0.7 <= (sul + sfg) / tul <= 1.3:
            out["distributie"]["sulina_pct"] = round(100 * sul / (chi + tul), 1)
            out["distributie"]["sf_gheorghe_pct"] = round(100 * sfg / (chi + tul), 1)
    return out


def api_low_flow(q):
    """Indici standard de ape mici (MAM7, 7Q10, curba duratei) pe o secțiune."""
    pid = q.get("point", ["bazias"])[0]
    if pid not in C.GLOFAS_POINTS:
        raise BadRequest(f"punct necunoscut: {pid}")
    # 24 h: indicii se schimbă doar când apare o zi nouă în arhivă.
    r = C.cached(f"lowflow:v1:{pid}", 24 * 3600,
                 lambda: anomalii.low_flow_indices(pid))
    return {**r["data"], "stale": r["stale"], "cache_age_s": r.get("cache_age_s")}


def api_anomalii(q):
    r = C.cached(anomalii.REPORT_CACHE_KEY, 6 * 3600, anomalii.report)
    # Vârsta reală a raportului: sinteza din capul paginii o afișa pe cea a
    # ceasului din browser, ceea ce pretindea o prospețime pe care verdictele
    # (TTL 6 h) nu o au.
    return {**r["data"], "stale": r["stale"], "cache_age_s": r.get("cache_age_s")}


def api_inhga_serie(q):
    return {"serie": C.inhga_series(_int_choice(q, "days", 90, INHGA_SERIES_DAYS))}


def _stats_cached():
    return C.cached(anomalii.STATS_CACHE_KEY, 6 * 3600, anomalii.full_stats)


def api_statistici(q):
    r = _stats_cached()
    return {**r["data"], "stale": r["stale"]}


def api_statistici_csv(q):
    d = _stats_cached()["data"]
    rows = ["sectiune;km;data;debit_azi_m3s;normala_zilei_m3s;abatere_pct;"
            "percentila;zile_sub_p10;ani_mai_mici;ani_referinta"]
    for r in d["debit"]:
        rows.append(";".join(str(r.get(k, "")) for k in (
            "name", "km", "data", "azi_m3s", "normala_zilei_m3s", "abatere_pct",
            "percentila", "zile_sub_p10", "ani_mai_mici", "ani_referinta")))
    rows.append("")
    rows.append("zona;pana_la;ian_azi_mm;ian_azi_mediana_mm;ian_azi_abatere_pct;"
                "ian_azi_ani_mai_uscati;iarna_mm;iarna_mediana_mm;"
                "iarna_abatere_pct;iarna_ani_mai_uscati;ult90_mm;ult90_percentila")
    for r in d["precipitatii"]:
        ia, ir, u = r.get("ian_azi") or {}, r.get("iarna") or {}, r.get("ultimele90") or {}
        rows.append(";".join(str(x) for x in (
            r["zona"], r["pana_la"],
            ia.get("cumul_mm", ""), ia.get("mediana_mm", ""), ia.get("abatere_pct", ""),
            ia.get("ani_mai_uscati", ""),
            ir.get("cumul_mm", ""), ir.get("mediana_mm", ""), ir.get("abatere_pct", ""),
            ir.get("ani_mai_uscati", ""),
            u.get("cumul_mm", ""), u.get("pct", ""))))
    return ("﻿" + "\r\n".join(rows), "text/csv; charset=utf-8")


def api_sen(q):
    r = C.sen_live()
    return {**r["data"], "stale": r["stale"]}


def api_sen_history(q):
    return C.sen_history_context()


def api_sen_market(q):
    return C.sen_market_context()


def api_anar_resources(q):
    r = C.anar_water_resources()
    return {**r["data"], "stale": r["stale"]}


def api_romania(q):
    """Testul de proporționalitate, recompus din sursele curente."""
    def optional(fetch, fallback):
        try:
            return fetch()
        except Exception:
            return fallback

    def build():
        stats = _stats_cached()["data"]
        archive = C.glofas_archive("cernavoda", romania.MODEL_START_YEAR)["data"]
        afdj = optional(lambda: C.afdj_cote()["data"], {"statii": []})
        inhga = optional(lambda: C.inhga_bulletin()["data"], {})
        tributaries = optional(lambda: C.inhga_danube_tributaries()["data"], {
            "available": False,
            "reason": "prognoza lunară INHGA nu a putut fi verificată",
        })
        tributary_observations = optional(
            lambda: C.danubehis_romanian_tributaries()["data"], {
                "available": False,
                "reason": "secțiunile românești DanubeHIS nu au putut fi verificate",
            })
        tributary_model_climatology = optional(
            lambda: C.glofas_romanian_tributary_climatology()["data"], {
                "available": False,
                "reason": "climatologia GloFAS a afluenților nu a putut fi verificată",
            })
        sen = optional(lambda: C.sen_live()["data"], {})
        sen_history = optional(C.sen_history_context, {
            "available": False, "enough_for_comparison": False,
            "days": 0, "minimum_days": 14,
        })
        energy_market = optional(C.sen_market_context, {
            "available_components": 0, "component_count": 4,
        })
        water_resources = optional(lambda: C.anar_water_resources()["data"], {
            "available": False, "current": False,
            "reason": "comunicatul național ANAR nu a putut fi verificat",
        })
        snn = optional(C.snn_cernavoda_status, {
            "data": {"status_available": False, "needs_review": False,
                     "status_fresh": False,
                     "reason": "lista oficială SNN nu a răspuns"},
            "stale": True,
        })
        return romania.build_report(stats, archive, afdj, inhga, sen, snn,
                                    tributaries=tributaries,
                                    tributary_observations=tributary_observations,
                                    tributary_model_climatology=tributary_model_climatology,
                                    water_resources=water_resources,
                                    sen_history=sen_history,
                                    energy_market=energy_market)

    # Versiunea cheii urmărește schema payloadului; schimbarea ei împiedică un
    # răspuns vechi din cache să mascheze câmpuri noi după repornire.
    result = C.cached("romania_proportionality:v16", 5 * 60, build)
    return {**result["data"], "stale": result["stale"],
            "cache_age_s": result.get("cache_age_s")}


def api_missing_data(q):
    """Registru public al verigilor utile, disponibilității și căutărilor.

    Este recompus din aceleași payloaduri ca verdictul România. Nu conține
    tokenuri, instrucțiuni interne sau concluzii păstrate manual.
    """
    report = api_romania({})
    water = report.get("water_resources") or {}
    reservoirs = water.get("reservoirs") or {}
    tributaries = report.get("romanian_tributaries") or {}
    observed = tributaries.get("observed_sections") or {}
    energy_history = (report.get("energy") or {}).get("history") or {}
    energy_market = (report.get("energy") or {}).get("market") or {}
    energy = report.get("energy") or {}
    bulletin = report.get("official_danube_bulletin") or {}
    transparency = ((report.get("cernavoda") or {})
                    .get("parameter_transparency") or {})

    try:
        satellite = C.earthdata_satellite_catalog()["data"]
    except Exception:
        satellite = {"sources": {}, "download_configurat": False}
    try:
        land = C.copernicus_land_context()["data"]
    except Exception:
        land = {"straturi": {}}
    # Aceeași tratare ca vecinii de mai sus: un fișier GRDC malformat sau un
    # ENTSO-E căzut nu are voie să dea 500 pe tot registrul „Date lipsă".
    try:
        grdc = C.grdc_series()
    except Exception:
        grdc = {"activ": False, "motiv": "seria GRDC nu a putut fi citită acum"}
    try:
        entsoe = C.entsoe_irongates()
    except Exception:
        entsoe = {"activ": False}

    reservoir_complete = (water.get("current") and
                          reservoirs.get("fill_pct") is not None and
                          reservoirs.get("volume_billion_m3") is not None)
    reservoir_status = "available" if reservoir_complete else (
        "partial" if water.get("current") else "missing")
    sections_available = int(observed.get("sections_available") or
                             len(observed.get("sections") or []))
    # Ținta e numărul de sisteme măsurate urmărite, nu numărul (variabil) de
    # bazine parsate din buletinul lunar INHGA.
    systems_selected = int(observed.get("measured_systems_target")
                           or (len(C.DANUBEHIS_RO_TRIBUTARY_SECTIONS)
                               + len(C.DANUBEHIS_RO_TRIBUTARY_MISSING)))
    # `[] or fallback` ar întoarce fallback-ul: o listă goală înseamnă „niciun
    # sistem nu lipsește", nu „nu știu".
    missing_declared = observed.get("missing_systems")
    systems_missing = list(missing_declared if missing_declared is not None
                           else C.DANUBEHIS_RO_TRIBUTARY_MISSING)
    decision_missing = [item for item in transparency.get("decision_parameters") or []
                        if item.get("status") != "available"]
    catalog_sources = satellite.get("sources") or {}
    soil_catalog = [catalog_sources.get(key) for key in ("smap", "nisar")
                    if catalog_sources.get(key)]
    land_soil = (land.get("straturi") or {}).get("soil") or {}

    entries = [
        {
            "id": "anar_reservoirs", "category": "România · resurse de apă",
            "title": "Acumulări și restricții naționale",
            "status": reservoir_status,
            "need": "coeficient de umplere, volum util și restricții într-o serie națională datată",
            "why": "separă un deficit al Dunării de o penurie uniformă a resurselor din România",
            "what_we_have": ({
                "published": water.get("published"), "age_days": water.get("age_days"),
                "reservoir_count": reservoirs.get("count"),
                "fill_pct": reservoirs.get("fill_pct"),
                "volume_billion_m3": reservoirs.get("volume_billion_m3"),
                "sufficient_for_centralized_supply": reservoirs.get("sufficient_for_centralized_supply"),
                "drinking_water_restrictions": (water.get("restrictions") or {}).get("drinking_water"),
            } if water.get("available") else None),
            "gap": ("comunicatul curent este util calitativ, dar câmpurile numerice nepublicate rămân lipsă"
                    if water.get("current") else
                    "nu există în flux un comunicat național suficient de recent"),
            "sources_checked": [
                {"label": "ANAR — ultimul comunicat relevant", "url": water.get("url")},
                {"label": "ANAR — Situația hidrologică lacuri", "url": C.ANAR_MANAGEMENT_URL},
            ],
        },
        {
            "id": "cernavoda_decision", "category": "Cernavodă · decizie tehnică",
            "title": "Parametrii care declanșează reducerea sau oprirea",
            "status": "available" if transparency.get("decision_reproducible") else "partial",
            "need": "nivel bazin aspirație și praguri curente pe aceeași cotă de referință, plus condițiile hidraulice ale pompelor",
            "why": "permite verificarea independentă a deciziei, nu doar confirmarea efectului publicat de operator",
            "what_we_have": {
                "public_signals": len([x for x in transparency.get("public_signals") or []
                                       if x.get("value") is not None]),
                "decision_reproducible": bool(transparency.get("decision_reproducible")),
            },
            "gap": "; ".join(item.get("name", "parametru neidentificat")
                              for item in decision_missing),
            "sources_checked": [
                {"label": "SNN — rapoarte curente", "url": C.SNN_CURRENT_REPORTS_URL},
                {"label": "SNN — praguri publicate în 2011",
                 "url": (transparency.get("historical_2011") or {}).get("source", {}).get("url")},
            ],
        },
        {
            "id": "cernavoda_bala", "category": "Cernavodă · hidraulică locală",
            "title": "Împărțirea debitului și intervențiile din zona Bala",
            "status": "partial" if (bulletin.get("date") or
                                      (report.get("cernavoda") or {}).get("gauge")) else "missing",
            "need": "debite sincronizate pe Dunărea Veche/Borcea/Bala, geometria secțiunilor și starea intervențiilor",
            "why": "INHGA avertizează că intervențiile pot modifica prognoza de nivel la Cernavodă, exact secțiunea relevantă pentru contextul CNE",
            "what_we_have": {
                "bulletin_date": bulletin.get("date"),
                "official_caveat_detected": bool(bulletin.get("cernavoda_bala_caveat")),
                "cernavoda_gauge_date": (((report.get("cernavoda") or {}).get("gauge") or {})
                                         .get("actualizat")),
            },
            "gap": ("avertismentul oficial și cota la miră nu cuantifică distribuția "
                    "debitului, efectul lucrărilor sau nivelul bazinului de aspirație"),
            "sources_checked": [
                {"label": "INHGA — buletinul curent al Dunării", "url": bulletin.get("url")},
                {"label": "AFDJ — cotele Dunării", "url": romania.AFDJ_CURRENT_LEVELS_URL},
            ],
        },
        {
            "id": "sen_history", "category": "Energie · proporționalitate",
            "title": "Consum, rezerve contractate, echilibrare și prețuri",
            "status": ("partial" if (energy_history.get("available") or
                                      energy_market.get("available_components")) else "missing"),
            "need": "cerere, import/export, producție, rezerve și prețuri înainte și după episod",
            "why": "o unitate nucleară oprită este materială, dar nu dovedește singură o criză energetică națională",
            "what_we_have": {
                "local_days": energy_history.get("days", 0),
                "minimum_days": energy_history.get("minimum_days", 14),
                "enough_for_comparison": bool(energy_history.get("enough_for_comparison")),
                "from": energy_history.get("from"), "to": energy_history.get("to"),
                "official_components": energy_market.get("available_components", 0),
                "consumption_date": (energy_market.get("consumption") or {}).get("delivery_date"),
                "reserve_date": (energy_market.get("reserve_procurement") or {}).get("delivery_date"),
                "day_ahead_delivery": (energy_market.get("day_ahead") or {}).get("delivery_date"),
            },
            "gap": ("consumul zilnic DAMAS, rezultatele achiziției de capacitate, "
                    "echilibrarea și PZU sunt ingerate când sursele răspund; lipsesc "
                    "încă marja operațională de rezervă rămasă, un baseline istoric "
                    "comparabil și eventualele măsuri de urgență"),
            "sources_checked": (energy_history.get("sources") or [
                {"label": "Transelectrica — Rapoarte zilnice", "url": C.SEN_DAILY_REPORTS_URL},
                {"label": "Transelectrica — DAMAS II Public Reports", "url": C.SEN_DAMAS_REPORTS_URL},
            ]) + [{"label": "OPCOM — rezultate PZU", "url": C.OPCOM_URL}],
        },
        {
            "id": "irongates_operations", "category": "Porțile de Fier · operare",
            "title": "Debite intrare/ieșire, stocare, turbinare și deversare",
            "status": "partial" if (entsoe.get("activ") or
                                      energy.get("hydro_mw") is not None) else "missing",
            "need": "serii sincronizate de intrare/ieșire, nivel sau volum al lacului, turbinare, deversare și manevre",
            "why": "separă efectul natural al aportului de operarea amenajării și permite verificarea bilanțului fizic",
            "what_we_have": {
                "national_hydro_mw": energy.get("hydro_mw"),
                "entsoe_unit_generation_active": bool(entsoe.get("activ")),
                "glofas_balance_is_model_only": True,
            },
            "gap": ("producția energetică este doar proxy; nu avem fluxul hidrologic "
                    "operațional, stocarea și jurnalele de manevră ale operatorilor român și sârb"),
            "sources_checked": [
                {"label": "Hidroelectrica", "url": "https://www.hidroelectrica.ro/"},
                {"label": "EPS Serbia", "url": "https://www.eps.rs/eng/"},
                {"label": "ENTSO-E Transparency", "url": "https://transparency.entsoe.eu"},
            ],
        },
        {
            "id": "tributary_gauges", "category": "România · afluenți",
            "title": "Debite măsurate aproape de confluențe",
            # Cât timp există sisteme fără secțiune publică, lacuna NU e închisă,
            # oricâte secțiuni ar livra DanubeHIS.
            "status": ("available"
                       if sections_available >= systems_selected and not systems_missing
                       else "partial"),
            "need": "debit măsurat comparabil pentru cele nouă sisteme selectate, cât mai aproape de Dunăre",
            "why": "arată ce a intrat efectiv în sectorul românesc, separat de prognoză și model",
            "what_we_have": {"measured_sections": sections_available,
                             "measured_systems_target": systems_selected,
                             "missing_systems": systems_missing},
            "gap": observed.get("limit") or "secțiunile disponibile au acoperire parțială",
            "sources_checked": [{"label": observed.get("provider") or "DanubeHIS / NIHWM",
                                 "url": observed.get("source_url")}],
        },
        {
            "id": "soil_moisture", "category": "Satelit · agricultură",
            "title": "Umiditate a solului validată spațial",
            "status": "partial" if land_soil.get("activ") else "missing",
            "need": "valori spațiale și indicatori de calitate pentru România, nu doar existența unei granule sau o miniatură",
            "why": "verifică dacă semnalul hidrologic se extinde la seceta agricolă și localizează contradicțiile",
            "what_we_have": {
                "copernicus_observation": land_soil.get("data"),
                "copernicus_mode": "context_map" if land_soil.get("activ") else None,
                "earthdata_catalogs": [item.get("title") for item in soil_catalog],
                "downloads_configured": bool(satellite.get("download_configurat")),
            },
            "gap": "Copernicus Land este context vizual; SMAP și NISAR sunt doar catalogate până la configurarea și validarea fișierelor",
            "sources_checked": [
                {"label": "Copernicus Land / CDSE", "url": "https://land.copernicus.eu/en/products/soil-moisture"},
                {"label": "NASA Earthdata CMR", "url": "https://cmr.earthdata.nasa.gov/search/"},
            ],
        },
        {
            "id": "optional_backfill", "category": "Istoric · verificare secundară",
            "title": "GRDC și produse orbitale directe",
            "status": "partial" if grdc.get("activ") or satellite.get("download_configurat") else "missing",
            "need": "backfill măsurat sau operațional care aduce o familie de probă nouă",
            "why": "poate calibra modelul și reconstrui episoade vechi, dar nu înlocuiește datele CNE lipsă",
            "what_we_have": {"grdc_active": bool(grdc.get("activ")),
                             "satellite_downloads_configured": bool(satellite.get("download_configurat"))},
            "gap": "se activează numai după obținerea legală a exportului sau configurarea descărcării și după un test de valoare informațională",
            "sources_checked": [
                {"label": "GRDC — portal date", "url": "https://portal.grdc.bafg.de"},
                {"label": "NASA Earthdata", "url": "https://search.earthdata.nasa.gov"},
            ],
        },
    ]
    counts = {status: sum(1 for item in entries if item["status"] == status)
              for status in ("available", "partial", "missing")}
    return {
        "generated": date.today().isoformat(),
        # Registrul se compune din raportul România; dacă acela vine din cache,
        # spunem asta în loc să lăsăm pagina să pară proaspătă necondiționat.
        "stale": bool(report.get("stale")),
        "source_generated": report.get("generated"),
        "entries": entries, "summary": counts,
        "rule": "O sursă intră în verdict numai dacă aduce o valoare comparabilă, datată și cu proveniență; catalogul sau linkul singur nu este observație.",
    }


def api_danubeportal(q):
    r = C.danubeportal_gauges()
    return {**r["data"], "stale": r["stale"]}


def api_dahiti(q):
    r = C.dahiti_danube()
    if isinstance(r, dict) and not r.get("activ", True):
        return r
    return {**r["data"], "stale": r["stale"], "cache_age_s": r.get("cache_age_s")}


def api_hydroweb(q):
    r = C.hydroweb_danube()
    if isinstance(r, dict) and not r.get("activ", True):
        return r
    return {**r["data"], "stale": r["stale"]}


def api_opera(q):
    r = C.opera_surface_status()
    return {**r["data"], "stale": r["stale"]}


def api_opera_map(q):
    kind = q.get("layer", ["sentinel1"])[0]
    zone = q.get("zone", ["dunarea_de_jos"])[0]
    if kind not in C.OPERA_LAYERS or zone not in C.OPERA_ZONES:
        raise BadRequest("strat sau zonă OPERA necunoscută")
    return (C.opera_surface_map(kind, zone), "image/png")


def api_copernicus_land(q):
    r = C.copernicus_land_context()
    return {**r["data"], "stale": r["stale"]}


def api_copernicus_land_map(q):
    kind = q.get("layer", ["snow"])[0]
    if kind not in C.CDSE_CONTEXT:
        raise BadRequest("strat Copernicus necunoscut")
    return (C.copernicus_land_map(kind), "image/png")


def api_satellite_catalog(q):
    r = C.earthdata_satellite_catalog()
    return {**r["data"], "stale": r["stale"]}


def api_evidence_sources(q):
    return C.evidence_source_registry()


def api_gravimetrie(q):
    r = C.hydroweb_gravimetry()
    if isinstance(r, dict) and not r.get("activ", True):
        return r
    return {**r["data"], "stale": r["stale"]}


def api_grdc(q):
    return anomalii.grdc_context()


def api_bilant_apa(q):
    r = C.cached(anomalii.BUDGET_CACHE_KEY, 6 * 3600, anomalii.water_budget)
    return {**r["data"], "stale": r["stale"]}


def api_raport(q):
    """Snapshot complet, descărcabil: toate verdictele + intrările lor, cu
    marcaj de timp — proba arhivabilă a oricărei afirmații din aplicație."""
    import datetime
    out = {"generat_utc": datetime.datetime.now(datetime.timezone.utc)
           .isoformat(timespec="seconds"),
           "aplicatie": "Monitor Dunărea — surse oficiale",
           "sectiuni": {}}
    for nume, fn in (("anomalii", lambda: C.cached(anomalii.REPORT_CACHE_KEY, 6 * 3600, anomalii.report)["data"]),
                     ("statistici", lambda: _stats_cached()["data"]),
                     ("bilant_apa", lambda: C.cached(anomalii.BUDGET_CACHE_KEY, 6 * 3600, anomalii.water_budget)["data"]),
                     ("inhga", lambda: C.inhga_bulletin()["data"]),
                     ("inhga_afluenti_dunare", lambda: C.inhga_danube_tributaries()["data"]),
                     ("danubehis_afluenti_romania", lambda: C.danubehis_romanian_tributaries()["data"]),
                     ("glofas_climatologie_afluenti_romania", lambda: C.glofas_romanian_tributary_climatology()["data"]),
                     ("afdj", lambda: C.afdj_cote()["data"]),
                     ("hidmet", lambda: C.hidmet_report()["data"]),
                     ("hydroinfo", lambda: C.hydroinfo_danube()["data"]),
                     ("danubehis", lambda: C.danubehis_danube()["data"]),
                     ("edo", lambda: C.edo_status()["data"]),
                     ("opera", lambda: C.opera_surface_status()["data"]),
                     ("copernicus_land", lambda: C.copernicus_land_context()["data"]),
                     ("catalog_sateliti", lambda: C.earthdata_satellite_catalog()["data"]),
                     ("registru_provenienta", C.evidence_source_registry),
                     ("sen", lambda: C.sen_live()["data"]),
                     ("sen_istoric_local", C.sen_history_context),
                     ("sen_piata", C.sen_market_context),
                     ("anar_resurse_apa", lambda: C.anar_water_resources()["data"]),
                     ("date_lipsa", lambda: api_missing_data({})),
                     ("romania", lambda: api_romania({}))):
        try:
            out["sectiuni"][nume] = fn()
        except Exception as exc:
            out["sectiuni"][nume] = {"eroare": str(exc)}
    return out


def api_points(q):
    return {
        "glofas": [{"id": k, **v} for k, v in C.GLOFAS_POINTS.items()],
        "precip": [{"id": k, **v} for k, v in C.PRECIP_POINTS.items()],
    }


ROUTES = {
    "/api/health": api_health,
    "/api/overview": api_overview,
    "/api/glofas/recent": api_glofas_recent,
    "/api/glofas/years": api_glofas_years,
    "/api/precip": api_precip,
    "/api/pegel/stations": api_pegel_stations,
    "/api/pegel/series": api_pegel_series,
    "/api/afdj": api_afdj,
    "/api/inhga": api_inhga,
    "/api/inhga/afluenti-dunare": api_inhga_tributaries,
    "/api/danubehis/afluenti-romania": api_danubehis_ro_tributaries,
    "/api/glofas/afluenti-romania": api_glofas_ro_tributaries,
    "/api/hidmet": api_hidmet,
    "/api/hydroinfo": api_hydroinfo,
    "/api/danubehis": api_danubehis,
    "/api/edo": api_edo,
    "/api/edo/map": api_edo_map,
    "/api/entsoe": api_entsoe,
    "/api/delta": api_delta,
    "/api/points": api_points,
    "/api/anomalii": api_anomalii,
    "/api/ape-mici": api_low_flow,
    "/api/inhga/serie": api_inhga_serie,
    "/api/statistici": api_statistici,
    "/api/statistici.csv": api_statistici_csv,
    "/api/sen": api_sen,
    "/api/sen/istoric": api_sen_history,
    "/api/sen/piata": api_sen_market,
    "/api/anar/resurse-apa": api_anar_resources,
    "/api/romania": api_romania,
    "/api/date-lipsa": api_missing_data,
    "/api/danubeportal": api_danubeportal,
    "/api/dahiti": api_dahiti,
    "/api/hydroweb": api_hydroweb,
    "/api/opera": api_opera,
    "/api/opera/map": api_opera_map,
    "/api/copernicus-land": api_copernicus_land,
    "/api/copernicus-land/map": api_copernicus_land_map,
    "/api/satellite-catalog": api_satellite_catalog,
    "/api/evidence-sources": api_evidence_sources,
    "/api/gravimetrie": api_gravimetrie,
    "/api/grdc": api_grdc,
    "/api/bilant-apa": api_bilant_apa,
    "/api/raport": api_raport,
    "/api/istoric": lambda q: C.history_status(),
    "/api/avize": lambda q: api_avize(q),
    "/api/analiza-ai": lambda q: api_analiza_ai(q),
}


def api_avize(q):
    r = C.danubeportal_avize()
    return {**r["data"], "stale": r["stale"]}


def api_analiza_ai(q):
    # Endpoint de stare, fără efecte: nici măcar ?run=1 nu poate porni un apel
    # plătit din exterior. Rularea manuală se face cu `python3 analiza_ai.py`.
    return analiza_ai.analiza(run=False)


# `style-src 'self'` fără 'unsafe-inline': markup-ul nu mai conține niciun
# atribut style=, iar valorile calculate din date se aplică prin CSSOM
# (applyDataStyles în app.js), care nu intră sub această directivă.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
       "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'")

PAGE_PATHS = {"/", "/romania", "/bazin", "/integritate", "/sectoare", "/optiuni",
              "/date-lipsa"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DanubeMonitor"
    sys_version = ""
    timeout = 30  # un client lent nu blochează un fir la nesfârșit
    # Anteturile și corpul pleacă în două write-uri separate. Cu algoritmul
    # Nagle activ (implicitul stdlib), al doilea segment așteaptă ACK-ul
    # primului, iar ACK-ul întârziat al clientului adaugă ~40 ms fiecărui
    # răspuns mic — adică exact răspunsurilor JSON pe care le dăm cel mai des.
    disable_nagle_algorithm = True

    def version_string(self):
        return self.server_version

    def log_message(self, fmt, *args):
        pass  # jurnalul propriu, structurat, se scrie în _log_request

    def handle_one_request(self):
        self._started_at = time.monotonic()
        super().handle_one_request()

    def _log_request(self, code, started_at):
        """Jurnal structurat, o linie pe cerere.

        Tăcerea totală de dinainte însemna că o defecțiune în producție nu lăsa
        nicio urmă. Ținem consola curată prin selecție, nu prin mutism: erorile
        și cererile lente se scriu întotdeauna, restul numai dacă operatorul
        cere explicit un access log complet.
        """
        elapsed_ms = (time.monotonic() - started_at) * 1000 if started_at else 0.0
        interesting = code >= 400 or elapsed_ms >= SLOW_REQUEST_MS
        if not (ACCESS_LOG or interesting):
            return
        # Calea se ia din request, nu din vreo valoare reflectată, și se
        # trunchiază: un URI lung nu are voie să umple jurnalul.
        path = (self.path or "")[:200].replace("\n", " ").replace("\r", " ")
        level = "eroare" if code >= 500 else "atentie" if interesting else "info"
        print(f'{datetime.datetime.now(datetime.timezone.utc).isoformat()} '
              f'{level} {self.command} {path} {code} {elapsed_ms:.0f}ms',
              file=sys.stderr, flush=True)

    def send_response(self, code, message=None):
        """Anteturile de securitate se emit aici, nu în _send.

        `send_error` din stdlib (501 metodă necunoscută, 505 versiune, 414 URI
        prea lung, 400 sintaxă) își construiește singur răspunsul HTML și nu
        trece prin _send — fără această suprascriere, exact acele răspunsuri
        plecau fără CSP, fără nosniff și fără Referrer-Policy, contrazicând
        afirmația din DEPLOY.md.
        """
        super().send_response(code, message)
        self._log_request(code, getattr(self, "_started_at", None))
        # a doua linie de apărare: chiar dacă un text extern ar scăpa
        # neescapat în pagină, CSP-ul îi interzice execuția
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")

    def _send(self, code, body, ctype="application/json; charset=utf-8",
              head_only=False, cache_control="no-store", extra_headers=None):
        public = body if isinstance(body, bytes) else _public_payload(body)
        data = public if isinstance(public, bytes) else json.dumps(public).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if head_only:
            return
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def _has_body(self):
        """Aplicația e numai-citire: nicio rută nu citește un corp de cerere.

        Lăsat necitit pe o conexiune keep-alive, corpul e interpretat de stdlib
        drept următoarea cerere — un singur POST devine zeci de răspunsuri și
        ocolește complet limitarea de rată din nginx. Închidem conexiunea în
        loc să ne bazăm pe proxy pentru propria încadrare a mesajelor.
        """
        return bool(self.headers.get("Content-Length")
                    or self.headers.get("Transfer-Encoding"))

    def do_HEAD(self):
        self.do_GET(head_only=True)

    def _method_not_allowed(self):
        self.close_connection = True
        self._send(405, {"error": "method not allowed"},
                   extra_headers={"Allow": "GET, HEAD", "Connection": "close"})

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    def do_GET(self, head_only=False):
        if self._has_body():
            self.close_connection = True
            self._send(400, {"error": "corp de cerere neacceptat"},
                       head_only=head_only,
                       extra_headers={"Connection": "close"})
            return
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ROUTES:
            try:
                res = ROUTES[path](parse_qs(parsed.query))
                if isinstance(res, tuple):  # (payload, content-type), CSV/PNG
                    payload = (res[0] if isinstance(res[0], bytes)
                               else res[0].encode("utf-8"))
                    self._send(200, payload, res[1], head_only)
                else:
                    self._send(200, res, head_only=head_only)
            except BadRequest as exc:
                self._send(400, {"error": str(exc)}, head_only=head_only)
            except Exception:
                # detaliile în log, nu la client (pot conține căi interne)
                traceback.print_exc()
                self._send(502, {"error": "sursa de date nu a răspuns; "
                                          "reîncercați în câteva minute"},
                           head_only=head_only)
            return

        # fișiere statice — doar tipurile cunoscute, doar din static/
        if path in PAGE_PATHS:
            path = "/index.html"
        try:
            fpath = os.path.realpath(os.path.join(STATIC_DIR, path.lstrip("/")))
            ext = os.path.splitext(fpath)[1]
            inside = os.path.commonpath([fpath, STATIC_DIR]) == STATIC_DIR
        except ValueError:
            # ex. octet NUL în cale — cerere invalidă, nu eroare de server
            self._send(404, {"error": "not found"}, head_only=head_only)
            return
        if not inside or ext not in MIME or not os.path.isfile(fpath):
            self._send(404, {"error": "not found"}, head_only=head_only)
            return
        with open(fpath, "rb") as fh:
            cache_control = ("public, max-age=2592000, immutable"
                             if path.startswith("/vendor/") else "no-cache")
            self._send(200, fh.read(), MIME[ext], head_only,
                       cache_control=cache_control)


def warmup():
    """Pre-încălzește cache-ul în fundal ca prima încărcare să fie rapidă.
    Prima rulare face și "snap"-ul celulelor GloFAS (multe cereri mici),
    de aceea rulează pe mai multe fire.

    Nu se repetă la fiecare repornire: dacă a rulat în ultimele 6 ore,
    se sare — altfel un supervisor care repornește în buclă ar bombarda
    sursele oficiale."""
    from concurrent.futures import ThreadPoolExecutor

    if C.cache_get("warmup_done", max_age=6 * 3600):
        print("warmup sărit (rulat recent)")
        return

    jobs = [C.pegelonline_stations, C.inhga_bulletin,
            C.inhga_danube_tributaries, C.danubehis_romanian_tributaries,
            C.glofas_romanian_tributary_climatology,
            C.anar_water_resources,
            C.hidmet_report,
            C.hydroinfo_danube, C.danubehis_danube, C.edo_status,
            C.copernicus_land_context, C.earthdata_satellite_catalog]
    jobs += [lambda p=pid: C.glofas_recent(p, past_days=10, forecast_days=3)
             for pid in C.GLOFAS_POINTS]

    def safe(fn):
        """Rulează izolat și spune dacă a reușit — apelantul poate decide."""
        try:
            fn()
            return True
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=6) as pool:
        pool.map(safe, jobs)
    print("cache pre-încălzit")

    # arhiva buletinelor INHGA (o singură dată; apoi doar ziua curentă)
    safe(lambda: C.inhga_backfill(days=90))
    # raportul de anomalii cere arhive lungi — îl pre-calculăm tot aici
    ok_report = safe(lambda: C.cached(anomalii.REPORT_CACHE_KEY, 6 * 3600,
                                      anomalii.report))
    # Statisticile și bilanțul erau lăsate pe seama primei cereri: pe cache rece
    # ajungeau într-un fir de cerere și puteau depăși proxy_read_timeout.
    safe(lambda: C.cached(anomalii.STATS_CACHE_KEY, 6 * 3600, anomalii.full_stats))
    safe(lambda: C.cached(anomalii.BUDGET_CACHE_KEY, 6 * 3600, anomalii.water_budget))
    safe(C.cache_gc)
    # `safe` înghite orice excepție: marcarea necondiționată însemna că, dacă
    # rețeaua a fost jos tot warmup-ul, fiecare repornire îl sărea 6 ore și
    # /api/health raporta warmup_done peste un cache gol.
    if ok_report:
        C.cache_put("warmup_done", True, 6 * 3600)
    else:
        print("warmup incomplet — NU marchez warmup_done")
    print("istoric INHGA + raport anomalii pregătite")


def maintenance_watcher():
    """Ține sursa INHGA la zi și curăță cache-ul; nu rulează analiza AI."""
    import time as _t
    n = 0
    while True:
        _t.sleep(1800)
        n += 1
        try:
            # Buletinul INHGA apare o dată pe zi, dar TTL-ul lui e de 30 min —
            # exact cadența acestei bucle, deci îl reîncărcam de 48 de ori pe zi
            # indiferent de trafic. Suntem oaspeți pe API-urile oficiale: îl
            # reîmprospătăm doar cât timp buletinul zilei încă nu a apărut.
            bul = C.cache_get(C.INHGA_CACHE_KEY, max_age=10 ** 9)
            azi = date.today().isoformat()
            if not bul or (bul.get("data") or {}).get("data_buletin") != azi:
                C.inhga_bulletin()   # ține seria oficială la zi fără repornire
            # Surse cu cadență lunară/săptămânală: o dată pe zi e suficient.
            if n % 48 == 0:
                C.inhga_danube_tributaries()
                C.danubehis_romanian_tributaries()
                C.anar_water_resources()
                C.glofas_romanian_tributary_climatology()
                C.cache_gc()
        except Exception:
            pass


if __name__ == "__main__":
    cleaned = C.scrub_sensitive_cache()
    if cleaned:
        print(f"cache HydroWeb igienizat: {cleaned} rânduri")
    threading.Thread(target=warmup, daemon=True).start()
    threading.Thread(target=maintenance_watcher, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Monitor Dunărea → http://localhost:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
