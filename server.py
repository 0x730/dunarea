#!/usr/bin/env python3
"""Monitor Dunărea — server local.

Pornire:  python3 server.py   (implicit http://localhost:7300)
Opțional: PORT=8080 python3 server.py
          ENTSOE_TOKEN=... python3 server.py   (activează datele PF I/II)
"""

import json
import os
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import anomalii
import connectors as C

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = int(os.environ.get("PORT", "7300"))

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def api_overview(q):
    return C.overview()


def api_glofas_recent(q):
    pid = q.get("point", ["bazias"])[0]
    if pid not in C.GLOFAS_POINTS:
        raise KeyError(f"punct necunoscut: {pid}")
    days = int(q.get("days", ["60"])[0])
    r = C.glofas_recent(pid, past_days=min(days, 92), forecast_days=7)
    return {"point": C.GLOFAS_POINTS[pid] | {"id": pid}, **r["data"],
            "stale": r["stale"]}


def api_glofas_years(q):
    pid = q.get("point", ["bazias"])[0]
    if pid not in C.GLOFAS_POINTS:
        raise KeyError(f"punct necunoscut: {pid}")
    start_year = max(1984, int(q.get("start", ["2015"])[0]))
    r = C.glofas_archive(pid, start_year)
    return {"point": C.GLOFAS_POINTS[pid] | {"id": pid}, **r["data"],
            "stale": r["stale"]}


def api_precip(q):
    pid = q.get("point", ["oltenia"])[0]
    if pid not in C.PRECIP_POINTS:
        raise KeyError(f"punct necunoscut: {pid}")
    start_year = max(1950, int(q.get("start", ["2015"])[0]))
    r = C.era5_precip(pid, start_year)
    return {**r["data"], "stale": r["stale"]}


def api_pegel_stations(q):
    r = C.pegelonline_stations()
    return {"stations": r["data"], "stale": r["stale"]}


def api_pegel_series(q):
    uuid = q.get("uuid", [""])[0]
    if not re.fullmatch(r"[0-9a-f-]{36}", uuid):
        raise KeyError("uuid invalid")
    param = q.get("param", ["W"])[0]
    days = int(q.get("days", ["10"])[0])
    r = C.pegelonline_series(uuid, param, days)
    return {**r["data"], "stale": r["stale"]}


def api_afdj(q):
    r = C.afdj_cote()
    return {**r["data"], "stale": r["stale"]}


def api_inhga(q):
    r = C.inhga_bulletin()
    return {**r["data"], "stale": r["stale"]}


def api_hidmet(q):
    r = C.hidmet_report()
    return {**r["data"], "stale": r["stale"]}


def api_entsoe(q):
    return C.entsoe_irongates() if os.environ.get("ENTSOE_TOKEN") \
        else C.entsoe_irongates()


def api_delta(q):
    """Distribuția pe brațe la intrarea în deltă, din celulele GloFAS —
    validată doar dacă suma brațelor se apropie de total."""
    out = {"puncte": {}, "distributie": None}
    for pid in ("ceatal_izmail", "brat_chilia", "brat_tulcea",
                "brat_sulina", "brat_sf_gheorghe"):
        try:
            r = C.glofas_recent(pid, past_days=10, forecast_days=0)
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


def api_anomalii(q):
    r = C.cached("anomalii_report", 6 * 3600, anomalii.report)
    return {**r["data"], "stale": r["stale"]}


def api_inhga_serie(q):
    days = min(int(q.get("days", ["90"])[0]), 365)
    return {"serie": C.inhga_series(days)}


def _stats_cached():
    return C.cached("statistici", 6 * 3600, anomalii.full_stats)


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


def api_danubeportal(q):
    r = C.danubeportal_gauges()
    return {**r["data"], "stale": r["stale"]}


def api_dahiti(q):
    return C.dahiti_danube()


def api_hydroweb(q):
    r = C.hydroweb_danube()
    if isinstance(r, dict) and not r.get("activ", True):
        return r
    return {**r["data"], "stale": r["stale"]}


def api_gravimetrie(q):
    r = C.hydroweb_gravimetry()
    if isinstance(r, dict) and not r.get("activ", True):
        return r
    return {**r["data"], "stale": r["stale"]}


def api_grdc(q):
    return anomalii.grdc_context()


