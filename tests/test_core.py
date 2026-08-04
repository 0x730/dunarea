import os
import sqlite3
import struct
import tempfile
import time
import unittest
import zlib
from datetime import date, timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import analiza_ai
import anomalii
import connectors as C


class StaticOptionsPageTests(unittest.TestCase):
    def test_options_page_keeps_audited_status_and_rejects_retired_claims(self):
        page = Path(__file__).parents[1].joinpath("static/index.html").read_text()
        section = page.split('<section id="sec-solutii"', 1)[1].split("</section>", 1)[0]

        self.assertIn("audit 04.08.2026", section)
        self.assertIn("termen 2026 neconfirmat", section)
        self.assertIn("procedură de mediu documentată", section)
        self.assertIn("aprobată pentru finanțare", section)
        self.assertIn("Regulamentul (UE) 2020/741", section)
        self.assertIn("Starea celor opt proiecte Hidroelectrica", section)
        self.assertIn("funcție energetică\n        secundară", section)
        self.assertIn("debit pentru irigații în perioadele de secetă", section)
        self.assertIn("590.889.477 RON", section)
        self.assertIn("Treapta Cireșu–Surduc", section)
        self.assertIn("Nu amestecăm treapta cu întreaga schemă", section)
        self.assertIn("confirmate la 31.12.2025", section)
        self.assertIn("Raport-FY-2025-merged.pdf", section)
        self.assertNotIn("confirmate la 31.12.2024", section)
        self.assertIn("pierderilor măsurate", section)
        self.assertNotIn("jurnalul.ro", section)
        self.assertNotIn("capital.ro", section)
        self.assertNotIn("sub 10% din arabil", section)
        self.assertNotIn("198 km", section)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = C.DB_PATH
        C.DB_PATH = os.path.join(self.tmp.name, "cache.db")
        with C._inflight_lock:
            C._inflight.clear()
            C._last_fail.clear()

    def tearDown(self):
        C.DB_PATH = self.old_db_path
        with C._inflight_lock:
            C._inflight.clear()
            C._last_fail.clear()
        self.tmp.cleanup()

    def test_stale_fallback_uses_failure_backoff(self):
        C.cache_put("source", {"old": True}, ttl=1)
        with sqlite3.connect(C.DB_PATH) as conn:
            conn.execute("UPDATE cache SET fetched_at=? WHERE key='source'",
                         (time.time() - 10,))

        calls = 0

        def failing_fetch():
            nonlocal calls
            calls += 1
            raise OSError("offline")

        first = C.cached("source", 1, failing_fetch)
        second = C.cached("source", 1, failing_fetch)

        self.assertTrue(first["stale"])
        self.assertTrue(second["stale"])
        self.assertEqual(calls, 1)


