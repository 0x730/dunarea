"""Conectori către sursele oficiale de date pentru Dunăre.

Fiecare conector returnează dict-uri JSON-serializabile și trece printr-un
cache SQLite cu TTL, ca să nu lovim serverele oficiale la fiecare refresh.
Doar biblioteca standard — fără dependențe externe.
"""

import base64
import hashlib
import json
import html as html_lib
import os
import re
import sqlite3
import ssl
import struct
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import unicodedata
import zlib
from datetime import date, datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "cache.db")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) DanubeMonitor/1.0 (uz personal, date oficiale)"

_db_lock = threading.Lock()
# single-flight: o singură reîmprospătare per cheie, oricâte cereri simultane
_inflight_lock = threading.Lock()
_inflight = {}          # cheie -> [lacăt, nr. așteptători] (se golește singur)
_last_fail = {}         # cheie -> moment ultimului eșec (evită N reîncercări)
FAIL_BACKOFF_S = 60

# ---------------------------------------------------------------- cache ----

def _db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache ("
        " key TEXT PRIMARY KEY, fetched_at REAL, ttl REAL, payload TEXT)"
    )
    return conn


def cache_get(key, max_age=None):
    with _db_lock, _db() as conn:
        row = conn.execute(
            "SELECT fetched_at, ttl, payload FROM cache WHERE key=?", (key,)
        ).fetchone()
    if not row:
        return None
    fetched_at, ttl, payload = row
    age = time.time() - fetched_at
    if age > (max_age if max_age is not None else ttl):
        return None
    return {"age": age, "data": json.loads(payload)}


def cache_put(key, data, ttl):
    with _db_lock, _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?,?,?,?)",
            (key, time.time(), ttl, json.dumps(data)),
        )


# chei păstrate permanent (arhiva locală care crește zi de zi)
PERMANENT_PREFIXES = ("hist:", "inhga_day:", "grav2:", "glofas_cell:", "analiza_ai")


def cache_gc(max_age_expirat=30 * 86400):
    """Curăță rândurile expirate de mult și cheile orfane rămase după
    redenumiri de prefix — altfel cache.db crește la nesfârșit."""
    acum = time.time()
    with _db_lock, _db() as conn:
        rows = conn.execute("SELECT key, fetched_at, ttl FROM cache").fetchall()
        sterse = 0
        for key, fetched_at, ttl in rows:
            if key.startswith(PERMANENT_PREFIXES):
                continue
            if acum - fetched_at > max(ttl, 0) + max_age_expirat:
                conn.execute("DELETE FROM cache WHERE key=?", (key,))
                sterse += 1
        # prefixe abandonate de versiuni vechi ale codului
        for mort in ("grav:%", "era5:%", "era5v2:%", "era5pt:v3:%",
                     "era5batch:v1:regional:%", "era5batch:v1:upper:%"):
            sterse += conn.execute("DELETE FROM cache WHERE key LIKE ?",
                                   (mort,)).rowcount
    return sterse


def daily_snapshot(source, payload):
    """Arhiva locală: o fotografie pe zi pentru fiecare sursă zilnică —
    aplicația își construiește singură istoricul măsurat."""
    try:
        cache_put(f"hist:{source}:{date.today().isoformat()}", payload, 10 ** 9)
    except Exception:
        pass


def history_status():
    with _db_lock, _db() as conn:
        rows = conn.execute(
            "SELECT key FROM cache WHERE key LIKE 'hist:%' OR key LIKE 'inhga_day:%'"
        ).fetchall()
    out = {}
    for (k,) in rows:
        if k.startswith("inhga_day:"):
            src, d = "inhga", k.split(":")[1]
        else:
            _, src, d = k.split(":", 2)
        e = out.setdefault(src, {"zile": 0, "din": d})
        e["zile"] += 1
        if d < e["din"]:
            e["din"] = d
    return out


def cached(key, ttl, fetch_fn, stale_ok=True):
    """Returnează din cache dacă e proaspăt; altfel refetch. La eroare de
    rețea servește versiunea veche (stale) dacă există, cu marcaj.

    Cereri simultane pe aceeași cheie NU declanșează fetch-uri paralele:
    prima ia lacătul cheii, restul așteaptă și consumă rezultatul ei."""
    hit = cache_get(key)
    if hit:
        return {"data": hit["data"], "cache_age_s": int(hit["age"]), "stale": False}

    with _inflight_lock:
        entry = _inflight.get(key)
        if entry is None:
            entry = _inflight[key] = [threading.Lock(), 0]
        entry[1] += 1
    try:
        with entry[0]:
            hit = cache_get(key)   # altcineva a adus datele cât am așteptat
            if hit:
                return {"data": hit["data"], "cache_age_s": int(hit["age"]),
                        "stale": False}
            # dacă tocmai a eșuat, nu punem toți așteptătorii să reîncerce
            with _inflight_lock:
                failed_at = _last_fail.get(key)
            if failed_at and time.time() - failed_at < FAIL_BACKOFF_S:
                old = cache_get(key, max_age=10 ** 9) if stale_ok else None
                if old:
                    return {"data": old["data"], "cache_age_s": int(old["age"]),
                            "stale": True, "error": "sursa nu a răspuns recent"}
                raise RuntimeError("sursa nu a răspuns recent (backoff)")
            try:
                res = _fetch_and_store(key, ttl, fetch_fn, stale_ok)
                # _fetch_and_store poate întoarce copia veche în loc să
                # arunce excepția. Și acesta este un eșec al sursei: păstrăm
                # backoff-ul, altfel fiecare cerere publică ar reîncerca
                # imediat aceeași sursă căzută.
                with _inflight_lock:
                    if res.get("stale"):
                        _last_fail[key] = time.time()
                    else:
                        _last_fail.pop(key, None)
                return res
            except Exception:
                with _inflight_lock:
                    _last_fail[key] = time.time()
                raise
    finally:
        with _inflight_lock:
            entry[1] -= 1
            if entry[1] <= 0:
                _inflight.pop(key, None)
            # marcajele de eșec expiră singure — nu lăsăm dicționarul să crească
            if len(_last_fail) > 256:
                prag = time.time() - FAIL_BACKOFF_S
                for k in [k for k, t in _last_fail.items() if t < prag]:
                    _last_fail.pop(k, None)


def _fetch_and_store(key, ttl, fetch_fn, stale_ok):
    try:
        data = fetch_fn()
        cache_put(key, data, ttl)
        return {"data": data, "cache_age_s": 0, "stale": False}
    except Exception as exc:
        if stale_ok:
            old = cache_get(key, max_age=10 ** 9)
            if old:
                return {
                    "data": old["data"],
                    "cache_age_s": int(old["age"]),
                    "stale": True,
                    "error": str(exc),
                }
        raise


# ----------------------------------------------------------------- http ----