def api_bilant_apa(q):
    r = C.cached("bilant_apa", 6 * 3600, anomalii.water_budget)
    return {**r["data"], "stale": r["stale"]}


def api_raport(q):
    """Snapshot complet, descărcabil: toate verdictele + intrările lor, cu
    marcaj de timp — proba arhivabilă a oricărei afirmații din aplicație."""
    import datetime
    out = {"generat_utc": datetime.datetime.now(datetime.timezone.utc)
           .isoformat(timespec="seconds"),
           "aplicatie": "Monitor Dunărea — surse oficiale",
           "sectiuni": {}}
    for nume, fn in (("anomalii", lambda: C.cached("anomalii_report", 6 * 3600, anomalii.report)["data"]),
                     ("statistici", lambda: _stats_cached()["data"]),
                     ("bilant_apa", lambda: C.cached("bilant_apa", 6 * 3600, anomalii.water_budget)["data"]),
                     ("inhga", lambda: C.inhga_bulletin()["data"]),
                     ("afdj", lambda: C.afdj_cote()["data"]),
                     ("hidmet", lambda: C.hidmet_report()["data"]),
                     ("sen", lambda: C.sen_live()["data"])):
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
    "/api/overview": api_overview,
    "/api/glofas/recent": api_glofas_recent,
    "/api/glofas/years": api_glofas_years,
    "/api/precip": api_precip,
    "/api/pegel/stations": api_pegel_stations,
    "/api/pegel/series": api_pegel_series,
    "/api/afdj": api_afdj,
    "/api/inhga": api_inhga,
    "/api/hidmet": api_hidmet,
    "/api/entsoe": api_entsoe,
    "/api/delta": api_delta,
    "/api/points": api_points,
    "/api/anomalii": api_anomalii,
    "/api/inhga/serie": api_inhga_serie,
    "/api/statistici": api_statistici,
    "/api/statistici.csv": api_statistici_csv,
    "/api/sen": api_sen,
    "/api/danubeportal": api_danubeportal,
    "/api/dahiti": api_dahiti,
    "/api/hydroweb": api_hydroweb,
    "/api/gravimetrie": api_gravimetrie,
    "/api/grdc": api_grdc,
    "/api/bilant-apa": api_bilant_apa,
    "/api/raport": api_raport,
    "/api/istoric": lambda q: C.history_status(),
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # liniște în consolă

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ROUTES:
            try:
                res = ROUTES[path](parse_qs(parsed.query))
                if isinstance(res, tuple):  # (text, content-type), ex. CSV
                    self._send(200, res[0].encode("utf-8"), res[1])
                else:
                    self._send(200, res)
            except Exception as exc:
                traceback.print_exc()
                self._send(502, {"error": str(exc)})
            return

        # fișiere statice
        if path == "/":
            path = "/index.html"
        fpath = os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if not fpath.startswith(STATIC_DIR) or not os.path.isfile(fpath):
            self._send(404, {"error": "not found"})
            return
        ext = os.path.splitext(fpath)[1]
        with open(fpath, "rb") as fh:
            self._send(200, fh.read(), MIME.get(ext, "application/octet-stream"))


def warmup():
    """Pre-încălzește cache-ul în fundal ca prima încărcare să fie rapidă.
    Prima rulare face și "snap"-ul celulelor GloFAS (multe cereri mici),
    de aceea rulează pe mai multe fire."""
    from concurrent.futures import ThreadPoolExecutor

    jobs = [C.pegelonline_stations, C.inhga_bulletin, C.hidmet_report]
    jobs += [lambda p=pid: C.glofas_recent(p, past_days=10, forecast_days=3)
             for pid in C.GLOFAS_POINTS]

    def safe(fn):
        try:
            fn()
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=6) as pool:
        pool.map(safe, jobs)
    print("cache pre-încălzit")

    # arhiva buletinelor INHGA (o singură dată; apoi doar ziua curentă)
    safe(lambda: C.inhga_backfill(days=90))
    # raportul de anomalii cere arhive lungi — îl pre-calculăm tot aici
    safe(lambda: C.cached("anomalii_report", 6 * 3600, anomalii.report))
    print("istoric INHGA + raport anomalii pregătite")


if __name__ == "__main__":
    threading.Thread(target=warmup, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Monitor Dunărea → http://localhost:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