class ConnectorTests(unittest.TestCase):
    @staticmethod
    def _png_rgba(rows):
        height, width = len(rows), len(rows[0])
        payload = b"".join(b"\x00" + b"".join(bytes(pixel) for pixel in row)
                           for row in rows)

        def chunk(kind, data):
            return (struct.pack(">I", len(data)) + kind + data +
                    struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
                chunk(b"IDAT", zlib.compress(payload)) + chunk(b"IEND", b""))

    def test_era5_requests_are_pinned_and_versioned(self):
        urls = []
        keys = []

        def fake_json(url, timeout=25):
            urls.append(url)
            return {"daily": {"time": ["2020-01-01"],
                              "precipitation_sum": [1.0],
                              "snowfall_sum": [0.0]}}

        def no_cache(key, ttl, fetch_fn, stale_ok=True):
            keys.append(key)
            return {"data": fetch_fn(), "stale": False}

        with mock.patch.object(C, "http_json", side_effect=fake_json), \
                mock.patch.object(C, "cached", side_effect=no_cache):
            C.era5_precip("oltenia", 2020)
            C.era5_point("probe", 48.0, 13.0, 2020)

        self.assertEqual([parse_qs(urlparse(u).query)["models"] for u in urls],
                         [["era5"], ["era5"]])
        self.assertTrue(keys[0].startswith("era5v3:"))
        self.assertTrue(keys[1].startswith("era5pt:v3:"))

    def test_hydroinfo_parser_extracts_daily_danube_measurement(self):
        def row(values):
            return "<tr>" + "".join(f"<td><font>{v}</font></td>" for v in values) + "</tr>"

        page = "Observed on: 04 August 2026" + row([
            "442027", "Budapest", "Danube", "41", "40", "38", "-3",
            "868", "23.5", "//",
        ]) + row([
            "444228", "Tokaj", "Tisza", "461", "459", "455", "-6",
            "80", "25.9", "//",
        ])

        out = C._parse_hydroinfo_html(page)

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["statie"], "Budapest")
        self.assertEqual(out[0]["data"], "2026-08-04")
        self.assertEqual(out[0]["debit_m3s"], 868.0)
        self.assertEqual(out[0]["km"], 1647)

    def test_era5_batch_uses_one_multi_coordinate_request(self):
        urls = []

        def fake_json(url, timeout=25):
            urls.append(url)
            return [
                {"daily": {"time": ["2020-01-01"],
                           "precipitation_sum": [1.0], "snowfall_sum": [0.0]}},
                {"daily": {"time": ["2020-01-01"],
                           "precipitation_sum": [2.0], "snowfall_sum": [0.5]}},
            ]

        def no_cache(key, ttl, fetch_fn, stale_ok=True):
            return {"data": fetch_fn(), "stale": False}

        points = [("one", 48.0, 13.0), ("two", 47.0, 12.0)]
        with mock.patch.object(C, "http_json", side_effect=fake_json), \
                mock.patch.object(C, "cached", side_effect=no_cache):
            out = C._era5_batch("test", points, 2020, include_snow=True)

        self.assertEqual(len(urls), 1)
        query = parse_qs(urlparse(urls[0]).query)
        self.assertEqual(query["latitude"], ["48.0,47.0"])
        self.assertEqual(query["models"], ["era5"])
        self.assertEqual(out["data"]["two"]["snow"], [0.5])

    def test_historical_inhga_parser_allows_punctuation_before_value(self):
        page = ("<p>Baziaș) a fost în scădere la ora 06.00, situându-se sub "
                "media lunii, în jurul valorii de 1.234 m3/s.</p>")
        self.assertEqual(C._parse_inhga_html(page), 1234.0)

    def test_danubehis_parser_extracts_public_current_value(self):
        page = """
        <tr class="sync-id-id">
          <td class="name">Budapest HU</td>
          <td data-sort-value="1597183200">12.08.2020</td>
          <td data-sort-value="1785794400">04.08.2026</td>
          <td data-sort-value="7532">753.2</td>
          <td><a href="/results/HU442027_HYDRO?symbol%5BQ%5D=Q">view results</a></td>
        </tr>
        """

        out = C._parse_danubehis_current(page)

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["cod"], "HU442027_HYDRO")
        self.assertEqual(out[0]["statie"], "Budapest")
        self.assertEqual(out[0]["debit_m3s"], 753.2)
        self.assertEqual(out[0]["data"], "2026-08-04")
        self.assertEqual(out[0]["km"], 1647)

    def test_edo_status_prefers_current_service_page_date(self):
        capabilities = """<WMT_MS_Capabilities><Capability><Layer>
          <Layer><Name>cdiad</Name><Extent name="time">2012-01-01/2026-06-11/P10D</Extent></Layer>
          <Layer><Name>smian</Name><Extent name="time">1995-01-01/2026-07-01/P10D</Extent></Layer>
        </Layer></Capability></WMT_MS_Capabilities>"""
        page = ("LAYERS=cdiad&amp;TIME=2026-07-11 "
                "LAYERS=smian&amp;TIME=2026-07-21")

        out = C._parse_edo_status(capabilities, page)

        self.assertEqual(out["cdi"]["data"], "2026-07-11")
        self.assertEqual(out["soil"]["data"], "2026-07-21")

    def test_hydroweb_selection_uses_mainstem_aliases_and_full_span(self):
        def feature(name, km):
            return {"id": f"R_DANUBE_{name}_KM{km:04d}@catalog:v1",
                    "assets": {"series.txt": {"href": "https://example/series.txt"}}}

        features = [feature("DONAU", 2500), feature("DUNAJ", 2050),
                    feature("DUNA", 1600), feature("DUNAV", 1100),
                    feature("DUNAREA", 500), feature("DANUBE", 100),
                    feature("DRAVA", 1500)]
        selected = C._select_hydroweb_features(features, 3)
        kms = [C._hydroweb_feature_km(f) for f in selected]

        self.assertEqual(len(selected), 3)
        self.assertEqual(max(kms), 2500)
        self.assertEqual(min(kms), 100)
        self.assertNotIn(None, kms)

    def test_hydroweb_station_quality_is_explicit(self):
        feature = {"id": "R_DANUBE_DUNA_KM1600@catalog:v1",
                   "geometry": {"coordinates": [19.0, 47.0]}}
        lines = [f"{year}-08-04 00:00 {1 + i / 10:.2f} 0.05"
                 for i, year in enumerate(range(2020, 2027))]
        entry = C._hydroweb_station_entry(feature, "\n".join(lines))

        self.assertEqual(entry["km"], 1600)
        self.assertEqual(entry["segment"], "mijlociu")
        self.assertEqual(entry["quality_flags"], [])
        self.assertTrue(entry["eligibila_detector"])
        self.assertEqual(len(entry["raw_sha256"]), 64)

    def test_png_stats_count_opera_classes_without_pillow(self):
        raw = self._png_rgba([
            [(0, 0, 0, 0), (255, 255, 255, 255)],
            [(0, 0, 255, 255), (0, 255, 0, 255)],
        ])
        out = C._png_rgba_stats(raw)

        self.assertEqual(out["no_data"], 1)
        self.assertEqual(out["open_water"], 1)
        self.assertEqual(out["inundated_vegetation"], 1)
        self.assertEqual(out["coverage_pct"], 75.0)
        self.assertEqual(out["water_like_pct"], 66.67)

    def test_cmr_catalog_reports_metadata_not_consumed_values(self):
        payload = {"feed": {"entry": [{
            "title": "granule-title", "producer_granule_id": "granule-id",
            "time_start": "2026-08-03T00:00:00.000Z",
        }]}}
        spec = {"short_name": "TEST", "title": "Test mission",
                "signal": "test signal", "product_family": "test_family",
                "lookback_days": 30}
        with mock.patch.object(C, "http_json", return_value=payload), \
                mock.patch.object(C, "_earthdata_token_present", return_value=False):
            out = C._cmr_source_status("test", spec)

        self.assertTrue(out["catalog_activ"])
        self.assertEqual(out["mode"], "catalog_only")
        self.assertFalse(out["download_configurat"])
        self.assertEqual(out["ultima_granula"], "granule-id")

    def test_evidence_registry_deduplicates_same_provider_and_sensor_families(self):
        registry = C.evidence_source_registry()
        relations = {frozenset(d["members"]): d for d in registry["dependencies"]}

        self.assertEqual(C.EVIDENCE_SOURCES["hydroinfo"]["family"],
                         C.EVIDENCE_SOURCES["danubehis"]["family"])
        self.assertEqual(relations[frozenset(("hydroinfo", "danubehis"))]["count_as"], 1)
        self.assertEqual(relations[frozenset(("hydroweb", "swot_direct"))]["count_as"], 1)
        self.assertEqual(relations[frozenset(("grdc", "grdc_wmo_2024"))]["count_as"], 1)

    def test_grdc_selects_ceatal_in_multi_station_directory(self):
        def daily_file(station_id, station, value):
            header = (f"# GRDC-No.:              {station_id}\n"
                      f"# River:                 DANUBE\n"
                      f"# Station:               {station}\n"
                      "# Country:               RO\n"
                      "# Time series:           1991-01 - 2001-12\n"
                      "# Last update:           2025-01-01\n"
                      "YYYY-MM-DD;hh:mm; Value\n")
            rows = []
            start = date(1991, 1, 1)
            for offset in range(3653):
                day = start + timedelta(days=offset)
                rows.append(f"{day.isoformat()};--:--; {value:.3f}\n")
            return header + "".join(rows)

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "6242100_Q_Day.Cmd.txt"), "w") as fh:
                fh.write(daily_file("6242100", "LINZ", 1000))
            with open(os.path.join(tmp, "6742900_Q_Day.Cmd.txt"), "w") as fh:
                fh.write(daily_file("6742900", "CEATAL IZMAIL", 5000))
            with mock.patch.object(C, "GRDC_DIR", tmp):
                out = C.grdc_series()

        self.assertTrue(out["activ"])
        self.assertEqual(out["grdc_id"], "6742900")
        self.assertEqual(out["statie"], "CEATAL IZMAIL")
        self.assertEqual(out["_serie"]["1991-01-01"], 5000)
        self.assertEqual(len(out["raw_sha256"]), 64)


