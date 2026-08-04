"""Conectori către sursele oficiale de date pentru Dunăre.

Fiecare conector returnează dict-uri JSON-serializabile și trece printr-un
cache SQLite cu TTL, ca să nu lovim serverele oficiale la fiecare refresh.
Doar biblioteca standard — fără dependențe externe.
"""

import json
import os
import re
import sqlite3
import ssl
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "cache.db")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) DanubeMonitor/1.0 (uz personal, date oficiale)"

_db_lock = threading.Lock()
# single-flight: o singură reîmprospătare per cheie, oricâte cereri simultane
_inflight_lock = threading.Lock()
_inflight = {}

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
        for mort in ("grav:%", "era5:%"):
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
        lock = _inflight.setdefault(key, threading.Lock())
    with lock:
        hit = cache_get(key)   # altcineva a adus datele cât am așteptat
        if hit:
            return {"data": hit["data"], "cache_age_s": int(hit["age"]), "stale": False}
        return _fetch_and_store(key, ttl, fetch_fn, stale_ok)


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
             "end_date": end, "daily": "precipitation_sum", "timezone": "UTC"})
        d = http_json(f"{ARCHIVE_API}?{qs}")
        return {"time": d["daily"]["time"],
                "precip": d["daily"]["precipitation_sum"]}

    return cached(f"era5pt:{tag}:{start_year}", 24 * 3600, fetch)


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
             "daily": "precipitation_sum,snowfall_sum", "timezone": "UTC"}
        )
        d = http_json(f"{ARCHIVE_API}?{qs}")
        return {"time": d["daily"]["time"],
                "precip": d["daily"]["precipitation_sum"],
                "snow": d["daily"].get("snowfall_sum"),
                "point": p}

    return cached(f"era5v2:{point_id}:{start_year}", 24 * 3600, fetch)


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


def _strip_tags(html):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = text.replace("&nbsp;", " ").replace("&#8211;", "–").replace("&amp;", "&")
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
        debit = grab(r"Baziaș\)\s*a fost.{0,120}?valoarea de\s*([\d.,]+)\s*m")
        trend = grab(r"Baziaș\)\s*a fost în\s*([^\s,]+(?:\s+ușoară)?)")
        medie = grab(r"media multianuală a lunii \w+\s*\(?\s*([\d.,]+)\s*m")
        prognoza = grab(r"Baziaș\)\s*va fi.{0,160}?valoarea de\s*([\d.,]+)\s*m")

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

    return cached("inhga_bulletin", 3 * 3600, fetch)


INHGA_DAILY = ("https://www.hidro.ro/bulletin/diagnoza-si-prognoza-hidrologica-"
               "pentru-dunare-la-intrarea-in-tara-si-pe-sectorul-romanesc-{d}/")


def _parse_inhga_html(html):
    text = _strip_tags(html)
    t = (text.replace("ş", "ș").replace("ţ", "ț")
             .replace("Ş", "Ș").replace("Ţ", "Ț"))
    t = re.sub(r"m\s*\n\s*3\s*\n\s*/s", " m³/s", t)
    m = re.search(r"Baziaș\)\s*a fost[^.]*?valoarea de\s*([\d.,]+)\s*m", t, re.S | re.I)
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
                "motiv": "Lipsește ENTSOE_TOKEN (cheie gratuită de la "
                         "transparency.entsoe.eu). Fără ea, producția pe unități "
                         "la PF I/II nu poate fi interogată."}

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