class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Cu verificarea TLS relaxată, un redirect către alt host ar duce
    contextul nesigur oriunde. Permitem redirect doar pe același host."""

    def __init__(self, host):
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).hostname != self.host:
            raise urllib.error.HTTPError(
                newurl, code, "redirect către alt host respins (TLS relaxat)",
                headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url, timeout=25, insecure=False, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx),
            _SameHostRedirect(urllib.parse.urlparse(url).hostname))
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
        return raw if binary else raw.decode("utf-8", errors="replace")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if binary:
        return raw
    return raw.decode("utf-8", errors="replace")


def http_json(url, timeout=25):
    return json.loads(http_get(url, timeout=timeout))


# ------------------------------------------------------ registru puncte ----
# Puncte GloFAS (Copernicus) pe Dunăre. Coordonatele sunt candidate; celula
# de grilă exactă a râului se determină automat (snap) și se ține în cache.
# km = kilometrul fluvial navigabil (de la Sulina spre amonte), aproximativ.

GLOFAS_POINTS = {
    "regensburg":    {"name": "Regensburg (DE)",            "lat": 49.020, "lon": 12.100, "km": 2379, "country": "DE"},
    "passau":        {"name": "Passau–Achleiten (DE/AT)",   "lat": 48.580, "lon": 13.505, "km": 2223, "country": "DE/AT", "tight": True},
    "linz":          {"name": "Linz (AT)",                  "lat": 48.310, "lon": 14.290, "km": 2135, "country": "AT"},
    "viena":         {"name": "Viena (AT)",                 "lat": 48.190, "lon": 16.420, "km": 1929, "country": "AT"},
    "hofkirchen":    {"name": "Hofkirchen (DE)",            "lat": 48.676, "lon": 13.115, "km": 2257, "country": "DE", "tight": True},
    "bratislava":    {"name": "Bratislava (SK)",            "lat": 48.135, "lon": 17.115, "km": 1869, "country": "SK"},
    "budapesta":     {"name": "Budapesta (HU)",             "lat": 47.480, "lon": 19.055, "km": 1647, "country": "HU"},
    "mohacs":        {"name": "Mohács (HU)",                "lat": 45.995, "lon": 18.685, "km": 1447, "country": "HU"},
    "novi_sad":      {"name": "Novi Sad (RS)",              "lat": 45.255, "lon": 19.860, "km": 1255, "country": "RS"},
    "bazias":        {"name": "Baziaș (intrarea în RO)",    "lat": 44.775, "lon": 21.325, "km": 1071, "country": "RO"},
    "pf1_amonte":    {"name": "Porțile de Fier I – amonte", "lat": 44.665, "lon": 22.520, "km": 943,  "country": "RO"},
    "gruia":         {"name": "Gruia (aval PF II)",         "lat": 44.270, "lon": 22.700, "km": 851,  "country": "RO"},
    "calafat":       {"name": "Calafat",                    "lat": 43.980, "lon": 22.930, "km": 795,  "country": "RO"},
    "corabia":       {"name": "Corabia",                    "lat": 43.755, "lon": 24.500, "km": 630,  "country": "RO"},
    "zimnicea":      {"name": "Zimnicea",                   "lat": 43.615, "lon": 25.365, "km": 554,  "country": "RO"},
    "giurgiu":       {"name": "Giurgiu",                    "lat": 43.870, "lon": 25.955, "km": 493,  "country": "RO"},
    "oltenita":      {"name": "Oltenița",                   "lat": 44.060, "lon": 26.635, "km": 430,  "country": "RO"},
    "calarasi":      {"name": "Călărași (Chiciu)",          "lat": 44.125, "lon": 27.270, "km": 375,  "country": "RO"},
    # zona Cernavodă e despletită (Borcea/Dunărea Veche/Bala); modelul duce
    # brațul principal pe Borcea — celula de mai jos e acel braț, nu canalul
    "cernavoda":     {"name": "Cernavodă (braț principal)", "lat": 44.375, "lon": 27.800, "km": 300,  "country": "RO", "tight": True},
    "harsova":       {"name": "Hârșova",                    "lat": 44.680, "lon": 27.940, "km": 253,  "country": "RO"},
    "braila":        {"name": "Brăila",                     "lat": 45.245, "lon": 27.970, "km": 170,  "country": "RO"},
    "galati":        {"name": "Galați",                     "lat": 45.420, "lon": 28.075, "km": 150,  "country": "RO"},
    "isaccea":       {"name": "Isaccea",                    "lat": 45.265, "lon": 28.465, "km": 100,  "country": "RO"},
    "ceatal_izmail": {"name": "Ceatal Izmail (intrare deltă)", "lat": 45.215, "lon": 28.720, "km": 80, "country": "RO/UA", "tight": True},
    "brat_chilia":   {"name": "Brațul Chilia",              "lat": 45.320, "lon": 28.860, "km": 60,  "country": "RO/UA", "tight": True},
    "brat_tulcea":   {"name": "Brațul Tulcea",              "lat": 45.180, "lon": 28.800, "km": 65,  "country": "RO", "tight": True},
    "brat_sulina":   {"name": "Brațul Sulina",              "lat": 45.155, "lon": 29.100, "km": 40,  "country": "RO", "tight": True},
    "brat_sf_gheorghe": {"name": "Brațul Sf. Gheorghe",     "lat": 45.050, "lon": 29.080, "km": 55,  "country": "RO", "tight": True},
}

# Puncte pentru precipitații (ERA5) — sub-bazine relevante pentru Dunăre.
PRECIP_POINTS = {
    "bazin_superior": {"name": "Bazin superior (Passau, DE/AT)", "lat": 48.57, "lon": 13.45},
    "bazin_mijlociu": {"name": "Bazin mijlociu (Budapesta, HU)", "lat": 47.50, "lon": 19.05},
    "oltenia":        {"name": "Oltenia (Craiova)",              "lat": 44.32, "lon": 23.80},
    "muntenia":       {"name": "Muntenia (București)",           "lat": 44.43, "lon": 26.10},
    "moldova_sud":    {"name": "Sud-estul României (Galați)",    "lat": 45.45, "lon": 28.05},
    "delta":          {"name": "Delta Dunării (Tulcea)",         "lat": 45.17, "lon": 28.80},
}


# puncte-proxy distribuite pe bazinul superior (la Achleiten): câmpie + alpin,
# pentru media de precipitații folosită în bilanțul „unde e apa"
UPPER_BASIN_POINTS = [
    ("passau",    48.57, 13.45),   # valea Dunării, câmpie
    ("regensburg", 49.02, 12.10),  # Bavaria de nord
    ("ulm",       48.40, 10.00),   # Dunărea șvabă
    ("salzburg",  47.80, 13.04),   # prealpin
    ("inn_tirol", 47.27, 11.40),   # valea Innului, alpin
    ("inn_sud",   46.95, 10.50),   # Alpii înalți (Engadin/Tirolul de sus)
]


def era5_point(tag, lat, lon, start_year):
    """Serie zilnică de precipitații pentru un punct arbitrar (cache separat)."""
    start = f"{start_year}-01-01"
    end = (date.today() - timedelta(days=3)).isoformat()

    def fetch():
        qs = urllib.parse.urlencode(
            {"latitude": lat, "longitude": lon, "start_date": start,
             "end_date": end, "daily": "precipitation_sum", "timezone": "UTC",
             # Pentru comparații multidecenale avem nevoie de aceeași
             # reanaliză în toată seria. Implicitul Open-Meteo „Best Match”
             # combină IFS, ERA5 și ERA5-Land și se poate schimba în timp.
             "models": "era5"})
        d = http_json(f"{ARCHIVE_API}?{qs}")
        return {"time": d["daily"]["time"],
                "precip": d["daily"]["precipitation_sum"]}

    return cached(f"era5pt:v3:{tag}:{start_year}", 24 * 3600, fetch)


def _era5_batch(cache_group, points, start_year, include_snow=False):
    """Fetch fixed monitor points in one documented multi-coordinate request.

    Besides being faster, this keeps a cold cache from issuing a burst of
    long-history calls and tripping the provider's rate limit.
    """
    start = f"{start_year}-01-01"
    end = (date.today() - timedelta(days=3)).isoformat()
    daily = ("precipitation_sum,snowfall_sum" if include_snow
             else "precipitation_sum")

    def fetch():
        qs = urllib.parse.urlencode({
            "latitude": ",".join(str(lat) for _, lat, _ in points),
            "longitude": ",".join(str(lon) for _, _, lon in points),
            "start_date": start,
            "end_date": end,
            "daily": daily,
            "timezone": "UTC",
            "models": "era5",
        }, safe=",")
        payload = http_json(f"{ARCHIVE_API}?{qs}")
        rows = payload if isinstance(payload, list) else [payload]
        if len(rows) != len(points):
            raise RuntimeError("răspuns ERA5 incomplet pentru punctele grupate")
        out = {}
        for (tag, _, _), row in zip(points, rows):
            values = row["daily"]
            out[tag] = {
                "time": values["time"],
                "precip": values["precipitation_sum"],
            }
            if include_snow:
                out[tag]["snow"] = values.get("snowfall_sum", [])
        return out

    key = f"era5batch:v1:{cache_group}:{start_year}"
    return cached(key, 24 * 3600, fetch)


def _era5_monitor_batch(start_year):
    """All monitor coordinates in one request, with coordinate aliases.

    The upper-basin Passau proxy is the same coordinate as the public
    ``bazin_superior`` series, so it is requested only once.
    """
    aliases = {"regional": {}, "upper": {}}
    points = []
    by_coordinate = {}

    def register(group, public_tag, lat, lon):
        coordinate = (lat, lon)
        internal_tag = by_coordinate.get(coordinate)
        if internal_tag is None:
            internal_tag = f"p{len(points)}"
            by_coordinate[coordinate] = internal_tag
            points.append((internal_tag, lat, lon))
        aliases[group][public_tag] = internal_tag

    for pid, p in PRECIP_POINTS.items():
        register("regional", pid, p["lat"], p["lon"])
    for tag, lat, lon in UPPER_BASIN_POINTS:
        register("upper", tag, lat, lon)

    result = _era5_batch("monitor", points, start_year, include_snow=True)
    data = {
        group: {tag: result["data"][internal]
                for tag, internal in mapping.items()}
        for group, mapping in aliases.items()
    }
    return {**result, "data": data}


def era5_precip_all(start_year):
    """All six public precipitation zones from the shared monitor request."""
    result = _era5_monitor_batch(start_year)
    return {**result, "data": result["data"]["regional"]}


def era5_upper_basin(start_year):
    """All six upper-basin proxy points from the shared monitor request."""
    result = _era5_monitor_batch(start_year)
    return {**result, "data": result["data"]["upper"]}


# --------------------------------------------------------------- GloFAS ----

FLOOD_API = "https://flood-api.open-meteo.com/v1/flood"


def _flood_call(lat, lon, extra):
    qs = urllib.parse.urlencode(
        {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
         "daily": "river_discharge", **extra}
    )
    url = f"{FLOOD_API}?{qs}"
    for attempt in range(4):
        try:
            return http_json(url)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                raise
            time.sleep(8 * (attempt + 1))  # rate-limit: așteaptă și reia


def glofas_snap(point_id):
    """Găsește celula de grilă GloFAS care conține efectiv albia fluviului:
    scanează vecinătatea coordonatei candidate și alege celula cu debitul
    maxim. Rezultatul se ține în cache un an."""
    p = GLOFAS_POINTS[point_id]

    # pentru brațele deltei căutăm doar în imediata vecinătate, altfel celula
    # ar putea "sări" pe brațul alăturat, care are debit mai mare
    offsets = (-0.05, 0.0, 0.05) if p.get("tight") else (-0.05, 0.0, 0.05, -0.10, 0.10)

    def find():
        best = (None, None, -1.0)
        for dlat in offsets:
            for dlon in offsets:
                try:
                    d = _flood_call(p["lat"] + dlat, p["lon"] + dlon,
                                    {"past_days": 3, "forecast_days": 1})
                    vals = [v for v in d["daily"]["river_discharge"] if v]
                    peak = max(vals) if vals else 0.0
                    if peak > best[2]:
                        best = (d["latitude"], d["longitude"], peak)
                except Exception:
                    continue
        if best[0] is None:
            raise RuntimeError(f"nicio celulă GloFAS validă pentru {point_id}")
        return {"lat": best[0], "lon": best[1], "peak_seen": best[2]}

    return cached(f"glofas_cell:{point_id}", 365 * 86400, find)["data"]


def glofas_recent(point_id, past_days=30, forecast_days=7):
    cell = glofas_snap(point_id)

    def fetch():
        d = _flood_call(cell["lat"], cell["lon"],
                        {"past_days": past_days, "forecast_days": forecast_days})
        return {"time": d["daily"]["time"],
                "discharge": d["daily"]["river_discharge"],
                "cell": cell}

    return cached(f"glofas_recent:{point_id}:{past_days}:{forecast_days}",
                  3 * 3600, fetch)


def glofas_archive(point_id, start_year):
    """Serie zilnică istorică (GloFAS) de la 1 ianuarie start_year până azi."""
    cell = glofas_snap(point_id)
    start = f"{start_year}-01-01"
    end = date.today().isoformat()

    def fetch():
        d = _flood_call(cell["lat"], cell["lon"],
                        {"start_date": start, "end_date": end})
        return {"time": d["daily"]["time"],
                "discharge": d["daily"]["river_discharge"],
                "cell": cell}

    return cached(f"glofas_arch:{point_id}:{start_year}", 24 * 3600, fetch)


# ----------------------------------------------------- ERA5 precipitații ----

ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"


def era5_precip(point_id, start_year):
    p = PRECIP_POINTS[point_id]
    start = f"{start_year}-01-01"
    # ERA5 are întârziere de câteva zile
    end = (date.today() - timedelta(days=3)).isoformat()

    def fetch():
        qs = urllib.parse.urlencode(
            {"latitude": p["lat"], "longitude": p["lon"],
             "start_date": start, "end_date": end,
             "daily": "precipitation_sum,snowfall_sum", "timezone": "UTC",
             "models": "era5"}
        )
        d = http_json(f"{ARCHIVE_API}?{qs}")
        return {"time": d["daily"]["time"],
                "precip": d["daily"]["precipitation_sum"],
                "snow": d["daily"].get("snowfall_sum"),
                "point": p}

    return cached(f"era5v3:{point_id}:{start_year}", 24 * 3600, fetch)


# ---------------------------------------------------------- PEGELONLINE ----
# Serviciul federal german al căilor navigabile (WSV) — include și stațiile
# austriece VIA DONAU. Date orare/15-min de nivel (W) și debit (Q).

PEGEL_API = "https://www.pegelonline.wsv.de/webservices/rest-api/v2"


def pegelonline_stations():
    def fetch():
        data = http_json(f"{PEGEL_API}/stations.json?waters=DONAU&includeTimeseries=true"
                         "&includeCurrentMeasurement=true")
        out = []
        for s in data:
            ts = {t["shortname"]: t for t in s.get("timeseries", [])}
            entry = {
                "uuid": s["uuid"], "name": s["longname"].title(),
                "km": s.get("km"), "agency": s.get("agency"),
                "params": sorted(ts.keys()),
            }
            for pname in ("W", "Q"):
                t = ts.get(pname)
                if t and t.get("currentMeasurement"):
                    entry[pname.lower()] = {
                        "value": t["currentMeasurement"].get("value"),
                        "unit": t.get("unit"),
                        "ts": t["currentMeasurement"].get("timestamp"),
                    }
            out.append(entry)
        out.sort(key=lambda x: -(x["km"] or 0))
        return out

    return cached("pegel_stations", 900, fetch)


def pegelonline_series(uuid, param="W", days=10):
    param = "Q" if param.upper() == "Q" else "W"
    days = max(1, min(int(days), 30))

    def fetch():
        d = http_json(f"{PEGEL_API}/stations/{uuid}/{param}/measurements.json?start=P{days}D")
        return {"param": param,
                "time": [m["timestamp"] for m in d],
                "values": [m["value"] for m in d]}

    return cached(f"pegel_series:{uuid}:{param}:{days}", 900, fetch)


# ------------------------------------------------------------- INHGA RO ----
# Buletinul zilnic „Diagnoza și prognoza hidrologică pentru Dunăre" —
# hidro.ro (WordPress). Extragem cifrele-cheie + textul oficial integral.

INHGA_LIST = "https://www.hidro.ro/bulletin_type/diagnoza-si-prognoza-pentru-dunare/"
INHGA_CACHE_KEY = "inhga_bulletin:v2"
INHGA_CACHE_TTL_S = 30 * 60


def _strip_tags(html):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text)


def _num(s):
    return float(s.replace(".", "").replace(",", ".")) if s else None


def inhga_bulletin():
    def fetch():
        listing = http_get(INHGA_LIST)
        links = re.findall(
            r'href="(https://www\.hidro\.ro/bulletin/diagnoza[^"]*-(\d{2})-(\d{2})-(\d{4})/)"',
            listing)
        if not links:
            raise RuntimeError("nu găsesc buletine INHGA în listă")
        links.sort(key=lambda m: (m[3], m[2], m[1]), reverse=True)
        url, dd, mm, yyyy = links[0]
        html = http_get(url)
        text = _strip_tags(html)

        # normalizăm diacriticele pentru regex și lipim "m³/s" rupt de taguri
        t = (text.replace("ş", "ș").replace("ţ", "ț")
                 .replace("Ş", "Ș").replace("Ţ", "Ț"))
        t = re.sub(r"m\s*\n\s*3\s*\n\s*/s", " m³/s", t)

        def grab(pattern):
            m = re.search(pattern, t, re.S | re.I)
            return m.group(1) if m else None

        # „[^.]*?" ar bloca fraza dacă apare un număr cu punct (1.500) sau o
        # oră (06.00) între ancoră și valoare — limităm pe lungime, nu pe punct
        debit = grab(r"Baziaș\)\s*a fost.{0,120}?(?:valoarea|valorii)\s+de\s*([\d.,]+)\s*m")
        trend = grab(r"Baziaș\)\s*a fost în\s*([^\s,]+(?:\s+ușoară)?)")
        medie = grab(r"media multianuală a lunii \w+\s*\(?\s*([\d.,]+)\s*m")
        prognoza = grab(r"Baziaș\)\s*va fi.{0,160}?(?:valoarea|valorii)\s+de\s*([\d.,]+)\s*m")

        # paragrafele oficiale integrale (diagnoză + prognoză), fără titluri
        lines = [ln.strip() for ln in t.split("\n") if len(ln.strip()) > 60]
        core, seen = [], set()
        for ln in lines:
            if ln.startswith("Diagnoza și prognoza") or ln in seen:
                continue
            if re.search(r"debit|nivel|Baziaș|Porțile|sector", ln, re.I):
                seen.add(ln)
                core.append(ln)
        core = core[:8]

        # ține seria zilnică la zi fără să depindă de backfill/repornire
        if _num(debit) is not None:
            cache_put(f"inhga_day:{yyyy}-{mm}-{dd}", _num(debit), 10 ** 9)

        return {
            "url": url,
            "data_buletin": f"{yyyy}-{mm}-{dd}",
            "debit_bazias_m3s": _num(debit),
            "tendinta": trend,
            "media_multianuala_m3s": _num(medie),
            "prognoza_debit_m3s": _num(prognoza),
            "text_oficial": core,
        }

    # Verificăm de două ori pe oră: suficient de rar pentru sursa oficială,
    # dar fără întârzierea de până la 3 ore a unui buletin nou sau corectat.
    return cached(INHGA_CACHE_KEY, INHGA_CACHE_TTL_S, fetch)


# Prognoza lunară INHGA pentru râurile interioare, restrânsă la sistemele care
# aduc apă în sectorul românesc al Dunării. Nu includem Someș/Mureș/Crișuri/
# Timiș: ele ajung în Dunăre prin Tisa în amonte de Baziaș și aportul lor este
# deja cuprins în debitul de intrare în țară.
INHGA_MONTHLY_LIST = "https://www.hidro.ro/bulletin_type/buletin-hidrologic-lunar/"
INHGA_TRIBUTARIES_CACHE_KEY = "inhga_tributaries:v1"
INHGA_TRIBUTARIES_TTL_S = 6 * 3600
INHGA_MONTHS_RO = {
    "ianuarie": 1, "februarie": 2, "martie": 3, "aprilie": 4,
    "mai": 5, "iunie": 6, "iulie": 7, "august": 8,
    "septembrie": 9, "octombrie": 10, "noiembrie": 11, "decembrie": 12,
}
INHGA_DANUBE_TRIBUTARIES = (
    {"id": "nera", "label": "Nera", "stem": "ner",
     "relation": "upstream_cernavoda"},
    {"id": "cerna", "label": "Cerna", "stem": "cern",
     "relation": "upstream_cernavoda"},
    {"id": "jiu", "label": "Jiu", "stem": "jiu",
     "relation": "upstream_cernavoda"},
    {"id": "olt", "label": "Olt", "stem": "olt",
     "relation": "upstream_cernavoda"},
    {"id": "vedea", "label": "Vedea", "stem": "vede",
     "relation": "upstream_cernavoda"},
    {"id": "arges", "label": "Argeș", "stem": "arges",
     "relation": "upstream_cernavoda"},
    {"id": "ialomita", "label": "Ialomița", "stem": "ialomit",
     "relation": "downstream_cernavoda",
     "caveat": "excepția poate viza numai bazinul superior"},
    {"id": "siret", "label": "Siret", "stem": "siret",
     "relation": "downstream_cernavoda",
     "caveat": "excepția poate viza numai cursul superior"},
    {"id": "prut", "label": "Prut", "stem": "prut",
     "relation": "downstream_cernavoda",
     "caveat": "excepția poate viza numai afluenții Prutului"},
)


def _plain_ro(value):
    value = (html_lib.unescape(value or "").replace("ş", "ș").replace("ţ", "ț")
             .replace("Ş", "Ș").replace("Ţ", "Ț"))
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in decomposed
                   if unicodedata.category(ch) != "Mn").lower()


def _inhga_monthly_latest_url(listing):
    """Selectează primul buletin de prognoză lunară, nu un slug presupus."""
    for article in re.findall(r"<article\b[^>]*>(.*?)</article>", listing,
                              re.S | re.I):
        title_match = re.search(
            r'<h2\b[^>]*class="[^"]*entry-title[^"]*"[^>]*>.*?'
            r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', article, re.S | re.I)
        if not title_match:
            continue
        title = re.sub(r"\s+", " ", _strip_tags(title_match.group(2))).strip()
        plain = _plain_ro(title)
        if "prognoza" in plain and "lunar" in plain:
            return html_lib.unescape(title_match.group(1)), title
    raise RuntimeError("nu găsesc ultima prognoză hidrologică lunară INHGA")


def _band(minimum=None, maximum=None, operator="range"):
    return {"min": minimum, "max": maximum, "operator": operator}


def _parse_inhga_monthly_tributaries(page, url=None, listing_title=None):
    """Extrage intervalele oficiale fără a transforma prognoza în măsurătoare."""
    title_match = re.search(
        r'<span\b[^>]*class="[^"]*entry-title[^"]*"[^>]*>(.*?)</span>',
        page, re.S | re.I)
    title = (re.sub(r"\s+", " ", _strip_tags(title_match.group(1))).strip()
             if title_match else listing_title)
    updated = re.search(r'<span\b[^>]*class="[^"]*updated[^"]*"[^>]*>'
                        r'(\d{4}-\d{2}-\d{2})T', page, re.I)
    if not title or not updated:
        raise RuntimeError("prognoza lunară INHGA nu mai expune titlul și data")

    paragraphs = []
    for raw in re.findall(r"<p\b[^>]*>(.*?)</p>", page, re.S | re.I):
        text = re.sub(r"\s+", " ", _strip_tags(raw)).strip()
        if _plain_ro(text).startswith("in luna ") and "regimul hidrologic" in _plain_ro(text):
            paragraphs.append(text)

    months = []
    for text in paragraphs:
        plain = _plain_ro(text)
        month_match = re.search(
            r"in luna\s+(ianuarie|februarie|martie|aprilie|mai|iunie|iulie|"
            r"august|septembrie|octombrie|noiembrie|decembrie)\s+(20\d{2})",
            plain)
        base_match = re.search(
            r"valori cuprinse intre\s*(\d+)\s*[-–]\s*(\d+)\s*%", plain)
        if not month_match or not base_match:
            raise RuntimeError("formatul intervalelor lunare INHGA s-a schimbat")

        month_name, year_text = month_match.groups()
        base = _band(int(base_match.group(1)), int(base_match.group(2)))
        groups = []
        higher = re.search(
            r"mai mari\s*\((\d+)\s*[-–]\s*(\d+)\s*%\)\s*(.*?)"
            r"(?=\s+(?:si|și|şi)\s+mai mici|\.$)", plain)
        if higher:
            groups.append({
                "kind": "higher",
                "band_pct": _band(int(higher.group(1)), int(higher.group(2))),
                "clause": higher.group(3).strip(" ,"),
            })
        lower = re.search(r"mai mici\s*\(sub\s*(\d+)\s*%\)\s*(.*?)(?=\.$)", plain)
        if lower:
            groups.append({
                "kind": "lower",
                "band_pct": _band(None, int(lower.group(1)), "lt"),
                "clause": lower.group(2).strip(" ,"),
            })

        basins = []
        for spec in INHGA_DANUBE_TRIBUTARIES:
            matches = [group for group in groups
                       if re.search(rf"\b{re.escape(spec['stem'])}\w*",
                                    group["clause"])]
            if len(matches) == 1:
                group = matches[0]
                basin = {**spec, "band_pct": group["band_pct"],
                         "basis": "explicit_" + group["kind"],
                         "official_clause": group["clause"]}
            elif len(matches) > 1:
                basin = {**spec, "band_pct": None, "basis": "mixed",
                         "bands": [group["band_pct"] for group in matches],
                         "official_clause": " · ".join(group["clause"] for group in matches)}
            else:
                basin = {**spec, "band_pct": base, "basis": "general_band",
                         "official_clause": None}
            basin.pop("stem", None)
            basins.append(basin)

        month_num = INHGA_MONTHS_RO[month_name]
        months.append({
            "month": f"{int(year_text):04d}-{month_num:02d}",
            "label": f"{month_name} {year_text}",
            "base_band_pct": base,
            "upstream_cernavoda": [b for b in basins
                                    if b["relation"] == "upstream_cernavoda"],
            "downstream_cernavoda": [b for b in basins
                                      if b["relation"] == "downstream_cernavoda"],
            "official_text": text,
        })

    if not months:
        raise RuntimeError("prognoza lunară INHGA nu conține luni parsabile")
    months.sort(key=lambda item: item["month"])
    return {
        "available": True,
        "url": url,
        "title": title,
        "published": updated.group(1),
        "months": months,
        "parser_version": "inhga-monthly-tributaries-v1",
        "scope": ("numai afluenții principali care intră în sectorul românesc al "
                  "Dunării; sistemele care ajung prin Tisa sunt deja incluse la Baziaș"),
        "limit": ("prognoză oficială în benzi procentuale față de mediile lunare; "
                  "nu este debit măsurat și nu cuantifică aportul fiecărui afluent"),
    }


def inhga_danube_tributaries():
    def fetch():
        listing = http_get(INHGA_MONTHLY_LIST, timeout=30)
        url, listing_title = _inhga_monthly_latest_url(listing)
        page = http_get(url, timeout=30)
        return _parse_inhga_monthly_tributaries(page, url, listing_title)

    # Fără stale fallback: un format nou sau un buletin neparsabil devine
    # indisponibil, nu păstrează prognoza lunii trecute ca și cum ar fi curentă.
    return cached(INHGA_TRIBUTARIES_CACHE_KEY, INHGA_TRIBUTARIES_TTL_S,
                  fetch, stale_ok=False)


INHGA_DAILY = ("https://www.hidro.ro/bulletin/diagnoza-si-prognoza-hidrologica-"
               "pentru-dunare-la-intrarea-in-tara-si-pe-sectorul-romanesc-{d}/")


def _parse_inhga_html(html):
    text = _strip_tags(html)
    t = (text.replace("ş", "ș").replace("ţ", "ț")
             .replace("Ş", "Ș").replace("Ţ", "Ț"))
    t = re.sub(r"m\s*\n\s*3\s*\n\s*/s", " m³/s", t)
    # Ca la parserul buletinului curent, nu oprim la primul punct: între
    # ancoră și debit pot apărea ore (06.00) sau mii scrise cu separator.
    m = re.search(r"Baziaș\)\s*a fost.{0,180}?(?:valoarea|valorii)\s+de\s*([\d.,]+)\s*m",
                  t, re.S | re.I)
    return _num(m.group(1)) if m else None


def inhga_bulletin_for(d):
    """Debitul oficial la Baziaș din buletinul INHGA al unei zile anume
    (arhiva e publică, URL-ul conține data). None dacă nu există."""
    ds = d.strftime("%d-%m-%Y")
    key = f"inhga_day:{d.isoformat()}"
    hit = cache_get(key, max_age=10 ** 9)
    if hit and hit["data"] is not None:
        return hit["data"]
    # un „nu există încă" pentru zilele recente se reîncearcă des (buletinul
    # apare pe parcursul zilei); pentru zile vechi, rar
    recent = (date.today() - d).days <= 3
    if hit and hit["data"] is None and hit["age"] < (2 * 3600 if recent else 7 * 86400):
        return None
    try:
        html = http_get(INHGA_DAILY.format(d=ds))
        debit = _parse_inhga_html(html)
    except Exception:
        debit = None
    cache_put(key, debit, 10 ** 9)
    return debit


def inhga_series(days=90):
    """Seria debitului oficial la Baziaș, din buletinele arhivate (doar din
    cache — umplerea o face warmup-ul, ca să nu blocheze cererile).
    O singură interogare SQL, nu una pe zi."""
    prag = (date.today() - timedelta(days=days)).isoformat()
    with _db_lock, _db() as conn:
        rows = conn.execute(
            "SELECT key, payload FROM cache "
            "WHERE key LIKE 'inhga_day:%' AND key >= ?",
            (f"inhga_day:{prag}",)).fetchall()
    out = []
    for k, payload in rows:
        val = json.loads(payload)
        if val is not None:
            out.append({"date": k.split(":", 1)[1], "debit_m3s": val})
    out.sort(key=lambda r: r["date"])
    return out


def inhga_backfill(days=90, pause=0.25):
    """Aduce buletinele lipsă din ultimele N zile (rulează în fundal)."""
    today = date.today()
    for i in range(days + 1):
        d = today - timedelta(days=i)
        if cache_get(f"inhga_day:{d.isoformat()}", max_age=10 ** 9) is None:
            inhga_bulletin_for(d)
            time.sleep(pause)


# ---------------------------------------------------------- RHMZ Serbia ----
# „Actual hydrological data" — tabel zilnic (ora 10 locală) cu toate stațiile,
# inclusiv Dunărea din Serbia până la Prahovo (aval de Porțile de Fier II):
# nivel (cm), variație (cm), debit (m³/s), temperatura apei. Site-ul are lanț
# TLS incomplet, de aceea fetch cu verificare relaxată (doar citire publică).

HIDMET_URL = "https://www.hidmet.gov.rs/eng/osmotreni/stanje_voda.php"


def hidmet_report():
    def fetch():
        html = http_get(HIDMET_URL, insecure=True)
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)
        out = []
        for row in rows:
            if "DUNAV" not in row:
                continue
            row = re.sub(r"<!--.*?-->", "", row, flags=re.S)
            name_m = re.search(r'prognoza\.php\?hm_id=\d+">([^<]+)</a>', row)
            if not name_m:
                continue
            cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]

            def num_at(idx):
                if len(cells) < abs(idx):
                    return None
                c = cells[idx].replace(" ", "")
                return (float(c.replace(",", "."))
                        if re.fullmatch(r"-?\d+(?:[.,]\d+)?", c) else None)

            tend_m = re.search(r'tendencije/(stag|rast|opad)\.gif', row)
            tend = {"stag": "staționar", "rast": "creștere",
                    "opad": "scădere"}.get(tend_m.group(1) if tend_m else "", None)
            # numărul de celule-link variază; ultimele 5 coloane sunt fixe:
            # nivel, variație, debit ("*" = nepublicat), temperatură, tendință
            out.append({
                "statie": name_m.group(1).strip().title(),
                "tendinta": tend,
                "nivel_cm": num_at(-5),
                "variatie_cm": num_at(-4),
                "debit_m3s": num_at(-3),
                "temp_apa_c": num_at(-2),
            })
        if not out:
            raise RuntimeError("tabelul RHMZ nu conține stații de pe Dunăre "
                               "(structura paginii s-a schimbat?)")
        daily_snapshot("rhmz", out)
        return {"url": HIDMET_URL, "statii": out}

    return cached("hidmet", 3600, fetch)


# ------------------------------------------------------ Hydroinfo Hungary ---
# Serviciul Hidrologic Național al Ungariei (OVF) publică zilnic, într-un
# singur tabel, niveluri, debite și temperaturi de-a lungul Dunării. Este
# deosebit de util pentru debitele MĂSURATE din sectorul maghiar, unde
# DanubeSTREAM oferă în principal cote.

HYDROINFO_URL = "https://www.hydroinfo.hu/tables/eng/dunhif.html"
HYDROINFO_PROFILE_KM = {
    "Budapest": 1647,
    "Mohács": 1447,
}


def _parse_hydroinfo_html(page):
    observed = re.search(r"Observed on:\s*([^<]+)", page, re.I)
    observed_iso = None
    if observed:
        try:
            observed_iso = datetime.strptime(
                re.sub(r"\s+", " ", observed.group(1)).strip(), "%d %B %Y"
            ).date().isoformat()
        except ValueError:
            pass

    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        cells = []
        for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I):
            value = html_lib.unescape(re.sub(r"<[^>]+>", " ", cell))
            cells.append(re.sub(r"\s+", " ", value).strip())
        if len(cells) != 10 or cells[2].lower() != "danube":
            continue

        def number(value):
            if value in ("", "//"):
                return None
            try:
                return float(value.replace(",", "."))
            except ValueError:
                return None

        name = cells[1]
        out.append({
            "cod": cells[0],
            "statie": name,
            "data": observed_iso,
            "nivel_cm": number(cells[5]),
            "variatie_cm": number(cells[6]),
            "debit_m3s": number(cells[7]),
            "temp_apa_c": number(cells[8]),
            "km": HYDROINFO_PROFILE_KM.get(name),
        })
    return out


def hydroinfo_danube():
    def fetch():
        out = _parse_hydroinfo_html(http_get(HYDROINFO_URL, timeout=40))
        if len(out) < 20:
            raise RuntimeError(f"doar {len(out)} stații Dunăre în Hydroinfo — "
                               "structura paginii s-a schimbat?")
        daily_snapshot("hydroinfo", out)
        return {"url": HYDROINFO_URL, "statii": out}

    return cached("hydroinfo", 3600, fetch)


# --------------------------------------------------------- DanubeHIS/ICPDR --
# Tabelul public DanubeHIS oferă valorile curente normalizate fără autentificare;
# doar exporturile WaterML/CSV/XLS cer cont. Pentru Ungaria, furnizorul din
# spatele ambelor căi este OVF, deci aceasta este o cale alternativă de livrare,
# nu o măsurătoare independentă de Hydroinfo.

DANUBEHIS_HU_DANUBE_Q = (
    "https://www.danubehis.org/time-series/stations/Q?country=HU&river=Danube"
)
DANUBEHIS_PROFILE_KM = {"Budapest": 1647, "Mohács": 1447}


def _parse_danubehis_current(page):
    out = []
    for row in re.findall(
            r'<tr[^>]*class="[^"]*\bsync-id-id\b[^"]*"[^>]*>(.*?)</tr>',
            page, re.S | re.I):
        raw_cells = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S | re.I)
        if len(raw_cells) < 5:
            continue
        cells = []
        for _, body in raw_cells:
            text = html_lib.unescape(re.sub(r"<[^>]+>", " ", body))
            cells.append(re.sub(r"\s+", " ", text).strip())

        name = re.sub(r"\s+HU$", "", cells[0]).strip()
        link = re.search(r'href="/results/([^?"/]+)', row, re.I)
        try:
            value = float(cells[3].replace(",", "."))
            observed_day = datetime.strptime(cells[2], "%d.%m.%Y").date().isoformat()
        except (ValueError, IndexError):
            continue

        observed_utc = None
        sort_value = re.search(r'data-sort-value="(\d{9,})"', raw_cells[2][0])
        if sort_value:
            try:
                observed_utc = datetime.fromtimestamp(
                    int(sort_value.group(1)), timezone.utc
                ).isoformat()
            except (ValueError, OSError, OverflowError):
                pass
        out.append({
            "cod": link.group(1) if link else None,
            "statie": name,
            "tara": "HU",
            "data": observed_day,
            "masurat_utc": observed_utc,
            "debit_m3s": value,
            "km": DANUBEHIS_PROFILE_KM.get(name),
        })
    return out


def danubehis_danube():
    def fetch():
        out = _parse_danubehis_current(
            http_get(DANUBEHIS_HU_DANUBE_Q, timeout=45)
        )
        if len(out) < 8:
            raise RuntimeError(f"doar {len(out)} debite Dunăre în DanubeHIS — "
                               "structura paginii s-a schimbat?")
        daily_snapshot("danubehis", out)
        return {
            "url": DANUBEHIS_HU_DANUBE_Q,
            "furnizor_date": "OVF via ICPDR DanubeHIS",
            "independent_de_hydroinfo": False,
            "statii": out,
        }

    return cached("danubehis:hu-danube-q:v1", 3600, fetch)


# ------------------------------------------------ Copernicus drought/EDO --
# EDO publică hărți OGC WMS. Le folosim numai drept context spațial datat;
# nu extragem valori din culorile PNG și nu le introducem în verdicte.

EDO_WMS = "https://drought.emergency.copernicus.eu/api/wms"
EDO_WMS_PAGE = "https://drought.emergency.copernicus.eu/data/wms-service"
EDO_MAP_SPECS = {
    "cdi": {
        "layer": "cdiad",
        "title": "Combined Drought Indicator v4.1",
        "tip": "indicator compozit pentru secetă agricolă",
    },
    "soil": {
        "layer": "smian",
        "title": "Soil Moisture Anomaly",
        "tip": "anomalie modelată a umidității solului",
    },
}


def _parse_edo_status(capabilities, service_page=""):
    root = ET.fromstring(capabilities)
    by_layer = {}
    for layer in root.iter("Layer"):
        name = layer.findtext("Name")
        if not name:
            continue
        extent = next((e for e in layer.findall("Extent")
                       if e.attrib.get("name") == "time"), None)
        latest = None
        if extent is not None and extent.text:
            parts = extent.text.strip().split("/")
            latest = parts[1] if len(parts) >= 2 else parts[0]
        by_layer[name] = latest

    out = {}
    for public_name, spec in EDO_MAP_SPECS.items():
        layer_name = spec["layer"]
        # Pagina oficială de serviciu expune explicit data folosită de
        # exemplul curent; poate fi mai proaspătă decât cache-ul Capabilities.
        current = re.search(
            rf"LAYERS={re.escape(layer_name)}[^\"']*?TIME=(\d{{4}}-\d{{2}}-\d{{2}})",
            service_page, re.I,
        )
        out[public_name] = {
            **spec,
            "data": current.group(1) if current else by_layer.get(layer_name),
        }
    if any(not item["data"] for item in out.values()):
        raise RuntimeError("datele straturilor EDO nu au putut fi determinate")
    return out


def edo_status():
    def fetch():
        qs = urllib.parse.urlencode({
            "SERVICE": "WMS", "REQUEST": "GetCapabilities", "VERSION": "1.1.1"
        })
        capabilities = http_get(f"{EDO_WMS}?{qs}", timeout=45)
        service_page = http_get(EDO_WMS_PAGE, timeout=45)
        return {
            "url": EDO_WMS_PAGE,
            "bbox": [8, 42, 30, 50],
            "straturi": _parse_edo_status(capabilities, service_page),
            "nota": "context spațial Copernicus; nu intră în verdictele automate",
        }

    return cached("edo:status:v1", 6 * 3600, fetch)


def edo_map(kind):
    if kind not in EDO_MAP_SPECS:
        raise KeyError("strat EDO necunoscut")
    status = edo_status()["data"]
    layer = status["straturi"][kind]
    observation_day = layer["data"]

    def fetch():
        qs = urllib.parse.urlencode({
            "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap",
            "LAYERS": layer["layer"], "STYLES": "", "SRS": "EPSG:4326",
            "BBOX": ",".join(str(v) for v in status["bbox"]),
            "WIDTH": 1100, "HEIGHT": 400, "FORMAT": "image/png",
            "TRANSPARENT": "TRUE", "TIME": observation_day,
        }, safe=",")
        raw = http_get(f"{EDO_WMS}?{qs}", timeout=60, binary=True)
        if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) < 1000:
            raise RuntimeError("EDO nu a returnat o hartă PNG validă")
        return {
            "png_base64": base64.b64encode(raw).decode("ascii"),
            "data": observation_day,
            "layer": layer["layer"],
        }

    return cached(f"edo:map:v1:{kind}:{observation_day}", 6 * 3600, fetch)


# ------------------------------------------------------------- AFDJ RO -----
# Administrația Fluvială a Dunării de Jos (Galați) — cotele oficiale ale
# Dunării pe sectorul românesc, flux XML public, actualizat zilnic.

AFDJ_XML = "https://afdj.ro/ro/tabel_cotele_dunarii/xml"


def afdj_cote():
    def fetch():
        xml = http_get(AFDJ_XML)
        items = re.findall(r"<item key=\"\d+\">(.*?)</item>", xml, re.S)
        out = []
        for it in items:
            def fld(name):
                m = re.search(rf"<{name}><value>([^<]*)</value>", it)
                return m.group(1) if m else None

            title = fld("title")
            if not title:
                continue
            cota = fld("field_cota")
            upd = fld("field_field_data_actualiz_cote")
            out.append({
                "statie": title.strip(),
                "cota_cm": float(cota) if cota not in (None, "") else None,
                "variatie_cm": _num_or_none(fld("field_variatia")),
                "temp_apa_c": _num_or_none(fld("field_temperatura_masurata")),
                "km": _num_or_none(fld("field_km")),
                "actualizat": upd,
                "tendinte_cm": {
                    f"{h}h": _num_or_none(fld(f"field_tendinta_{h}h"))
                    for h in (24, 48, 72, 96, 120)
                },
            })
        if not out:
            raise RuntimeError("fluxul XML AFDJ nu conține stații")
        out.sort(key=lambda s: -(s["km"] or 0))
        daily_snapshot("afdj", out)
        return {"url": AFDJ_XML,
                "pagina": "https://www.afdj.ro/ro/cotele-dunarii",
                "statii": out}

    return cached("afdj", 3600, fetch)


def _num_or_none(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# -------------------------------------------------- ENTSO-E (opțional) -----
# Producția orară pe unități la Porțile de Fier I/II (proxy oficial pentru
# activitatea turbinelor). Necesită token gratuit: transparency.entsoe.eu →
# cont → Web API Security Token, apoi export ENTSOE_TOKEN=...

ENTSOE_URL = "https://web-api.tp.entsoe.eu/api"


def entsoe_irongates():
    token = os.environ.get("ENTSOE_TOKEN", "").strip()
    if not token:
        return {"activ": False,
                "motiv": "Integrarea ENTSO-E nu este activată pe această "
                         "instanță; producția pe unități la PF I/II nu este "
                         "disponibilă aici."}

    def fetch():
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=2)).strftime("%Y%m%d%H00")
        end = now.strftime("%Y%m%d%H00")
        units = []
        # partea românească (Hidroelectrica) + partea sârbească (EPS)
        for domain in ("10YRO-TEL------P", "10YCS-SERBIATSOV"):
            qs = urllib.parse.urlencode({
                "securityToken": token, "documentType": "A73",
                "processType": "A16", "in_Domain": domain,
                "periodStart": start, "periodEnd": end,
            })
            try:
                xml = http_get(f"{ENTSOE_URL}?{qs}", timeout=60)
            except Exception:
                continue
            for ts in re.findall(r"<TimeSeries>(.*?)</TimeSeries>", xml, re.S):
                name = re.search(r"<name>([^<]+)</name>", ts)
                if not name or not re.search(
                        r"portile|iron\s*gate|pdf|p\.?d\.?f|djerdap|derdap|đerdap",
                        name.group(1), re.I):
                    continue
                start_m = re.search(r"<start>([^<]+)</start>", ts)
                points = re.findall(
                    r"<position>(\d+)</position>\s*<quantity>([\d.]+)</quantity>", ts)
                res = re.search(r"<resolution>PT(\d+)M</resolution>", ts)
                units.append({
                    "unitate": name.group(1),
                    "domeniu": "RO" if domain.startswith("10YRO") else "RS",
                    "start": start_m.group(1) if start_m else None,
                    "rezolutie_min": int(res.group(1)) if res else 60,
                    "valori_mw": [(int(p), float(q)) for p, q in points],
                })
        return {"activ": True, "unitati": units}

    return cached("entsoe_pf", 3600, fetch)


# --------------------------------------------- DanubeSTREAM (danubeportal) --
# Portalul administrațiilor de navigație (FAIRway/DanubeSTREAM): mirele de pe
# întreaga Dunăre, toate țările, valori cvasi-orare, înglobate în pagină.

DANUBEPORTAL_URL = "https://www.danubeportal.com/"


def danubeportal_gauges():
    def fetch():
        html = http_get(DANUBEPORTAL_URL, timeout=40)
        m = re.search(r"var gauge = (\[.*?\]);\s*</script>", html, re.S)
        if not m:
            raise RuntimeError("nu găsesc variabila 'gauge' în pagină")
        out = []
        for g in json.loads(m.group(1)):
            if not isinstance(g, dict) or g.get("value") in (None, ""):
                continue
            out.append({
                "tara": (g.get("isrs_code") or "??")[:2],
                "statie": g.get("name"),
                "km": float(g["rkm"]) if g.get("rkm") else None,
                "cota_cm": float(g["value"]),
                "masurat_utc": g.get("measure_date_utc"),
                "fairway": g.get("fairway_name"),
            })
        if len(out) < 20:
            raise RuntimeError(f"doar {len(out)} mire găsite — structura paginii "
                               "s-a schimbat?")
        out.sort(key=lambda x: -(x["km"] or 0))
        daily_snapshot("danubestream", out)
        return {"url": DANUBEPORTAL_URL, "mire": out}

    return cached("danubeportal", 1800, fetch)


# ---------------------------------- avize către navigatori (NtS, oficiale) --
# Aceeași pagină danubeportal conține și „Notices to Skippers" — avizele
# oficiale ale administrațiilor de navigație: restricții, dragaje, niveluri
# scăzute (DECLEV), pe sectoare cu kilometraj precis. Rezultatul practic:
# harta oficială a punctelor critice din albie.

AVIZE_MOTIVE = {
    "DECLEV": "niveluri scăzute", "LIMITA": "restricție", "DREDGE": "dragaj",
    "OBSTRU": "obstacol", "SHALLO": "apă mică", "WORK": "lucrări",
    "LOCRUL": "lucrări ecluză", "CHGMAR": "semnalizare modificată",
    "SOUND": "sondaje", "INFSER": "informare", "WERMCO": "comunicat meteo",
}
AVIZE_PRIORITARE = ("DECLEV", "SHALLO", "LIMITA", "OBSTRU", "DREDGE")


def _isrs_km(code):
    # formatul ISRS: ...țară+secțiune+obiect+HHHHH — ultimele 5 cifre sunt
    # hectometrii kilometrajului fluvial
    m = re.search(r"(\d{5})$", code or "")
    return round(int(m.group(1)) / 10, 1) if m else None


def danubeportal_avize():
    def fetch():
        html = http_get(DANUBEPORTAL_URL, timeout=40)
        m = re.search(r"var nts_data = (\[.*?\]);\s*(?:var |</script>)", html, re.S)
        if not m:
            raise RuntimeError("nu găsesc nts_data în pagină")
        azi = date.today().isoformat()
        out = []
        prag_vechi = (date.today() - timedelta(days=365)).isoformat()
        for d in json.loads(m.group(1)):
            ds = d.get("date_start") or ""
            if ds > azi:
                continue
            de = d.get("date_end")
            if de and de != "0000-00-00" and de < azi:
                continue
            # avize fără dată de sfârșit, emise acum ani — zgomot rămas în sistem
            if (not de or de == "0000-00-00") and ds < prag_vechi:
                continue
            g = (d.get("geo") or [{}])[0]
            lims = [{"cod": l.get("limitation_code"), "valoare": l.get("value"),
                     "unitate": l.get("unit")}
                    for gg in (d.get("geo") or [])
                    for l in (gg.get("limitation") or [])
                    if l.get("limitation_code")]
            out.append({
                "tara": d.get("country_code"),
                "emitent": d.get("organisation"),
                "numar": d.get("year_number"),
                "motiv_cod": d.get("reason_code"),
                "motiv": AVIZE_MOTIVE.get(d.get("reason_code"),
                                          d.get("reason_code") or "?"),
                "rau": g.get("objname"),
                "km_de_la": _isrs_km(g.get("start")),
                "km_pana_la": _isrs_km(g.get("end")),
                "din": d.get("date_start"), "pana": de,
                "text": re.sub(r"\s+", " ", (d.get("contents") or ""))[:260],
                "limitari": [l for l in lims if l.get("valoare")],
                "prioritar": d.get("reason_code") in AVIZE_PRIORITARE,
            })
        if not out:
            raise RuntimeError("niciun aviz activ — structura s-a schimbat?")
        from collections import Counter
        pe_tari = dict(Counter(a["tara"] for a in out))
        daily_snapshot("avize", {"pe_tari": pe_tari,
                                 "prioritare": sum(1 for a in out if a["prioritar"])})
        out.sort(key=lambda a: (a["tara"] not in ("RO", "BG"),
                                not a["prioritar"], -(a["km_de_la"] or 0)))
        return {"active": len(out), "pe_tari": pe_tari, "avize": out[:60]}

    return cached("avize", 3 * 3600, fetch)
# Starea Sistemului Energetic Național, JSON public, fără cont.

SEN_URL = "https://www.transelectrica.ro/sen-filter"

SEN_KEYS = {
    "PROD": "productie_mw", "CONS": "consum_mw", "APE": "hidro_mw",
    "NUCL": "nuclear_mw", "CARB": "carbune_mw", "GAZE": "gaze_mw",
    "EOLIAN": "eolian_mw", "FOTO": "fotovoltaic_mw", "BMASA": "biomasa_mw",
    "SOLD": "sold_mw", "DJER": "linia_djerdap_mw",
    "PANCEVO21": "linia_pancevo1_mw", "PANCEVO22": "linia_pancevo2_mw",
    "KOZL1": "linia_kozlodui1_mw", "KOZL2": "linia_kozlodui2_mw",
}


def sen_live():
    def fetch():
        data = json.loads(http_get(SEN_URL, timeout=20))
        flat = {}
        for item in data:
            flat.update(item)
        out = {}
        for k, name in SEN_KEYS.items():
            v = flat.get(k)
            try:
                out[name] = float(v)
            except (TypeError, ValueError):
                out[name] = None
        out["actualizat"] = flat.get("row1_HARTASEN_DATA")
        daily_snapshot("sen", out)  # ultima valoare a zilei rămâne în arhivă
        return out

    return cached("sen", 300, fetch)


# ------------------------- CNE Cernavodă: rapoarte curente oficiale SNN ----
# Pagina IR este lista canonică a rapoartelor curente. Titlurile permit să
# detectăm imediat apariția unui document nou, dar cauza și starea exactă pot
# fi numai în PDF. Pentru documentele revizuite manual păstrăm un rezumat
# structurat și datat; dacă SNN publică un raport Cernavodă necunoscut, NU
# propagăm automat vechiul verdict — îl marcăm pentru revizuire.

SNN_CURRENT_REPORTS_URL = "https://nuclearelectrica.ro/ir/rapoarte-curente/"
SNN_AUDITED_CERNAVODA_REPORTS = {
    "RC-Status-Update-U2-bvb.pdf": {
        "date": "2026-08-04",
        "sha256": "786f7df06ba8f4dcfeb2b05661b6e98b4ebf1d0149a220e31740983d581de16f",
        "u1": "oprită controlat",
        "u2": "capacitate nominală",
        "u1_cause": "parametri de exploatare legați de nivelul foarte scăzut al Dunării",
        "water_related": True,
        "note": "U2 continuă să funcționeze; U1 rămâne oprită până când nivelul permite reconectarea.",
    },
    "RC-SNN_raport-curent-Unitatea-2-CNE-Cernavoda-ramane-conectata-la-SEN-bvb.pdf": {
        "date": "2026-07-30",
        "sha256": "773e1828b96ab174961cfc0ee2becdee523b17dc845054ab66863a859fb17f9e",
        "u1": "oprită controlat",
        "u2": "conectată la SEN",
        "u1_cause": "parametri de exploatare legați de nivelul foarte scăzut al Dunării",
        "water_related": True,
        "note": "Analiza parametrilor a permis menținerea U2 conectată.",
    },
    "RC-SNN_raport-curent-oprire-u-2-bvb-.pdf": {
        "date": "2026-07-29",
        "sha256": "0d103f141595248ffdb06540b17af0f775ff8b8821e60668acbf25a1699ff68e",
        "u1": "oprită controlat",
        "u2": "oprire controlată anunțată condiționat",
        "u1_cause": "parametri de exploatare legați de nivelul foarte scăzut al Dunării",
        "water_related": True,
        "note": "Raportul anunța o posibilă oprire U2; un raport ulterior poate schimba starea.",
    },
    "RC-SNN_raport-curent-debit-dunare-bvb.pdf": {
        "date": "2026-07-27",
        "sha256": "3a0120e0cf0f290b43838952644e14fead8709fdeeee817ad865d07f2ae69ace",
        "u1": "oprire controlată anunțată",
        "u2": "nespecificată în rezumat",
        "u1_cause": "parametri de exploatare legați de nivelul foarte scăzut al Dunării",
        "water_related": True,
        "note": "Oprirea U1 a fost anunțată pentru 28 iulie 2026.",
    },
    "RC-Reconectare-U1-CNE-Cernavoda.pdf": {
        "date": "2026-07-05",
        "sha256": "4814c18c12663f0d593a6941dbe7ce8b5269d7ecde2b88bb60e9ee86be651b4a",
        "u1": "reconectată la SEN",
        "u2": "nespecificată în rezumat",
        "u1_cause": None,
        "water_related": False,
        "note": "Reconectare după oprirea planificată din 2026; nu este raportul despre episodul de ape mici.",
    },
}


def _parse_snn_cernavoda_reports(page):
    items = []
    for href, label in re.findall(
            r'<a\s+[^>]*href=["\'](https://nuclearelectrica\.ro/[^"\']+\.pdf)["\'][^>]*>(.*?)</a>',
            page, re.S | re.I):
        title = _strip_tags(label).strip()
        normalized = title.lower().replace("ă", "a").replace("â", "a")
        if "/2026/" not in href:
            continue
        if not ("cernavod" in normalized or
                re.search(r"unitat(?:ea|ii)\s*[12]\b", normalized)):
            continue
        if not any(term in normalized for term in (
                "oprir", "reconect", "deconect", "ramane conectat",
                "functione", "capacitate nominal", "nivel", "debit dun")):
            continue
        filename = urllib.parse.unquote(urllib.parse.urlparse(href).path.rsplit("/", 1)[-1])
        audited = SNN_AUDITED_CERNAVODA_REPORTS.get(filename)
        items.append({
            "title": re.sub(r"\s+", " ", title),
            "url": href,
            "filename": filename,
            "audited": audited is not None,
            **({"audit_date": audited["date"]} if audited else {}),
        })
    return items


def snn_cernavoda_status():
    def fetch():
        page = http_get(SNN_CURRENT_REPORTS_URL, timeout=25)
        reports = _parse_snn_cernavoda_reports(page)
        if not reports:
            raise RuntimeError("nu găsesc rapoarte Cernavodă în lista SNN")

        latest = reports[0]
        audited = SNN_AUDITED_CERNAVODA_REPORTS.get(latest["filename"])
        if not audited:
            return {
                "source_page": SNN_CURRENT_REPORTS_URL,
                "latest_report": latest,
                "needs_review": True,
                "status_available": False,
                "reason": "SNN a publicat un raport Cernavodă nou; conținutul nu a fost încă revizuit.",
                "recent_reports": reports[:6],
            }
        try:
            raw = http_get(latest["url"], timeout=25, binary=True)
            actual_sha256 = hashlib.sha256(raw).hexdigest()
        except Exception:
            return {
                "source_page": SNN_CURRENT_REPORTS_URL,
                "latest_report": latest,
                "needs_review": True,
                "status_available": False,
                "reason": "PDF-ul SNN nu a putut fi reverificat; rezumatul anterior nu este folosit.",
                "recent_reports": reports[:6],
            }
        if actual_sha256 != audited["sha256"]:
            return {
                "source_page": SNN_CURRENT_REPORTS_URL,
                "latest_report": latest,
                "needs_review": True,
                "status_available": False,
                "pdf_sha256": actual_sha256,
                "reason": "Conținutul PDF-ului SNN s-a schimbat la același URL; necesită reverificare.",
                "recent_reports": reports[:6],
            }
        age_days = max(0, (date.today() - date.fromisoformat(audited["date"])).days)
        return {
            "source_page": SNN_CURRENT_REPORTS_URL,
            "latest_report": latest,
            "needs_review": False,
            "status_available": True,
            "status_fresh": age_days <= 3,
            "age_days": age_days,
            **{key: value for key, value in audited.items() if key != "sha256"},
            "pdf_sha256": actual_sha256,
            "recent_reports": reports[:6],
        }

    return cached("snn_cernavoda:v2", 30 * 60, fetch)


# ------------------------------------------- DAHITI (altimetrie satelitară) --
# Niveluri măsurate din satelit (TU München) — independent de orice institut
# național. Cont + cheie gratuite: dahiti.dgfi.tum.de → Register → API key.

DAHITI_API = "https://dahiti.dgfi.tum.de/api/v2"


def dahiti_danube():
    key = os.environ.get("DAHITI_KEY", "").strip()
    if not key:
        return {"activ": False,
                "motiv": "Lipsește DAHITI_KEY (cont gratuit pe dahiti.dgfi.tum.de "
                         "→ profil → API key). Cu ea, aplicația arată nivelurile "
                         "măsurate din satelit pe Dunăre, lângă mirele oficiale."}

    def fetch():
        qs = urllib.parse.urlencode({"api_key": key, "basin": "DANUBE"})
        targets = http_json(f"{DAHITI_API}/list-targets/?{qs}")
        if isinstance(targets, dict):
            targets = targets.get("data") or targets.get("targets") or []
        out = []
        for t in targets[:40]:
            entry = {"id": t.get("dahiti_id") or t.get("id"),
                     "nume": t.get("target_name") or t.get("name"),
                     "lat": t.get("latitude"), "lon": t.get("longitude")}
            try:
                q2 = urllib.parse.urlencode({"api_key": key,
                                             "dahiti_id": entry["id"]})
                d = http_json(f"{DAHITI_API}/download-water-level/?{q2}")
                seria = d.get("data") or []
                if seria:
                    last = seria[-1]
                    entry["ultima_data"] = last.get("date") or last.get("datetime")
                    entry["nivel_m"] = last.get("water_level") or last.get("wl")
            except Exception:
                pass
            out.append(entry)
        return {"activ": True, "tinte": out}

    return cached("dahiti", 12 * 3600, fetch)


# ------------------------------------- hydroweb.next (altimetrie satelitară) --
# CNES/Theia: niveluri de apă măsurate din satelit (Sentinel-3/6, SWOT) pe
# stații virtuale. Independent de orice institut național. Cheia API stă în
# data/keys/hydroweb.key sau în env HYDROWEB_KEY. Nivelurile sunt în metri
# față de geoid — alt datum decât mirele, deci se compară variațiile și
# percentilele proprii, nu valorile absolute.

HYDROWEB_STAC = "https://hydroweb.next.theia-land.fr/api/v1/rs-catalog/stac"
HYDROWEB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126"


def _hydroweb_key():
    k = os.environ.get("HYDROWEB_KEY", "").strip()
    if k:
        return k
    path = os.path.join(BASE_DIR, "data", "keys", "hydroweb.key")
    if os.path.isfile(path):
        return open(path).read().strip()
    return ""


def _hydroweb_get(url, key, raw=False):
    req = urllib.request.Request(url, headers={
        "User-Agent": HYDROWEB_UA, "Accept": "application/json,text/plain",
        "X-API-Key": key})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = r.read()
    return data if raw else json.loads(data)


def _parse_hydroweb_txt(text):
    """Seria unei stații virtuale: data, nivel (m), incertitudine (m)."""
    out = []
    for line in text.split("\n"):
        m = re.match(r"(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\s+(-?\d+\.\d+)\s+(\d+\.\d+)", line)
        if m:
            out.append((m.group(1), float(m.group(2)), float(m.group(3))))
    return out


HYDROWEB_SEARCH_AREAS = (
    ("superior", "8,47,17.5,50.5"),
    ("mijlociu_vest", "16,46.2,20.5,49"),
    ("mijlociu_est", "18,44,23.5,47.5"),
    ("inferior", "21,43,30,46.5"),
)
HYDROWEB_MAINSTEM = {"DANUBE", "DONAU", "DUNAJ", "DUNA", "DUNAV", "DUNAREA"}
HYDROWEB_MAX_AGE_DAYS = 35
HYDROWEB_MAX_UNCERTAINTY_M = 0.25


def _hydroweb_feature_km(feature):
    """Întoarce kilometrul numai pentru Dunărea propriu-zisă, nu afluenți.

    Catalogul folosește numele local al fluviului (Donau, Dunaj, Duna,
    Dunav, Dunărea), deci filtrul vechi `_DUNAREA_` vedea doar cursul inferior.
    """
    sid = (feature.get("id") or "").split("@", 1)[0]
    m = re.match(r"^R_DANUBE_([A-Z0-9-]+)_KM0*(\d+)$", sid.upper())
    if not m or m.group(1) not in HYDROWEB_MAINSTEM:
        return None
    return int(m.group(2))


def _select_hydroweb_features(features, max_statii):
    """Selecție deterministă și spațial stratificată pe întregul fluviu."""
    unique = {}
    for feature in features:
        km = _hydroweb_feature_km(feature)
        assets = feature.get("assets") or {}
        if km is None or not any(k.endswith(".txt") for k in assets):
            continue
        sid = (feature.get("id") or "").split("@", 1)[0]
        unique[sid] = (km, feature)
    candidates = sorted(unique.values(), key=lambda pair: pair[0])
    if len(candidates) <= max_statii:
        return [feature for _, feature in reversed(candidates)]

    low, high = candidates[0][0], candidates[-1][0]
    targets = [low + i * (high - low) / (max_statii - 1)
               for i in range(max_statii)] if max_statii > 1 else [(low + high) / 2]
    remaining = list(candidates)
    selected = []
    for target in targets:
        km, feature = min(remaining, key=lambda pair: (abs(pair[0] - target), pair[0]))
        selected.append((km, feature))
        remaining.remove((km, feature))
    return [feature for _, feature in sorted(selected, key=lambda pair: pair[0], reverse=True)]


def _hydroweb_station_entry(feature, text, fetched_at=None, source_url=None):
    sid = feature["id"].split("@", 1)[0]
    coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
    km = _hydroweb_feature_km(feature)
    entry = {
        "statie": sid,
        "km": km,
        "segment": "superior" if km is not None and km >= 1900 else
                   "mijlociu" if km is not None and km >= 900 else "inferior",
        "lon": coords[0],
        "lat": coords[1],
        "provider": "CNES/Theia",
        "product_family": "altimetrie_satelitara_hydroweb",
        "feature_id": feature.get("id"),
        "source_url": source_url,
        "parser_version": "hydroweb-series-v2",
        "raw_sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(),
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    serie = _parse_hydroweb_txt(text)
    flags = []
    if serie:
        d, v, sig = serie[-1]
        try:
            age = max(0, (date.today() - date.fromisoformat(d)).days)
        except ValueError:
            age = None
        entry.update({"data": d, "observation_time": d, "nivel_m": v,
                      "incertitudine_m": sig, "observatii": len(serie),
                      "vechime_zile": age})
        luna = d[5:7]
        ref = [x[1] for x in serie[:-1] if x[0][5:7] == luna]
        if len(ref) >= 5:
            below = sum(1 for r in ref if r <= v)
            entry["percentila_lunii"] = round(100 * below / len(ref), 1)
            entry["ani_serie"] = len({x[0][:4] for x in serie})
        else:
            flags.append("istoric_lunar_insuficient")
        if len(serie) >= 2:
            entry["variatie_fata_de_precedenta_m"] = round(v - serie[-2][1], 2)
        if age is None or age > HYDROWEB_MAX_AGE_DAYS:
            flags.append("observatie_veche")
        if sig is None:
            flags.append("incertitudine_lipsa")
        elif sig > HYDROWEB_MAX_UNCERTAINTY_M:
            flags.append("incertitudine_ridicata")
    else:
        flags.append("serie_lipsa")
    entry["quality_flags"] = flags
    entry["eligibila_detector"] = bool(
        entry.get("percentila_lunii") is not None and not flags
    )
    return entry


def hydroweb_danube(max_statii=12):
    key = _hydroweb_key()
    if not key:
        return {"activ": False,
                "motiv": "Lipsește cheia hydroweb.next (cont gratuit pe "
                         "hydroweb.next.theia-land.fr → API key în "
                         "data/keys/hydroweb.key sau env HYDROWEB_KEY)."}

    def fetch():
        all_features = []
        for _, bbox in HYDROWEB_SEARCH_AREAS:
            res = _hydroweb_get(
                f"{HYDROWEB_STAC}/search?collections=HYDROWEB_RIVERS_OPE"
                f"&bbox={bbox}&limit=300", key)
            all_features.extend(res.get("features", []))
        feats = _select_hydroweb_features(all_features, max_statii)
        statii = []
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for f in feats:
            txt_asset = next((a for k, a in (f.get("assets") or {}).items()
                              if k.endswith(".txt")), None)
            if txt_asset:
                try:
                    text = _hydroweb_get(txt_asset["href"], key, raw=True) \
                        .decode("utf-8", "replace")
                    entry = _hydroweb_station_entry(
                        f, text, fetched_at, source_url=txt_asset.get("href"))
                except Exception as exc:
                    entry = {"statie": f["id"].split("@", 1)[0],
                             "km": _hydroweb_feature_km(f),
                             "eroare": str(exc)[:80],
                             "quality_flags": ["eroare_citire"],
                             "eligibila_detector": False}
                statii.append(entry)
        if not statii:
            raise RuntimeError("nicio stație virtuală de pe cursul principal găsită")
        eligible = [s for s in statii if s.get("eligibila_detector")]
        payload = {
            "activ": True,
            "statii": statii,
            "statii_eligibile": len(eligible),
            "segmente_eligibile": sorted({s["segment"] for s in eligible}),
            "acoperire_km": [min((s["km"] for s in statii if s.get("km") is not None), default=None),
                             max((s["km"] for s in statii if s.get("km") is not None), default=None)],
            "colectie": "HYDROWEB_RIVERS_OPE (Theia/CNES)",
            "provider": "CNES/Theia",
            "product_family": "altimetrie_satelitara_hydroweb",
            "parser_version": "hydroweb-series-v2",
            "fetched_at": fetched_at,
            "praguri_calitate": {"vechime_max_zile": HYDROWEB_MAX_AGE_DAYS,
                                  "incertitudine_max_m": HYDROWEB_MAX_UNCERTAINTY_M},
            "nota": "selecție stratificată pe cursul principal; niveluri față de "
                    "geoid. Detectorul folosește numai observații proaspete, cu "
                    "incertitudine acceptabilă și istoric lunar suficient",
            "independenta": "independent de mire ca instrument; stațiile apropiate "
                             "și procesările DAHITI ale acelorași misiuni nu sunt voturi distincte",
        }
        daily_snapshot("hydroweb", payload)
        return payload

    return cached("hydroweb:v3", 12 * 3600, fetch)


# ---------------------- OPERA: întinderea apei, radar + optic (NASA/JPL) --
# GIBS publică vizualizările analitice OPERA fără autentificare. Folosim
# aceleași clase discrete din produs (nu clasificăm noi imagini SAR/optice).

OPERA_GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
OPERA_LAYERS = {
    "sentinel1": {
        "layer": "OPERA_L3_Dynamic_Surface_Water_Extent-Sentinel-1",
        "title": "OPERA DSWx-S1",
        "sensor": "Sentinel-1 SAR",
        "product_family": "opera_dswx_sentinel1",
        "lookback_days": 14,
    },
    "hls": {
        "layer": "OPERA_L3_Dynamic_Surface_Water_Extent-HLS",
        "title": "OPERA DSWx-HLS",
        "sensor": "Harmonized Landsat + Sentinel-2",
        "product_family": "opera_dswx_hls",
        "lookback_days": 10,
    },
}
OPERA_ZONES = {
    "portile_fier": {"name": "Porțile de Fier", "bbox": [21.65, 44.1, 23.15, 45.1]},
    "dunarea_de_jos": {"name": "Dunărea de Jos", "bbox": [25.6, 43.5, 28.45, 45.55]},
    "delta": {"name": "Delta Dunării", "bbox": [28.1, 44.65, 29.9, 45.75]},
}


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def _png_rgba_stats(raw):
    """Decodor PNG RGBA minim, stdlib-only, pentru clasele discrete GIBS."""
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("răspunsul nu este PNG")
    pos, width, height, color_type, interlace = 8, None, None, None, None
    chunks = []
    while pos + 12 <= len(raw):
        size = struct.unpack(">I", raw[pos:pos + 4])[0]
        kind = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + size]
        pos += 12 + size
        if kind == b"IHDR":
            width, height, depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", data)
            if depth != 8 or color_type != 6 or interlace != 0:
                raise ValueError("PNG GIBS cu format neașteptat")
        elif kind == b"IDAT":
            chunks.append(data)
        elif kind == b"IEND":
            break
    if not width or not height or not chunks:
        raise ValueError("PNG incomplet")
    decoded = zlib.decompress(b"".join(chunks))
    bpp, stride = 4, width * 4
    expected = height * (stride + 1)
    if len(decoded) != expected:
        raise ValueError("dimensiune PNG neașteptată")
    previous = bytearray(stride)
    pixels = []
    offset = 0
    for _ in range(height):
        filter_type = decoded[offset]
        offset += 1
        source = decoded[offset:offset + stride]
        offset += stride
        row = bytearray(stride)
        for i, value in enumerate(source):
            left = row[i - bpp] if i >= bpp else 0
            up = previous[i]
            upper_left = previous[i - bpp] if i >= bpp else 0
            if filter_type == 0:
                reconstructed = value
            elif filter_type == 1:
                reconstructed = value + left
            elif filter_type == 2:
                reconstructed = value + up
            elif filter_type == 3:
                reconstructed = value + ((left + up) // 2)
            elif filter_type == 4:
                reconstructed = value + _paeth(left, up, upper_left)
            else:
                raise ValueError("filtru PNG necunoscut")
            row[i] = reconstructed & 0xff
        pixels.extend(tuple(row[i:i + 4]) for i in range(0, stride, 4))
        previous = row

    counts = {"no_data": 0, "not_water": 0, "open_water": 0,
              "inundated_vegetation": 0, "partial_water": 0,
              "masked": 0, "other": 0}
    for r, g, b, a in pixels:
        if a == 0:
            counts["no_data"] += 1
        elif (r, g, b) == (255, 255, 255):
            counts["not_water"] += 1
        elif (r, g, b) == (0, 0, 255):
            counts["open_water"] += 1
        elif (r, g, b) == (0, 255, 0):
            counts["inundated_vegetation"] += 1
        elif (r, g, b) in ((180, 213, 244), (0, 255, 255)):
            counts["partial_water"] += 1
        elif r == g == b:
            counts["masked"] += 1
        else:
            counts["other"] += 1
    total = width * height
    coverage = total - counts["no_data"]
    classified = (counts["not_water"] + counts["open_water"] +
                  counts["inundated_vegetation"] + counts["partial_water"])
    water_like = (counts["open_water"] + counts["inundated_vegetation"] +
                  counts["partial_water"])
    return {
        "width": width, "height": height,
        **counts,
        "coverage_pct": round(100 * coverage / total, 2),
        "classified_pixels": classified,
        "water_like_pixels": water_like,
        "water_like_pct": round(100 * water_like / classified, 2) if classified else None,
    }


def _opera_wms_url(layer, bbox, observation_day, width=512, height=512):
    query = urllib.parse.urlencode({
        "SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.1.1",
        "LAYERS": layer, "STYLES": "", "SRS": "EPSG:4326",
        "BBOX": ",".join(str(v) for v in bbox), "WIDTH": width,
        "HEIGHT": height, "FORMAT": "image/png", "TRANSPARENT": "true",
        "TIME": observation_day,
    }, safe=",/")
    return f"{OPERA_GIBS_WMS}?{query}"


def _opera_latest_observation(kind, zone_id, zone):
    spec = OPERA_LAYERS[kind]
    for delta in range(spec["lookback_days"] + 1):
        observation_day = (date.today() - timedelta(days=delta)).isoformat()
        url = _opera_wms_url(spec["layer"], zone["bbox"], observation_day)
        raw = http_get(url, timeout=45, binary=True)
        try:
            stats = _png_rgba_stats(raw)
        except ValueError:
            continue
        # O fâșie minusculă poate fi doar marginea unei scene. Nu o prezentăm
        # ca observație a întregii zone de control.
        if stats["coverage_pct"] < 5 or stats["classified_pixels"] < 100:
            continue
        flags = []
        if stats["coverage_pct"] < 25:
            flags.append("acoperire_partiala")
        if stats["masked"] > stats["classified_pixels"]:
            flags.append("mascare_extinsa")
        return {
            "zone_id": zone_id, "zone": zone["name"], "bbox": zone["bbox"],
            "data": observation_day, "observation_time": observation_day,
            "vechime_zile": delta, "provider": "NASA/JPL via GIBS",
            "product": spec["title"], "sensor": spec["sensor"],
            "product_family": spec["product_family"],
            "quality_flags": flags, "stats": stats,
            "parser_version": "opera-gibs-rgba-v1",
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "source_url": url,
            "png_base64": base64.b64encode(raw).decode("ascii"),
        }
    return {"zone_id": zone_id, "zone": zone["name"], "bbox": zone["bbox"],
            "product": spec["title"], "sensor": spec["sensor"],
            "product_family": spec["product_family"],
            "quality_flags": ["fara_acoperire_recenta"], "data": None}


def _without_images(value):
    if isinstance(value, dict):
        return {k: _without_images(v) for k, v in value.items() if k != "png_base64"}
    if isinstance(value, list):
        return [_without_images(v) for v in value]
    return value


def opera_surface_water():
    def fetch():
        zones = {}
        for zone_id, zone in OPERA_ZONES.items():
            zones[zone_id] = {
                "name": zone["name"], "bbox": zone["bbox"],
                "sentinel1": _opera_latest_observation("sentinel1", zone_id, zone),
                "hls": _opera_latest_observation("hls", zone_id, zone),
            }
        payload = {
            "activ": True, "mode": "shadow", "zones": zones,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "access": "public, fără cont, prin NASA GIBS",
            "independenta": "radar și optic sunt senzori diferiți, dar ambele "
                             "hărți folosesc familia de algoritmi OPERA DSWx",
            "nota": "suprafața apei este context datat; nu intră în verdict "
                    "până când arhiva locală permite un baseline sezonier",
        }
        daily_snapshot("opera", _without_images(payload))
        return payload
    return cached("opera:gibs:v2", 12 * 3600, fetch)


def opera_surface_status():
    result = opera_surface_water()
    return {**result, "data": _without_images(result["data"])}


def opera_surface_map(kind, zone_id):
    if kind not in OPERA_LAYERS or zone_id not in OPERA_ZONES:
        raise KeyError("strat sau zonă OPERA necunoscută")
    result = opera_surface_water()
    observation = result["data"]["zones"][zone_id][kind]
    encoded = observation.get("png_base64")
    if not encoded:
        raise RuntimeError("OPERA nu are imagine recentă pentru această zonă")
    return base64.b64decode(encoded)


# ---------------- Copernicus Data Space: zăpadă și umiditatea solului ------

CDSE_STAC = "https://stac.dataspace.copernicus.eu/v1"
CDSE_BASIN_BBOX = [8, 42, 30, 50]
CDSE_CONTEXT = {
    "snow": {
        "collection": "clms_sce_europe_500m_daily_v1_cog",
        "title": "Snow Cover Extent Europe 500 m",
        "product_family": "clms_snow_extent_modis_viirs",
        "lookback_days": 30,
        "role": "indicator fizic amonte; context, nu debit măsurat",
    },
    "soil": {
        "collection": "clms_ssm_europe_1km_daily_v1_cog",
        "title": "Surface Soil Moisture Europe 1 km",
        "product_family": "clms_soil_moisture_sentinel1",
        "lookback_days": 45,
        "role": "starea antecedentă a solului; context, nu adevăr hidrologic",
        "quality_notice": "https://land.copernicus.eu/en/production-updates/"
                          "radio-frequency-interference-affecting-surface-soil-moisture-products",
    },
}


def _cdse_latest_feature(kind, spec):
    start = (date.today() - timedelta(days=spec["lookback_days"])).isoformat()
    end = date.today().isoformat()
    query = urllib.parse.urlencode({
        "bbox": ",".join(str(v) for v in CDSE_BASIN_BBOX),
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
        "limit": 20,
    }, safe=",/:" )
    response = http_json(
        f"{CDSE_STAC}/collections/{spec['collection']}/items?{query}", timeout=45)
    features = response.get("features") or []
    if not features:
        return {"activ": False, "motiv": "nicio observație recentă în catalog"}
    feature = max(features, key=lambda f: (f.get("properties") or {}).get("datetime") or "")
    props = feature.get("properties") or {}
    assets = feature.get("assets") or {}
    thumbnail = (assets.get("thumbnail") or {}).get("href")
    observation_time = props.get("datetime")
    observed_day = observation_time[:10] if observation_time else None
    age = (date.today() - date.fromisoformat(observed_day)).days if observed_day else None
    return {
        "activ": True, "id": feature.get("id"), "data": observed_day,
        "observation_time": observation_time, "vechime_zile": age,
        "collection": spec["collection"], "title": spec["title"],
        "product_family": spec["product_family"], "role": spec["role"],
        "thumbnail_url": thumbnail,
        "quality_notice": spec.get("quality_notice"),
        "provider": "Copernicus Land Monitoring Service / CDSE",
        "parser_version": "cdse-stac-v1",
        "access": "catalog și vizualizare publice; descărcare completă cu cont CDSE gratuit",
    }


def copernicus_land_context():
    def fetch():
        layers = {}
        for kind, spec in CDSE_CONTEXT.items():
            try:
                layers[kind] = _cdse_latest_feature(kind, spec)
            except Exception as exc:
                layers[kind] = {"activ": False, "motiv": str(exc)[:120],
                                "collection": spec["collection"], "title": spec["title"]}
        if not any(v.get("activ") for v in layers.values()):
            raise RuntimeError("niciun strat Copernicus Land nu a răspuns")
        return {
            "activ": any(v.get("activ") for v in layers.values()),
            "bbox": CDSE_BASIN_BBOX, "straturi": layers,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "nota": "straturi satelitare de context; quality notice și data "
                    "observației rămân atașate fiecărei imagini",
        }
    return cached("cdse:land-context:v3", 6 * 3600, fetch)


def copernicus_land_map(kind):
    if kind not in CDSE_CONTEXT:
        raise KeyError("strat Copernicus necunoscut")
    layer = copernicus_land_context()["data"]["straturi"][kind]
    if not layer.get("activ") or not layer.get("thumbnail_url"):
        raise RuntimeError("stratul Copernicus nu are vizualizare recentă")
    # Serviciul public de thumbnails autorizează exact URL-ul publicat în STAC;
    # modificarea BBOX-ului poate răspunde 403. Păstrăm imaginea europeană
    # originală și nu pretindem că este un subset numeric al bazinului.
    url = layer["thumbnail_url"]

    def fetch():
        raw = http_get(url, timeout=60, binary=True)
        if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) < 500:
            raise RuntimeError("Copernicus nu a returnat o hartă PNG validă")
        return {"png_base64": base64.b64encode(raw).decode("ascii"),
                "data": layer.get("data"), "raw_sha256": hashlib.sha256(raw).hexdigest()}
    result = cached(f"cdse:land-map:v1:{kind}:{layer.get('data')}", 12 * 3600, fetch)
    return base64.b64decode(result["data"]["png_base64"])


# --------------- NASA Earthdata CMR: catalogul misiunilor complementare ---

CMR_GRANULES = "https://cmr.earthdata.nasa.gov/search/granules.json"
CMR_SOURCES = {
    "swot": {
        "short_name": "SWOT_L2_HR_RiverSP_reach_D", "title": "SWOT RiverSP Version D",
        "signal": "nivel, pantă, lățime, suprafață și debit derivat pe reach",
        "product_family": "swot_karin_riversp", "lookback_days": 120,
    },
    "smap": {
        "short_name": "SPL3SMP_E", "title": "SMAP Enhanced L3 9 km",
        "signal": "umiditatea zilnică a solului, arhivă din 2015",
        "product_family": "smap_lband_soil_moisture", "lookback_days": 30,
    },
    "icesat2": {
        "short_name": "ATL13", "title": "ICESat-2 ATL13",
        "signal": "nivel al apei măsurat cu laser, control punctual",
        "product_family": "icesat2_atlas_altimetry", "lookback_days": 730,
    },
    "nisar": {
        "short_name": "NISAR_L3_SME2_PROVISIONAL_V1", "title": "NISAR SME2 provisional",
        "signal": "umiditatea solului la scară de câmp; produs nou, shadow only",
        "product_family": "nisar_lband_soil_moisture", "lookback_days": 120,
    },
}


def _earthdata_token_present():
    if os.environ.get("EARTHDATA_TOKEN"):
        return True
    return os.path.isfile(os.path.join(BASE_DIR, "data", "keys", "earthdata.token"))


def _cmr_source_status(source_id, spec):
    start = (date.today() - timedelta(days=spec["lookback_days"])).isoformat()
    query = urllib.parse.urlencode({
        "short_name": spec["short_name"],
        "bounding_box": ",".join(str(v) for v in CDSE_BASIN_BBOX),
        "temporal": f"{start}T00:00:00Z,{date.today().isoformat()}T23:59:59Z",
        "page_size": 3, "sort_key": "-start_date",
    }, safe=",/:" )
    response = http_json(f"{CMR_GRANULES}?{query}", timeout=45)
    entries = (response.get("feed") or {}).get("entry") or []
    if not entries:
        return {"activ": False, "catalog_activ": True,
                "motiv": f"fără granule în ultimele {spec['lookback_days']} zile"}
    latest = max(entries, key=lambda e: e.get("time_start") or "")
    observed = latest.get("time_start")
    observed_day = observed[:10] if observed else None
    age = (date.today() - date.fromisoformat(observed_day)).days if observed_day else None
    return {
        "activ": True, "catalog_activ": True, "id": source_id,
        "title": spec["title"], "short_name": spec["short_name"],
        "signal": spec["signal"], "product_family": spec["product_family"],
        "ultima_granula": latest.get("producer_granule_id") or latest.get("title"),
        "observation_time": observed, "data": observed_day, "vechime_zile": age,
        "download_configurat": _earthdata_token_present(),
        "access": "metadate publice; fișiere cu Earthdata Login gratuit",
        "mode": "catalog_only",
        "parser_version": "cmr-granules-v1",
    }


def earthdata_satellite_catalog():
    def fetch():
        sources = {}
        for source_id, spec in CMR_SOURCES.items():
            try:
                sources[source_id] = _cmr_source_status(source_id, spec)
            except Exception as exc:
                sources[source_id] = {"activ": False, "catalog_activ": False,
                                      "title": spec["title"], "motiv": str(exc)[:120]}
        if not any(s.get("catalog_activ") for s in sources.values()):
            raise RuntimeError("catalogul NASA CMR nu a răspuns pentru nicio misiune")
        return {
            "activ": any(s.get("catalog_activ") for s in sources.values()),
            "sources": sources,
            "download_configurat": _earthdata_token_present(),
            "nota": "catalogul confirmă existența și prospețimea granulelor; "
                    "nu pretinde că valorile protejate au fost încă ingerate",
        }
    return cached("earthdata:catalog:v3", 6 * 3600, fetch)


# ---------------- registru de proveniență și dependență între toate sursele -

EVIDENCE_SOURCES = {
    "pegelonline": {"provider": "WSV / VIA DONAU", "kind": "masurat",
                    "family": "gauge_de_at", "mode": "active"},
    "inhga": {"provider": "INHGA", "kind": "masurat",
              "family": "gauge_ro_inhga", "mode": "active"},
    "inhga_tributaries": {"provider": "INHGA", "kind": "prognoza_oficiala",
                           "family": "inhga_monthly_basin_forecast",
                           "mode": "active_context"},
    "afdj": {"provider": "AFDJ", "kind": "masurat",
             "family": "gauge_ro_afdj", "mode": "active"},
    "rhmz": {"provider": "RHMZ Serbia", "kind": "masurat",
             "family": "gauge_rs_rhmz", "mode": "active"},
    "hydroinfo": {"provider": "OVF Hungary", "kind": "masurat",
                  "family": "gauge_hu_ovf", "mode": "active"},
    "danubehis": {"provider": "ICPDR, valori OVF", "kind": "masurat_retransmis",
                  "family": "gauge_hu_ovf", "mode": "active"},
    "danubestream": {"provider": "FAIRway / administrații naționale", "kind": "masurat_agregat",
                     "family": "navigation_gauge_aggregation", "mode": "active"},
    "glofas": {"provider": "Copernicus CEMS / Open-Meteo delivery", "kind": "model",
               "family": "lisflood_glofas", "mode": "active"},
    "era5": {"provider": "Copernicus C3S / Open-Meteo delivery", "kind": "reanaliza",
             "family": "era5_reanalysis", "mode": "active"},
    "edo": {"provider": "Copernicus EDO", "kind": "model_compozit",
            "family": "copernicus_drought_lisflood", "mode": "context"},
    "hydroweb": {"provider": "CNES/Theia", "kind": "masurat_orbita",
                 "family": "radar_altimetry_missions", "mode": "active_shadow"},
    "dahiti": {"provider": "DGFI-TUM", "kind": "masurat_orbita_procesare_alternativa",
               "family": "radar_altimetry_missions", "mode": "optional_free_key"},
    "opera_s1": {"provider": "NASA/JPL OPERA", "kind": "clasificare_satelit",
                 "family": "sentinel1_scene_opera", "mode": "active_shadow"},
    "opera_hls": {"provider": "NASA/JPL OPERA", "kind": "clasificare_satelit",
                  "family": "hls_scene_opera", "mode": "active_shadow"},
    "clms_snow": {"provider": "Copernicus Land / CDSE", "kind": "observatie_satelit",
                  "family": "modis_viirs_snow", "mode": "active_context"},
    "clms_soil": {"provider": "Copernicus Land / CDSE", "kind": "observatie_satelit",
                  "family": "sentinel1_soil_moisture", "mode": "active_context"},
    "grace": {"provider": "GRACE/GRACE-FO, SAGSA/Theia", "kind": "masurat_orbita",
              "family": "grace_gravimetry", "mode": "active_lagged"},
    "swot_direct": {"provider": "NASA/JPL PO.DAAC", "kind": "produs_satelit",
                    "family": "swot_karin_riversp", "mode": "catalog_only_free_account"},
    "smap": {"provider": "NASA NSIDC", "kind": "produs_satelit",
             "family": "smap_lband_soil", "mode": "catalog_only_free_account"},
    "icesat2": {"provider": "NASA NSIDC", "kind": "masurat_laser",
                "family": "icesat2_atlas", "mode": "catalog_only_free_account"},
    "nisar": {"provider": "NASA/ISRO ASF", "kind": "produs_satelit_provisional",
              "family": "nisar_lband_soil", "mode": "catalog_only_free_account"},
    "jrc_global_surface_water": {"provider": "EC JRC", "kind": "baseline_satelit",
                                 "family": "landsat_surface_water_history",
                                 "mode": "documented_baseline",
                                 "url": "https://global-surface-water.appspot.com/download"},
    "clms_fsc_sws": {"provider": "Copernicus Land", "kind": "zapada_high_resolution",
                     "family": "sentinel1_sentinel2_snow", "mode": "documented_free_account",
                     "url": "https://land.copernicus.eu/en/products/snow"},
    "grdc": {"provider": "Global Runoff Data Centre / WMO", "kind": "masurat_istoric",
             "family": "grdc_in_situ_discharge", "mode": "optional_noncommercial_request",
             "url": "https://portal.grdc.bafg.de",
             "terms": "date brute numai pentru cercetare; fără redistribuire"},
    "grdc_wmo_2024": {"provider": "GRDC / WMO via Zenodo", "kind": "masurat_istoric",
                      "family": "grdc_in_situ_discharge", "mode": "documented_cc_by_nc_snapshot",
                      "url": "https://doi.org/10.5281/zenodo.19126732",
                      "coverage": "1991-2024; fără stație pe cursul principal al Dunării în ediția verificată"},
}

EVIDENCE_DEPENDENCIES = [
    {"members": ["inhga", "inhga_tributaries"],
     "relationship": "același emitent; măsurarea Baziaș și prognoza pe bazine sunt produse diferite, nu voturi instituționale independente",
     "count_as": 1},
    {"members": ["hydroinfo", "danubehis"],
     "relationship": "aceleași măsurători OVF, două căi de livrare",
     "count_as": 1},
    {"members": ["afdj", "danubestream"],
     "relationship": "posibilă retransmitere a aceleiași mire naționale; cross-check de livrare, nu independență garantată",
     "count_as": 1},
    {"members": ["hydroweb", "dahiti"],
     "relationship": "procesări diferite pot folosi aceleași misiuni de altimetrie",
     "count_as": 1},
    {"members": ["hydroweb", "swot_direct"],
     "relationship": "HydroWeb poate include SWOT; granula directă aduce flags/proveniență, nu un senzor nou",
     "count_as": 1},
    {"members": ["opera_s1", "clms_soil"],
     "relationship": "produse diferite care pot porni din aceeași scenă Sentinel-1",
     "count_as": 1},
    {"members": ["glofas", "edo"],
     "relationship": "produse Copernicus/LISFLOOD corelate; nu sunt două modele complet independente",
     "count_as": 1},
    {"members": ["grace"],
     "relationship": "un al doilea procesor GRACE validează procesarea, nu adaugă o misiune independentă",
     "count_as": 1},
    {"members": ["grdc", "grdc_wmo_2024"],
     "relationship": "același furnizor și aceleași observații in-situ pot apărea în produse cu licențe și intervale diferite",
     "count_as": 1},
]


def evidence_source_registry():
    modes = {}
    for source in EVIDENCE_SOURCES.values():
        modes[source["mode"]] = modes.get(source["mode"], 0) + 1
    return {
        "sources": EVIDENCE_SOURCES,
        "dependencies": EVIDENCE_DEPENDENCIES,
        "summary": {"sources": len(EVIDENCE_SOURCES), "by_mode": modes},
        "rule": "membrii aceleiași dependențe nu se însumează ca voturi independente",
    }


# ---------------------------- gravimetrie GRACE (apa totală din bazin) -------
# Produs SAGSA/Theia din misiunile GRACE/GRACE-FO: anomalia lunară a apei
# totale (suprafață + sol + subteran), grilă 1°, în km³/celulă. Decalaj de
# publicare ~1 an — util ca tendință a rezervei, nu ca „azi". Necesită h5py.

GRAV_BOX = {"lat_min": 43.0, "lat_max": 49.5, "lon_min": 8.0, "lon_max": 30.0}

# segmente de bazin — granularitatea maximă onestă la rezoluția GRACE (~300 km);
# semnalul „curge" între casete vecine, deci valorile pe segment sunt orientative
GRAV_SEGMENTS = {
    "superior": {"name": "superior (DE/AT, Alpi)",           "lat_min": 46.0, "lat_max": 50.0, "lon_min": 8.0,  "lon_max": 17.0},
    "mijlociu": {"name": "mijlociu (SK/HU, Sava/Drava/Tisa)", "lat_min": 44.0, "lat_max": 49.5, "lon_min": 17.0, "lon_max": 23.0},
    "inferior": {"name": "inferior (RO/BG/MD)",              "lat_min": 43.0, "lat_max": 48.5, "lon_min": 23.0, "lon_max": 30.0},
}


def _grav_month_values(nc_bytes):
    """Suma anomaliei (km³) pe caseta totală + pe cele trei segmente."""
    import h5py
    import tempfile
    boxes = {"total": GRAV_BOX, **GRAV_SEGMENTS}
    sums = {k: 0.0 for k in boxes}
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=True) as tmp:
        tmp.write(nc_bytes)
        tmp.flush()
        with h5py.File(tmp.name, "r") as f:
            lat = f["latitude"][:]
            lon = f["longitude"][:]
            tw = f["total_water"][0]
            mask = f["land_mask"][:]
            for i, la in enumerate(lat):
                if not (42.5 <= la <= 50.5):
                    continue
                for j, lo in enumerate(lon):
                    if not (7.5 <= lo <= 30.5):
                        continue
                    v = tw[i, j]
                    if mask[i, j] != 1 or v != v:
                        continue
                    for k, b in boxes.items():
                        if (b["lat_min"] <= la <= b["lat_max"]
                                and b["lon_min"] <= lo <= b["lon_max"]):
                            sums[k] += float(v)
    return {k: round(s, 2) for k, s in sums.items()}


def hydroweb_gravimetry(luni=290):
    key = _hydroweb_key()
    if not key:
        return {"activ": False, "motiv": "lipsește cheia hydroweb.next"}
    try:
        import h5py  # noqa: F401
    except ImportError:
        return {"activ": False,
                "motiv": "necesită parserul HDF5: pip3 install h5py"}

    def fetch():
        res = _hydroweb_get(
            f"{HYDROWEB_STAC}/search?collections=GRAVIMETRY_TOTAL_WATER"
            f"&sortby=-properties.datetime&limit={luni}", key)
        serie = []
        for f in res.get("features", []):
            m = re.search(r"_(\d{4}-\d{2})@", f.get("id", ""))
            if not m:
                continue
            luna = m.group(1)
            hit = cache_get(f"grav2:{luna}", max_age=10 ** 9)
            if hit:
                serie.append({"luna": luna, **hit["data"]})
                continue
            nc = next((a for k, a in (f.get("assets") or {}).items()
                       if k.endswith(".nc")), None)
            if not nc:
                continue
            try:
                vals = _grav_month_values(_hydroweb_get(nc["href"], key, raw=True))
                cache_put(f"grav2:{luna}", vals, 10 ** 9)
                serie.append({"luna": luna, **vals})
            except Exception:
                continue
        if not serie:
            raise RuntimeError("nicio lună de gravimetrie descărcabilă")
        serie.sort(key=lambda x: x["luna"])
        ultima = serie[-1]

        def rank(camp):
            ref = [s[camp] for s in serie[:-1]
                   if s["luna"][5:] == ultima["luna"][5:] and camp in s]
            return {"anomalie_km3": ultima.get(camp),
                    "mai_seci": sum(1 for v in ref if v < ultima.get(camp, 0)),
                    "ani": len(ref)}

        segmente = {k: {**rank(k), "nume": v["name"]}
                    for k, v in GRAV_SEGMENTS.items()}
        return {"activ": True, "serie": serie,
                "ultima": {"luna": ultima["luna"],
                           "anomalie_km3": ultima.get("total")},
                "ani_mai_seci_aceeasi_luna": rank("total")["mai_seci"],
                "ani_comparati": rank("total")["ani"],
                "segmente": segmente,
                "caseta": GRAV_BOX,
                "nota": "anomalia apei totale (suprafață+sol+subteran) în km³; "
                        "segmentele sunt orientative — rezoluția reală GRACE e "
                        "~300 km, semnalul se amestecă între casete vecine; "
                        "decalaj de publicare ~1 an"}

    return cached("gravimetrie", 7 * 86400, fetch)


# ------------------------------------------------ GRDC (istoric măsurat) --
# Global Runoff Data Centre (Koblenz): serii MĂSURATE multidecenale. Pentru
# comparația de la intrarea în deltă selectăm explicit Ceatal Izmail (6742900),
# nu primul fișier alfabetic dintr-un pachet cu mai multe stații. Datele clasice
# se cer gratuit, numai pentru uz necomercial, pe portal.grdc.bafg.de; fișierul
# *_Q_Day.Cmd.txt primit se pune în data/grdc/ și rămâne local.

GRDC_DIR = os.path.join(BASE_DIR, "data", "grdc")
GRDC_CEATAL_ID = "6742900"


def _grdc_parse_file(path):
    """Parsează exportul zilnic GRDC ASCII fără a pierde metadatele/proveniența."""
    series = {}
    metadata = {}
    digest = hashlib.sha256()
    with open(path, "rb") as raw:
        payload = raw.read()
    digest.update(payload)
    text = payload.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("#"):
            if ":" in line:
                key, value = line.lstrip("# ").split(":", 1)
                metadata[key.strip().lower()] = value.strip()
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2});[^;]*;\s*(-?\d+(?:\.\d+)?)", line)
        if m and float(m.group(2)) > -900:
            series[m.group(1)] = float(m.group(2))
    station_id = metadata.get("grdc-no.", "")
    return {
        "path": path,
        "fisier": os.path.basename(path),
        "station_id": station_id,
        "statie": metadata.get("station", os.path.basename(path)),
        "rau": metadata.get("river"),
        "tara": metadata.get("country"),
        "interval_declarat": metadata.get("time series"),
        "ultima_actualizare": metadata.get("last update"),
        "raw_sha256": digest.hexdigest(),
        "_serie": series,
    }


def grdc_series(station_id=GRDC_CEATAL_ID):
    if not os.path.isdir(GRDC_DIR):
        return {"activ": False, "motiv": "director data/grdc/ inexistent"}
    files = [f for f in sorted(os.listdir(GRDC_DIR))
             if f.lower().endswith((".txt", ".csv", ".day"))]
    if not files:
        return {"activ": False,
                "motiv": "Niciun export zilnic GRDC în data/grdc/. Cere seria "
                         "Ceatal Izmail (GRDC 6742900) în format GRDC ASCII și "
                         "păstrează fișierul primit local."}

    parsed = [_grdc_parse_file(os.path.join(GRDC_DIR, f)) for f in files]
    selected = next((item for item in parsed if item["station_id"] == station_id), None)
    if selected is None:
        selected = next((item for item in parsed
                         if station_id in item["fisier"]), None)
    if selected is None:
        found = [{"grdc_id": item["station_id"] or None, "statie": item["statie"],
                  "fisier": item["fisier"]} for item in parsed]
        return {"activ": False,
                "motiv": f"Lipsește stația țintă Ceatal Izmail (GRDC {station_id}); "
                         f"am găsit {len(found)} alte exporturi.",
                "statii_gasite": found,
                "termeni": "uz necomercial; datele brute nu se redistribuie"}

    series = selected.pop("_serie")
    if len(series) < 3650:
        return {"activ": False,
                "motiv": f"fișierul {selected['fisier']} are doar {len(series)} zile valide "
                         "— seria este prea scurtă sau formatul nu este cel zilnic GRDC",
                "grdc_id": selected["station_id"] or station_id}
    dates = sorted(series)
    return {"activ": True, "statie": selected["statie"],
            "grdc_id": selected["station_id"] or station_id,
            "rau": selected["rau"], "tara": selected["tara"],
            "fisier": selected["fisier"], "raw_sha256": selected["raw_sha256"],
            "din": dates[0], "pana": dates[-1], "zile": len(series),
            "interval_declarat": selected["interval_declarat"],
            "ultima_actualizare": selected["ultima_actualizare"],
            "termeni": "uz necomercial; datele brute rămân locale și nu se redistribuie",
            "sursa": "The Global Runoff Data Centre, 56068 Koblenz, Germany",
            "parser_version": "grdc_ascii_daily_v2", "_serie": series}


# ------------------------------------------------------------- overview ----

def _latest_valid(times, values):
    today = date.today().isoformat()
    best = None
    for t, v in zip(times, values):
        if v is not None and t <= today:
            best = (t, v)
    return best


def overview():
    """Profilul longitudinal: ultimele valori din fiecare sursă, de la
    Germania până la deltă."""
    result = {"pegelonline": None, "glofas": [], "inhga": None, "errors": {}}

    try:
        pg = pegelonline_stations()
        result["pegelonline"] = {"stations": pg["data"], "stale": pg["stale"]}
    except Exception as exc:
        result["errors"]["pegelonline"] = str(exc)

    for pid in GLOFAS_POINTS:
        try:
            r = glofas_recent(pid, past_days=10, forecast_days=3)
            latest = _latest_valid(r["data"]["time"], r["data"]["discharge"])
            p = GLOFAS_POINTS[pid]
            result["glofas"].append({
                "id": pid, "name": p["name"], "km": p["km"],
                "country": p["country"],
                "date": latest[0] if latest else None,
                "discharge_m3s": latest[1] if latest else None,
                "tip_proba": "model_hidrologic",
                "rezolutie_spatiala_aprox_km": 5,
                "celula_model": r["data"].get("cell"),
                "stale": r["stale"],
            })
        except Exception as exc:
            result["errors"][f"glofas:{pid}"] = str(exc)

    try:
        b = inhga_bulletin()
        result["inhga"] = {
            **b["data"],
            "stale": b["stale"],
            "cache_age_s": b.get("cache_age_s"),
        }
    except Exception as exc:
        result["errors"]["inhga"] = str(exc)

    return result