class AnomalySourceTests(unittest.TestCase):
    def test_hungary_check_falls_back_to_danubehis(self):
        dhis = {"data": {"statii": [{"statie": "Budapest",
                                      "debit_m3s": 750.0,
                                      "data": "2026-08-04"}]}}
        model = {"data": {"time": ["2026-08-04"],
                           "discharge": [1000.0]}}
        with mock.patch.object(C, "hydroinfo_danube", side_effect=OSError("down")), \
                mock.patch.object(C, "danubehis_danube", return_value=dhis), \
                mock.patch.object(C, "glofas_recent", return_value=model):
            out = anomalii.hungary_check()

        self.assertEqual(out["sursa_masurata"], "ICPDR DanubeHIS")
        self.assertEqual(out["raport"], 0.75)
        self.assertTrue(out["coerent"])

    def test_satellite_check_uses_only_quality_filtered_spatial_coverage(self):
        stations = []
        for i, (segment, pct) in enumerate([
                ("superior", 0), ("superior", 5),
                ("mijlociu", 10), ("mijlociu", 15),
                ("inferior", 20), ("inferior", 25)]):
            stations.append({"statie": f"s{i}", "km": 2400 - i * 400,
                             "segment": segment, "percentila_lunii": pct,
                             "eligibila_detector": True})
        stations.append({"statie": "bad", "km": 200,
                         "eligibila_detector": False,
                         "quality_flags": ["observatie_veche"]})
        payload = {"data": {"activ": True, "statii": stations,
                             "acoperire_km": [200, 2400],
                             "product_family": "hydroweb"}}
        with mock.patch.object(C, "hydroweb_danube", return_value=payload):
            out = anomalii.satellite_check()

        self.assertTrue(out["poate_sustine_context"])
        self.assertEqual(out["status"], "shadow_coerent")
        self.assertEqual(out["mediana_pct"], 12.5)
        self.assertEqual(out["statii_excluse"], 1)