def hydroweb_danube(max_statii=12):
    key = _hydroweb_key()
    if not key:
        return {"activ": False,
                "motiv": "Lipsește cheia hydroweb.next (cont gratuit pe "
                         "hydroweb.next.theia-land.fr → API key în "
                         "data/keys/hydroweb.key sau env HYDROWEB_KEY)."}

    def fetch():
        res = _hydroweb_get(
            f"{HYDROWEB_STAC}/search?collections=HYDROWEB_RIVERS_OPE"
            "&bbox=20.4,43.5,29.9,48.6&limit=200", key)
        feats = [f for f in res.get("features", [])
                 if "_DUNAREA_" in (f.get("id") or "")]
        feats.sort(key=lambda f: f.get("id", ""))
        statii = []
        for f in feats[:max_statii]:
            sid = f["id"].split("@")[0]
            coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
            txt_asset = next((a for k, a in (f.get("assets") or {}).items()
                              if k.endswith(".txt")), None)
            entry = {"statie": sid, "lon": coords[0], "lat": coords[1]}
            if txt_asset:
                try:
                    text = _hydroweb_get(txt_asset["href"], key, raw=True) \
                        .decode("utf-8", "replace")
                    serie = _parse_hydroweb_txt(text)
                    if serie:
                        d, v, sig = serie[-1]
                        entry.update({"data": d, "nivel_m": v,
                                      "incertitudine_m": sig,
                                      "observatii": len(serie)})
                        luna = d[5:7]
                        ref = [x[1] for x in serie[:-1] if x[0][5:7] == luna]
                        if len(ref) >= 5:
                            below = sum(1 for r in ref if r <= v)
                            entry["percentila_lunii"] = round(100 * below / len(ref), 1)
                            entry["ani_serie"] = len({x[0][:4] for x in serie})
                        if len(serie) >= 2:
                            entry["variatie_fata_de_precedenta_m"] = round(
                                v - serie[-2][1], 2)
                except Exception as exc:
                    entry["eroare"] = str(exc)[:60]
            statii.append(entry)
        if not statii:
            raise RuntimeError("nicio stație virtuală _DUNAREA_ găsită")
        return {"activ": True, "statii": statii,
                "colectie": "HYDROWEB_RIVERS_OPE (Theia/CNES)",
                "nota": "niveluri în metri față de geoid — se compară variația "
                        "și percentila proprie, nu cu cota mirei"}

    return cached("hydroweb", 12 * 3600, fetch)


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


# ---------------------------------------------------- GRDC (istoric secular) --
# Global Runoff Data Centre (Koblenz): serii MĂSURATE lungi (Ceatal Izmail din
# sec. XIX). Datele se cer gratuit pe portal.grdc.bafg.de; fișierul primit
# (*_Q_Day.Cmd.txt sau similar) se pune în data/grdc/ și e citit de aici.

GRDC_DIR = os.path.join(BASE_DIR, "data", "grdc")


def grdc_series():
    if not os.path.isdir(GRDC_DIR):
        return {"activ": False, "motiv": "director data/grdc/ inexistent"}
    files = [f for f in sorted(os.listdir(GRDC_DIR))
             if f.lower().endswith((".txt", ".csv", ".day"))]
    if not files:
        return {"activ": False,
                "motiv": "Niciun fișier în data/grdc/. Cere gratuit seria zilnică "
                         "(ex. stația Ceatal Izmail) pe portal.grdc.bafg.de și "
                         "pune fișierul primit aici."}
    path = os.path.join(GRDC_DIR, files[0])
    series = {}
    station = files[0]
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#"):
                m = re.search(r"Station:\s*(.+)", line)
                if m:
                    station = m.group(1).strip()
                continue
            m = re.match(r"(\d{4}-\d{2}-\d{2});[^;]*;\s*(-?\d+(?:\.\d+)?)", line)
            if m and float(m.group(2)) > -900:
                series[m.group(1)] = float(m.group(2))
    if len(series) < 3650:
        return {"activ": False,
                "motiv": f"fișierul {files[0]} are doar {len(series)} zile valide "
                         "— nu pare o serie GRDC zilnică"}
    dates = sorted(series)
    return {"activ": True, "statie": station, "fisier": files[0],
            "din": dates[0], "pana": dates[-1], "zile": len(series),
            "_serie": series}


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
                "stale": r["stale"],
            })
        except Exception as exc:
            result["errors"][f"glofas:{pid}"] = str(exc)

    try:
        b = inhga_bulletin()
        result["inhga"] = b["data"]
        result["inhga"]["stale"] = b["stale"]
    except Exception as exc:
        result["errors"]["inhga"] = str(exc)

    return result