class AiAnalysisAuditTests(unittest.TestCase):
    def test_digest_carries_errors_lineage_hungary_freshness_and_context(self):
        report = {
            "climatologie": [{"id": "bazias", "recent": [1, 2], "azi": {"value": 900}}],
            "bilant": {"z": 0.2},
            "masurat_vs_model": {"serie": [1], "z": 0.1},
            "mire_crosscheck": {"statii_comune": 2},
            "precipitatii": {"coerent": True},
            "satelit": {"status": "shadow_coerent"},
            "germania": {"coerent": True},
            "ungaria": {"coerent": False, "raport": 3.0},
            "serbia": {"coerent": True},
            "austria": {"statii": [1], "suspiciune_retentie": False},
            "erori": {"mire_crosscheck": "source down"},
        }
        stats = {"debit": [{"name": "Baziaș"}],
                 "precipitatii": [{"zona": "Alpi"}],
                 "metoda": {"debit": "model", "precipitatii": "reanaliză"}}
        budget = {"bazias": {"lipsa_km3": 2, "normal_km3": 20}}

        def fake_cached(key, ttl, fetch_fn):
            payload = {anomalii.REPORT_CACHE_KEY: report,
                       anomalii.STATS_CACHE_KEY: stats,
                       anomalii.BUDGET_CACHE_KEY: budget}[key]
            return {"data": payload, "stale": key == anomalii.REPORT_CACHE_KEY,
                    "cache_age_s": 123}

        fresh_context = {"data": {"activ": True}, "stale": False, "cache_age_s": 5}
        with mock.patch.object(C, "cached", side_effect=fake_cached), \
                mock.patch.object(analiza_ai, "_inhga", return_value={"debit_bazias_m3s": 900}), \
                mock.patch.object(C, "edo_status", return_value=fresh_context), \
                mock.patch.object(C, "opera_surface_status", return_value=fresh_context), \
                mock.patch.object(C, "copernicus_land_context", return_value=fresh_context), \
                mock.patch.object(C, "earthdata_satellite_catalog", return_value=fresh_context), \
                mock.patch.object(anomalii, "grdc_context", return_value={"activ": False}):
            digest = analiza_ai._digest()

        self.assertEqual(digest["ungaria_masurat_vs_model"]["raport"], 3.0)
        self.assertEqual(digest["erori_calcul"]["mire_crosscheck"], "source down")
        self.assertTrue(digest["metadate_actualizare"]["raport_anomalii"]["stale"])
        self.assertNotIn("recent", digest["climatologie_sectiuni"][0])
        self.assertEqual(digest["statistici_debit_sectiuni"][0]["name"], "Baziaș")
        self.assertIn("dependencies", digest["registru_provenienta"])
        self.assertEqual(digest["context_seceta_copernicus_edo"]["livrare"]["cache_age_s"], 5)
        self.assertIn("catalog_misiuni_satelitare_nasa", digest)

    def test_request_spec_keeps_compatible_mode_and_adds_opt_in_web_tool(self):
        with mock.patch.object(analiza_ai, "AI_WEB_SEARCH", False), \
                mock.patch.object(analiza_ai, "AI_MODEL", "compatible-mini"):
            chat = analiza_ai._request_spec({"x": 1})
        self.assertEqual(chat["mode"], "doar_json")
        self.assertTrue(chat["url"].endswith("/chat/completions"))
        self.assertNotIn("tools", chat["body"])

        with mock.patch.object(analiza_ai, "AI_WEB_SEARCH", True), \
                mock.patch.object(analiza_ai, "AI_WEB_MODEL", "web-model"):
            web = analiza_ai._request_spec({"x": 1})
        self.assertEqual(web["mode"], "web_cu_citari")
        self.assertTrue(web["url"].endswith("/responses"))
        self.assertEqual(web["body"]["tools"][0]["type"], "web_search")
        self.assertEqual(web["body"]["model"], "web-model")

    def test_responses_parser_marks_deduplicated_clickable_sources(self):
        text = "Nivel scăzut conform sursei. Context suplimentar."
        out = {"output": [
            {"type": "web_search_call", "action": {"queries": ["Danube official flow"]}},
            {"type": "message", "content": [{
                "type": "output_text", "text": text,
                "annotations": [
                    {"type": "url_citation", "url": "https://example.org/report",
                     "title": "Official report", "end_index": 27},
                    {"type": "url_citation", "url": "https://example.org/report",
                     "title": "Official report", "end_index": len(text)},
                    {"type": "url_citation", "url": "javascript:alert(1)",
                     "title": "unsafe", "end_index": len(text)},
                ],
            }]},
        ]}

        parsed, citations, queries = analiza_ai._parse_responses_output(out)

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["url"], "https://example.org/report")
        self.assertEqual(parsed.count("⟦WEB:1⟧"), 2)
        self.assertEqual(queries, ["Danube official flow"])

    def test_prompt_requires_integrity_checks_and_external_separation(self):
        prompt = analiza_ai.PROMPT_SISTEM
        self.assertIn("ANOMALII DE DATE ȘI CONTRADICȚII", prompt)
        self.assertIn("VERIFICARE EXTERNĂ", prompt)
        self.assertIn("registru_provenienta", prompt)
        self.assertIn("două căi de livrare", prompt)
        self.assertIn("catalog_only", prompt)
        self.assertIn("nu a fost efectuată", prompt)

    def test_response_heading_normalization_is_narrow_and_auditable(self):
        text = "## SITUAȚIA\nFapt.\n\n**CE AR SCHIMBĂ CONCLUZIA:**\nAlt fapt."
        normalized, missing = analiza_ai._normalize_response(text)

        self.assertIn("SITUAȚIA\nFapt.", normalized)
        self.assertIn("CE AR SCHIMBA CONCLUZIA\nAlt fapt.", normalized)
        self.assertIn("CAUZE PROBABILE", missing)


class CalendarTests(unittest.TestCase):
    def test_calendar_window_wraps_at_new_year(self):
        self.assertEqual(anomalii._doy_diff("01-02", "12-31"), 2)
        self.assertEqual(anomalii._doy_diff("12-31", "01-02"), -2)


if __name__ == "__main__":
    unittest.main()
