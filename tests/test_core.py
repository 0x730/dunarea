import inspect
import html
import json
import os
import sqlite3
import struct
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import analiza_ai
import anomalii
import connectors as C
import romania
import server


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

    def test_inhga_ui_exposes_bulletin_date_freshness_and_stale_state(self):
        app = Path(__file__).parents[1].joinpath("static/app.js").read_text()

        self.assertIn("b.data_buletin", app)
        self.assertIn("b.cache_age_s", app)
        self.assertIn("cache vechi · sursa nu a răspuns", app)
        self.assertIn("setInterval(refreshData, 5 * 60 * 1000)", app)
        self.assertIn("Valoarea curentă folosită: INHGA", app)
        self.assertIn("Nu este o a doua măsurătoare", app)
        self.assertIn("reper prognozat", app)
        self.assertNotIn("prognoză 7 zile", app)

    def test_stateful_copy_is_neutral_and_ai_is_not_rendered(self):
        root = Path(__file__).parents[1]
        page = root.joinpath("static/index.html").read_text()
        app = root.joinpath("static/app.js").read_text()

        self.assertNotIn("ai-card", page)
        self.assertNotIn("renderAnalizaAI", app)
        self.assertNotIn("/api/analiza-ai", app)
        self.assertIn("Starea bazinului", page)
        self.assertIn("Cele mai recente cote și debite disponibile", page)
        self.assertNotIn("Seceta din bazin", page)
        self.assertNotIn("Punctele critice azi", app)
        self.assertNotIn("Modelul supraestimează sistematic", app)
        self.assertNotIn("record al ultimilor", app)
        self.assertIn("Number(m.raport_mediu) < 0.97", app)
        self.assertIn("fără interpretare AI", app)

    def test_romania_page_is_dynamic_and_has_no_fixed_current_verdict(self):
        root = Path(__file__).parents[1]
        page = root.joinpath("static/index.html").read_text()
        app = root.joinpath("static/app.js").read_text()

        self.assertIn('href="/romania"', page)
        self.assertIn('href="/date-lipsa"', page)
        self.assertIn('data-view="romania"', page)
        self.assertIn('data-view="lipsa"', page)
        self.assertIn("/api/romania", app)
        self.assertIn("/api/date-lipsa", app)
        self.assertIn("consum DAMAS", app)
        self.assertIn("capacitate de echilibrare contractată", app)
        self.assertIn("PZU · ziua următoare", app)
        self.assertIn("rare_not_unprecedented", app)
        self.assertIn("nicio concluzie veche nu este păstrată", app)
        self.assertIn('id="ro-operational-history"', page)
        self.assertIn('id="ro-historical-thresholds"', page)
        self.assertIn('id="ro-parameter-coverage"', page)
        self.assertIn('id="ro-tributaries"', page)
        self.assertIn('id="ro-water-resources"', page)
        self.assertIn('id="missing-register"', page)
        self.assertIn("gauge?.temp_apa_c", app)
        self.assertIn("gauge?.tendinte_cm", app)
        self.assertIn("mediană ian.–azi", app)
        self.assertIn("raritate GloFAS", app)
        self.assertIn("climate.percentile", app)
        self.assertIn("point.ytd_mm", app)
        self.assertIn("secțiuni măsurate", page)
        self.assertNotIn("1.450 m³/s", page.split('data-view="romania"', 1)[1])
        self.assertNotIn("-214 cm", page.split('data-view="romania"', 1)[1])
        self.assertNotIn("27.5°C", page.split('data-view="romania"', 1)[1])
        self.assertNotIn("-226 cm", page.split('data-view="romania"', 1)[1])
        self.assertNotIn("U1 oprită", page.split('data-view="romania"', 1)[1])

    def test_deep_links_chart_accessibility_and_effective_reference_copy(self):
        root = Path(__file__).parents[1]
        page = root.joinpath("static/index.html").read_text()
        app = root.joinpath("static/app.js").read_text()

        self.assertIn("preserveHash", app)
        self.assertIn("initialTarget.scrollIntoView", app)
        self.assertIn("aria: { enabled: true", app)
        self.assertIn('role="img" aria-label=', page)
        self.assertIn('id="stats-glofas-reference"', page)
        self.assertIn('id="stats-era5-reference"', page)
        self.assertNotIn("referință 1991–anul trecut", page)


class PublicApiSecurityTests(unittest.TestCase):
    def test_public_payload_removes_sensitive_query_parameters_recursively(self):
        value = {"items": [{"source_url": (
            "https://example.org/data?token=secret&safe=yes&X-Amz-Signature=also-secret")}]}

        out = server._public_payload(value)

        self.assertEqual(out["items"][0]["source_url"], "https://example.org/data?safe=yes")
        self.assertNotIn("secret", json.dumps(out))

    def test_credential_scrub_matches_words_not_substrings(self):
        """Un parametru legitim nu are voie să fie tăiat dintr-un URL-sursă:
        linkul trebuie să reproducă pagina originală, altfel proveniența, care
        e chiar produsul aplicației, devine neverificabilă."""
        for key in ("api_key", "securityToken", "apikey", "X-Amz-Signature",
                    "access-token", "sig"):
            self.assertTrue(server._is_sensitive_param(key, "x"), key)
        for key in ("keywords", "layers", "time", "bbox", "designation",
                    "station", "uuid"):
            self.assertFalse(server._is_sensitive_param(key, "x" * 40), key)

    def test_health_endpoint_touches_no_external_source(self):
        """Un endpoint de sănătate care poate provoca muncă e o pârghie de
        amplificare; acesta citește numai starea locală."""
        def explode(*a, **k):
            raise AssertionError("health nu are voie să atingă o sursă externă")

        with mock.patch.object(C, "http_get", side_effect=explode), \
                mock.patch.object(C, "http_json", side_effect=explode), \
                mock.patch.object(C, "cached", side_effect=explode):
            out = server.api_health({})

        self.assertEqual(out["status"], "ok")
        self.assertIn("uptime_s", out)
        self.assertIn("warmup_done", out)

    def test_dynamic_external_url_requires_expected_https_host(self):
        self.assertEqual(
            C._validated_https_url("https://hydroweb.next.theia-land.fr/a", {
                "hydroweb.next.theia-land.fr"}),
            "https://hydroweb.next.theia-land.fr/a")
        for url in ("http://hydroweb.next.theia-land.fr/a",
                    "https://127.0.0.1/a", "https://example.org/a"):
            with self.assertRaises(RuntimeError):
                C._validated_https_url(url, {"hydroweb.next.theia-land.fr"})


class MissingDataRegistryTests(unittest.TestCase):
    def test_registry_statuses_follow_available_evidence(self):
        report = {
            "water_resources": {
                "available": True, "current": True, "published": "2026-08-05",
                "reservoirs": {"count": 40, "fill_pct": 61.5,
                               "volume_billion_m3": 2.4},
                "restrictions": {"drinking_water": False},
            },
            "romanian_tributaries": {
                "selected_systems": 9,
                # Toate sistemele urmărite au secțiune măsurată și niciunul nu
                # lipsește — singurul caz în care lacuna e într-adevăr închisă.
                "observed_sections": {"sections_available": 9, "sections": [],
                                      "measured_systems_target": 9,
                                      "missing_systems": []},
            },
            "energy": {
                "hydro_mw": 1200,
                "history": {
                    "available": True, "enough_for_comparison": True,
                    "days": 14, "minimum_days": 14,
                },
                "market": {
                    "available_components": 4,
                    "consumption": {"available": True, "delivery_date": "2026-08-05"},
                    "reserve_procurement": {"available": True, "delivery_date": "2026-08-05"},
                    "balancing": {"available": True, "delivery_date": "2026-08-05"},
                    "day_ahead": {"available": True, "delivery_date": "2026-08-06"},
                },
            },
            "official_danube_bulletin": {
                "date": "2026-08-05", "url": "https://www.hidro.ro/current/",
                "cernavoda_bala_caveat": True,
            },
            "cernavoda": {"parameter_transparency": {
                "decision_reproducible": True, "decision_parameters": [],
                "public_signals": [],
            }},
        }
        with mock.patch.object(server, "api_romania", return_value=report), \
                mock.patch.object(C, "earthdata_satellite_catalog", return_value={
                    "data": {"sources": {}, "download_configurat": False}}), \
                mock.patch.object(C, "copernicus_land_context", return_value={
                    "data": {"straturi": {"soil": {"activ": False}}}}), \
                mock.patch.object(C, "grdc_series", return_value={"activ": True}), \
                mock.patch.object(C, "entsoe_irongates", return_value={"activ": False}):
            result = server.api_missing_data({})

        statuses = {entry["id"]: entry["status"] for entry in result["entries"]}
        self.assertEqual(statuses["anar_reservoirs"], "available")
        self.assertEqual(statuses["cernavoda_decision"], "available")
        self.assertEqual(statuses["tributary_gauges"], "available")
        self.assertEqual(statuses["sen_history"], "partial")
        self.assertEqual(statuses["cernavoda_bala"], "partial")
        self.assertEqual(statuses["irongates_operations"], "partial")
        self.assertEqual(statuses["soil_moisture"], "missing")
        self.assertEqual(statuses["optional_backfill"], "partial")
        self.assertEqual(result["summary"], {
            "available": 3, "partial": 4, "missing": 1,
        })

    def test_tributary_gap_stays_open_while_systems_have_no_public_section(self):
        """Oricâte secțiuni ar livra DanubeHIS, sistemele fără secțiune publică
        țin lacuna deschisă — altfel registrul se contrazice pe el însuși."""
        report = {
            "romanian_tributaries": {
                "observed_sections": {
                    "sections_available": 9, "sections": [],
                    "measured_systems_target": 9,
                    "missing_systems": ["nera", "cerna", "arges", "ialomita"],
                },
            },
        }
        with mock.patch.object(server, "api_romania", return_value=report), \
                mock.patch.object(C, "earthdata_satellite_catalog", return_value={
                    "data": {"sources": {}, "download_configurat": False}}), \
                mock.patch.object(C, "copernicus_land_context", return_value={
                    "data": {"straturi": {"soil": {"activ": False}}}}), \
                mock.patch.object(C, "grdc_series", return_value={"activ": False}), \
                mock.patch.object(C, "entsoe_irongates", return_value={"activ": False}):
            result = server.api_missing_data({})

        entry = next(e for e in result["entries"] if e["id"] == "tributary_gauges")
        self.assertEqual(entry["status"], "partial")
        self.assertEqual(entry["what_we_have"]["missing_systems"],
                         ["nera", "cerna", "arges", "ialomita"])


class RomaniaProportionalityTests(unittest.TestCase):
    @staticmethod
    def archive():
        pairs = [
            ("2003-08-01", 3302.7),
            ("2003-08-04", 3442.5), ("2003-08-25", 2354.1),
            ("2003-09-05", 2274.1), ("2003-09-30", 2740.6),
            ("2011-08-04", 4500.0), ("2011-08-31", 4264.8),
            ("2011-09-15", 3418.8), ("2011-09-24", 3075.1),
            ("2011-09-30", 3980.1),
            ("2015-07-22", 4225.7),
            ("2015-08-04", 3588.2), ("2015-08-21", 3068.0),
            ("2015-09-30", 3836.1),
            ("2022-08-04", 2617.2), ("2022-08-05", 2611.2),
            ("2022-08-26", 3434.6), ("2022-08-31", 3853.8),
            ("2025-07-13", 2605.2), ("2025-08-04", 3300.0),
            ("2026-07-20", 2800.0), ("2026-08-04", 2665.9),
        ]
        return {"time": [p[0] for p in pairs],
                "discharge": [p[1] for p in pairs]}

    @staticmethod
    def stats(cern_pct=0.5, ro_last90=50, ro_ytd=20):
        precip = []
        for pid, zone in (("oltenia", "Oltenia"), ("muntenia", "Muntenia"),
                          ("moldova_sud", "Sud-est"), ("delta", "Delta")):
            precip.append({"id": pid, "zona": zone,
                           "pana_la": "2026-08-02",
                           "ultimele90": {"pct": ro_last90, "cumul_mm": 72.5},
                           "ian_azi": {"abatere_pct": ro_ytd, "cumul_mm": 410.0,
                                       "mediana_mm": 380.0}})
        return {
            "generat": "2026-08-04",
            "debit": [
                {"name": "Baziaș (intrarea în RO)", "azi_m3s": 1976,
                 "percentila": 0, "zile_sub_p10": 43},
                {"name": "Cernavodă (braț principal)", "azi_m3s": 2665.9,
                 "percentila": cern_pct, "zile_sub_p10": 38},
            ],
            "precipitatii": precip,
        }

    @staticmethod
    def snn(known=True):
        if not known:
            return {"data": {"needs_review": True, "status_available": False,
                             "latest_report": {"title": "raport operațional nou",
                                               "url": "https://nuclearelectrica.ro/new.pdf"}},
                    "stale": False}
        return {"data": {
            "date": "2026-08-04", "status_available": True,
            "status_fresh": True, "needs_review": False,
            "water_related": True, "u1": "oprită controlat",
            "u2": "capacitate nominală",
            "latest_report": {"title": "status U2", "url": "https://nuclearelectrica.ro/status.pdf"},
        }, "stale": False}

    @staticmethod
    def inputs():
        afdj = {"statii": [{"statie": "Cernavoda", "cota_cm": -214,
                            "variatie_cm": -1, "actualizat": "2026-08-04T03:00:00+03:00"}]}
        inhga = {"debit_bazias_m3s": 1450, "media_multianuala_m3s": 3900,
                 "data_buletin": "2026-08-04"}
        sen = {"nuclear_mw": 680, "hidro_mw": 1370, "sold_mw": 1900,
               "consum_mw": 6400, "actualizat": "26/8/4 23:00:00"}
        return afdj, inhga, sen

    @staticmethod
    def tributaries(month="2026-08", maximum=50):
        def basin(identifier, label, relation):
            return {
                "id": identifier, "label": label, "relation": relation,
                "band_pct": {"min": 30, "max": maximum, "operator": "range"},
                "basis": "general_band", "official_clause": None,
            }

        upstream = [basin(identifier, label, "upstream_cernavoda")
                    for identifier, label in (("nera", "Nera"), ("cerna", "Cerna"),
                                              ("jiu", "Jiu"), ("olt", "Olt"),
                                              ("vedea", "Vedea"), ("arges", "Argeș"))]
        downstream = [basin(identifier, label, "downstream_cernavoda")
                      for identifier, label in (("ialomita", "Ialomița"),
                                                ("siret", "Siret"), ("prut", "Prut"))]
        return {
            "available": True,
            "url": "https://www.hidro.ro/bulletin/monthly/",
            "title": "Prognoza hidrologică lunară",
            "published": "2026-07-31",
            "scope": "afluenți selectați",
            "limit": "prognoză, nu măsurătoare",
            "months": [{
                "month": month, "label": "august 2026",
                "base_band_pct": {"min": 30, "max": maximum,
                                  "operator": "range"},
                "upstream_cernavoda": upstream,
                "downstream_cernavoda": downstream,
                "official_text": "Text oficial dinamic.",
            }],
        }

    @staticmethod
    def tributary_observations(latest="2026-08-03"):
        return {
            "available": True,
            "as_of": "2026-08-05",
            "provider": "NIHWM/INHGA via ICPDR DanubeHIS",
            "source_url": "https://www.danubehis.org/time-series/stations/Q?country=RO",
            "kind": "debit instantaneu brut la secțiuni hidrometrice",
            "scope": "secțiuni parțiale",
            "limit": "nu sunt aporturi totale la Dunăre",
            "missing_systems": ["nera", "cerna", "arges", "ialomita"],
            "sections": [{
                "river_id": "vedea", "river": "Vedea", "station": "Alexandria",
                "relation": "upstream_cernavoda", "coverage": "secțiune parțială",
                "available": True,
                "latest": {"date": latest, "value_m3s": 3.56},
                "lag_days": 2,
                "ytd": {"median_m3s": 5.75, "min_m3s": 0.36,
                        "max_m3s": 78.0, "days": 208,
                        "expected_days": 215, "coverage_pct": 96.7},
                "current_month": {"month": "2026-08", "median_m3s": 3.7,
                                  "min_m3s": 3.56, "max_m3s": 3.84,
                                  "days": 3, "expected_days": 3,
                                  "coverage_pct": 100.0},
                "previous_year_same_window": None,
                "url": "https://www.danubehis.org/results/RO42471_HYDRO",
            }],
        }

    @staticmethod
    def tributary_model_climatology(model_date="2026-08-04", percentile=4.2,
                                    reference_years=29):
        return {
            "available": True,
            "as_of": model_date,
            "source": "GloFAS test",
            "source_url": "https://open-meteo.com/en/docs/flood-api",
            "reference": "1997–2025, fereastră calendaristică ±7 zile",
            "limit": "percentilă în model, nu a măsurătorii",
            "sections": [{
                "river_id": "vedea", "river": "Vedea", "station": "Alexandria",
                "model_date": model_date, "model_m3s": 4.2,
                "percentile": percentile, "climate_median_m3s": 12.0,
                "deviation_pct": -65.0, "reference_samples": 435,
                "reference_years": reference_years,
                "reference_period": {"start": 1997, "end": 2025},
                "calendar_window_days": 7,
            }],
        }

    def test_history_calls_current_rare_but_not_unprecedented(self):
        out = romania.historical_cernavoda(self.archive(), "2026-08-04")

        self.assertEqual(out["rank_low_to_high"], 2)
        self.assertEqual(out["years_compared"], 6)
        self.assertEqual(out["lower_years"], [{"year": 2022, "value_m3s": 2617.2}])
        row_2003 = next(r for r in out["rows"] if r["year"] == 2003)
        self.assertEqual(row_2003["august_min"]["value_m3s"], 2354.1)
        self.assertFalse(row_2003["august_partial"])

    def test_current_low_water_and_u1_impact_do_not_imply_national_crisis(self):
        afdj, inhga, sen = self.inputs()
        out = romania.build_report(self.stats(), self.archive(), afdj, inhga,
                                   sen, self.snn(), "2026-08-04")
        claims = {c["key"]: c for c in out["claims"]}

        self.assertEqual(claims["physical"]["status"], "confirmed")
        self.assertEqual(claims["rarity"]["status"], "rare_not_unprecedented")
        self.assertEqual(claims["romania_scope"]["status"], "not_supported")
        self.assertEqual(claims["cernavoda_impact"]["status"], "confirmed")
        self.assertEqual(claims["national_energy_crisis"]["status"], "insufficient")
        assessment = claims["national_energy_crisis"]["evidence"]["national_crisis_assessment"]
        self.assertFalse(assessment["ready"])
        self.assertFalse(assessment["coverage"]["operational_reserve_margin_available"])
        self.assertIn("confirmarea sau excluderea",
                      claims["national_energy_crisis"]["conclusion"])

    def test_new_water_and_energy_inputs_update_missing_sections(self):
        afdj, inhga, sen = self.inputs()
        water = {
            "available": True, "current": True, "published": "2026-08-04",
            "reservoirs": {"count": 40, "fill_pct": 61.5,
                           "volume_billion_m3": 2.4,
                           "sufficient_for_centralized_supply": True},
            "restrictions": {"drinking_water": False,
                             "official_statements": []},
        }
        history = {"available": True, "enough_for_comparison": True,
                   "days": 14, "minimum_days": 14, "metrics": {}}
        market = {
            "available_components": 4, "component_count": 4,
            "consumption": {"available": True, "delivery_date": "2026-08-04"},
            "reserve_procurement": {"available": True, "delivery_date": "2026-08-04"},
            "balancing": {"available": True, "delivery_date": "2026-08-04"},
            "day_ahead": {"available": True, "delivery_date": "2026-08-05"},
        }

        out = romania.build_report(
            self.stats(), self.archive(), afdj, inhga, sen, self.snn(),
            "2026-08-04", water_resources=water, sen_history=history,
            energy_market=market)

        missing = " ".join(out["missing_for_national_verdict"])
        rain_claim = next(c for c in out["claims"] if c["key"] == "romania_scope")
        self.assertNotIn("gradul curent de umplere", missing)
        self.assertNotIn("arhiva locală acumulează", missing)
        self.assertNotIn("rezervele și prețurile rămân neintegrate", missing)
        self.assertIn("marja operațională de rezervă rămasă", missing)
        self.assertIn("nicio restricție", rain_claim["conclusion"])
        self.assertTrue(out["energy"]["history"]["enough_for_comparison"])
        self.assertEqual(out["energy"]["market"]["available_components"], 4)
        self.assertIn("nu poate fi verificată", out["headline"])
        self.assertIn("snn_pdf_sha256", out["energy"])

        operational = out["cernavoda"]["operational_history"]
        self.assertEqual([row["year"] for row in operational],
                         [2003, 2011, 2015, 2022, 2026])
        self.assertEqual(operational[0]["classification"], "water_shutdown")
        self.assertEqual(operational[1]["classification"], "operating")
        self.assertEqual(operational[3]["classification"], "other_cause")
        self.assertEqual(operational[1]["reference_date"], "2011-09-15")
        self.assertIn("contextul hidrologic este GloFAS",
                      operational[3]["source_scope"])
        self.assertEqual(operational[0]["model_context"]["minimum"],
                         {"date": "2003-09-05", "value_m3s": 2274.1})
        self.assertEqual(operational[1]["model_context"]["start_value_m3s"], 3418.8)
        self.assertEqual(operational[2]["model_context"]["minimum"],
                         {"date": "2015-08-21", "value_m3s": 3068.0})
        self.assertEqual(operational[3]["model_context"]["minimum"],
                         {"date": "2022-08-26", "value_m3s": 3434.6})
        self.assertEqual(operational[3]["model_context"]["end_value_m3s"], 3853.8)
        self.assertEqual(operational[0]["gauge_context"]["facts"][0], {
            "label": "minim în fereastră", "value": -237, "unit": "cm",
            "date": "2003-09-10",
        })
        self.assertEqual(operational[1]["gauge_context"]["facts"][0]["value"], -138)
        self.assertEqual(operational[1]["gauge_context"]["facts"][0]["dates"],
                         ["2011-09-26", "2011-09-27"])
        self.assertEqual(operational[2]["gauge_context"]["facts"][0]["value"], -119)
        self.assertEqual(operational[3]["gauge_context"]["facts"][1], {
            "label": "zile sub LNWL în august", "value": 31, "unit": "zile",
        })
        self.assertIn("Comisia Dunării", operational[0]["gauge_context"]["source"]["label"])
        self.assertIn("nu se convertește", operational[0]["gauge_context"]["limit"])
        self.assertEqual(operational[-1]["classification"], "current_water_shutdown")
        self.assertIn("Baziaș 1450", operational[-1]["hydrology"])
        self.assertEqual(operational[-1]["model_context"]["start_value_m3s"], 2665.9)
        self.assertEqual(operational[-1]["model_context"]["percentile"], 0.5)
        self.assertEqual(operational[-1]["gauge_context"]["facts"][0]["value"], -214)
        self.assertEqual(operational[-1]["gauge_context"]["facts"][0]["date"],
                         "2026-08-04T03:00:00+03:00")

        transparency = out["cernavoda"]["parameter_transparency"]
        self.assertFalse(transparency["decision_reproducible"])
        self.assertEqual(transparency["historical_2011"]["shutdown_levels_mdmb"][0]["value"], 2.5)
        self.assertIn("reper istoric", transparency["historical_2011"]["validity"])
        self.assertTrue(all(parameter["status"] == "missing"
                            for parameter in transparency["decision_parameters"]))

    def test_copy_reacts_to_normal_flow_national_dryness_and_unknown_snn_report(self):
        afdj, inhga, sen = self.inputs()
        inhga["debit_bazias_m3s"] = 3800
        out = romania.build_report(
            self.stats(cern_pct=50, ro_last90=4, ro_ytd=-30),
            self.archive(), afdj, inhga, sen, self.snn(known=False), "2026-08-04")
        claims = {c["key"]: c for c in out["claims"]}

        self.assertEqual(claims["physical"]["status"], "not_supported")
        self.assertEqual(claims["romania_scope"]["status"], "supported_component")
        self.assertEqual(claims["cernavoda_impact"]["status"], "insufficient")
        self.assertNotIn("U1 oprită", claims["cernavoda_impact"]["conclusion"])
        self.assertEqual(out["cernavoda"]["operational_history"][-1]["classification"],
                         "unknown")
        bazias = next(signal for signal in
                      out["cernavoda"]["parameter_transparency"]["public_signals"]
                      if signal["key"] == "bazias")
        self.assertEqual(bazias["value"], 3800)
        self.assertEqual(bazias["context"], "97.4% din media lunii")

    def test_missing_event_window_is_explicit_and_not_filled_from_another_date(self):
        context = romania._model_window_context(
            {"time": ["2011-09-14"], "discharge": [3100]},
            {"start": "2011-09-15", "end": "2011-09-30",
             "label": "test", "basis": "test"})

        self.assertFalse(context["available"])
        self.assertEqual(context["days_available"], 0)
        self.assertIsNone(context.get("start_value_m3s"))

    def test_current_operational_row_reacts_to_reconnection(self):
        afdj, inhga, sen = self.inputs()
        sen["nuclear_mw"] = 1350
        snn = self.snn()
        snn["data"].update({
            "water_related": False,
            "u1": "conectată la SEN",
            "u2": "capacitate nominală",
        })

        out = romania.build_report(self.stats(), self.archive(), afdj, inhga,
                                   sen, snn, "2026-08-04")
        current = out["cernavoda"]["operational_history"][-1]

        self.assertEqual(current["classification"], "current_official")
        self.assertIn("U1: conectată la SEN", current["plant_action"])
        self.assertNotIn("oprirea U1 asociată apei", current["interpretation"])

    def test_current_measured_gauge_context_reacts_to_new_afdj_value(self):
        afdj, inhga, sen = self.inputs()
        afdj["statii"][0].update({
            "cota_cm": 87,
            "actualizat": "2026-08-05T03:00:00+03:00",
        })

        out = romania.build_report(self.stats(), self.archive(), afdj, inhga,
                                   sen, self.snn(), "2026-08-05")
        gauge = out["cernavoda"]["operational_history"][-1]["gauge_context"]

        self.assertTrue(gauge["available"])
        self.assertEqual(gauge["facts"], [{
            "label": "ultima citire", "value": 87, "unit": "cm",
            "date": "2026-08-05T03:00:00+03:00",
        }])
        self.assertIn("se schimbă odată cu sursa", gauge["source_scope"])
        self.assertNotIn("-214", str(gauge))

    def test_afdj_temperature_and_forecast_react_to_source_values(self):
        afdj, inhga, sen = self.inputs()
        afdj["statii"][0].update({
            "cota_cm": -180,
            "temp_apa_c": 24.3,
            "tendinte_cm": {"24h": -181, "48h": -184, "72h": -188,
                              "96h": -190, "120h": -193},
            "actualizat": "2026-08-05T03:00:00+03:00",
        })

        out = romania.build_report(self.stats(), self.archive(), afdj, inhga,
                                   sen, self.snn(), "2026-08-05")
        gauge = out["cernavoda"]["operational_history"][-1]["gauge_context"]
        signals = {item["key"]: item for item in
                   out["cernavoda"]["parameter_transparency"]["public_signals"]}

        self.assertEqual(gauge["facts"][1]["value"], 24.3)
        self.assertEqual(gauge["forecast"], [
            {"hours": 24, "value_cm": -181},
            {"hours": 48, "value_cm": -184},
            {"hours": 72, "value_cm": -188},
            {"hours": 96, "value_cm": -190},
            {"hours": 120, "value_cm": -193},
        ])
        self.assertEqual(signals["cernavoda_water_temperature"]["value"], 24.3)
        self.assertEqual(signals["cernavoda_gauge_forecast_120h"]["value"], -193)
        self.assertNotIn("27.5", str(gauge))
        self.assertNotIn("-226", str(gauge))

    def test_current_month_tributary_forecast_changes_national_context(self):
        afdj, inhga, sen = self.inputs()
        out = romania.build_report(
            self.stats(ro_last90=50, ro_ytd=20), self.archive(), afdj, inhga,
            sen, self.snn(), "2026-08-05", tributaries=self.tributaries())
        claim = next(item for item in out["claims"] if item["key"] == "romania_scope")

        self.assertEqual(claim["status"], "mixed")
        self.assertEqual(out["romanian_tributaries"]["selected_systems"], 9)
        self.assertEqual(len(out["romanian_tributaries"]["systems_at_most_50pct"]), 9)
        self.assertIn("afluenților", claim["conclusion"])

    def test_realized_precipitation_and_measured_section_stats_are_preserved(self):
        afdj, inhga, sen = self.inputs()
        out = romania.build_report(
            self.stats(), self.archive(), afdj, inhga, sen, self.snn(),
            "2026-08-05", tributaries=self.tributaries(),
            tributary_observations=self.tributary_observations(),
            tributary_model_climatology=self.tributary_model_climatology())
        claim = next(item for item in out["claims"] if item["key"] == "romania_scope")
        point = claim["evidence"]["points"][0]
        observed = out["romanian_tributaries"]["observed_sections"]

        self.assertEqual(point["data_through"], "2026-08-02")
        self.assertEqual(point["ytd_mm"], 410.0)
        self.assertEqual(point["ytd_median_mm"], 380.0)
        self.assertEqual(point["last90_mm"], 72.5)
        self.assertTrue(observed["available"])
        self.assertEqual(observed["latest_date"], "2026-08-03")
        self.assertEqual(observed["sections"][0]["latest"]["value_m3s"], 3.56)
        climate = out["romanian_tributaries"]["model_climatology"]
        self.assertTrue(climate["available"])
        self.assertEqual(climate["sections_below_p10"], ["vedea"])
        self.assertEqual(climate["sections"][0]["percentile"], 4.2)
        self.assertEqual(out["data_as_of"]["danubehis_ro_tributaries"],
                         "2026-08-03")
        self.assertEqual(out["data_as_of"]["glofas_ro_tributaries"],
                         "2026-08-04")

    def test_short_or_future_tributary_model_reference_is_rejected(self):
        afdj, inhga, sen = self.inputs()
        out = romania.build_report(
            self.stats(), self.archive(), afdj, inhga, sen, self.snn(),
            "2026-08-05", tributaries=self.tributaries(),
            tributary_model_climatology=self.tributary_model_climatology(
                model_date="2026-08-06", reference_years=19))

        climate = out["romanian_tributaries"]["model_climatology"]
        self.assertFalse(climate["available"])
        self.assertEqual(climate["sections"], [])
        self.assertIn("referință acceptabile", climate["reason"])

    def test_future_measured_tributary_section_is_not_relabelled_current(self):
        afdj, inhga, sen = self.inputs()
        out = romania.build_report(
            self.stats(), self.archive(), afdj, inhga, sen, self.snn(),
            "2026-08-05", tributaries=self.tributaries(),
            tributary_observations=self.tributary_observations("2026-08-06"))

        observed = out["romanian_tributaries"]["observed_sections"]
        self.assertFalse(observed["available"])
        self.assertEqual(observed["sections"], [])
        self.assertIn("dată acceptabilă", observed["reason"])

    def test_old_tributary_month_is_not_relabelled_as_current(self):
        afdj, inhga, sen = self.inputs()
        out = romania.build_report(
            self.stats(), self.archive(), afdj, inhga, sen, self.snn(),
            "2026-08-05", tributaries=self.tributaries(month="2026-07"))
        context = out["romanian_tributaries"]

        self.assertFalse(context["available"])
        self.assertIsNone(context["forecast_month"])
        self.assertIn("2026-08", context["reason"])
        self.assertEqual(context["horizon"], ["2026-07"])

    def test_day_rollover_keeps_previous_model_date_and_does_not_relabel_value(self):
        afdj, inhga, sen = self.inputs()
        stats = self.stats()
        stats["generat"] = "2026-08-05"
        cern = next(row for row in stats["debit"] if "Cernavod" in row["name"])
        cern["data"] = "2026-08-04"

        out = romania.build_report(stats, self.archive(), afdj, inhga,
                                   sen, self.snn(), "2026-08-05")
        current = out["cernavoda"]["operational_history"][-1]

        self.assertEqual(out["generated"], "2026-08-05")
        self.assertEqual(out["data_as_of"]["glofas"], "2026-08-04")
        self.assertEqual(out["data_as_of"]["glofas_lag_days"], 1)
        self.assertEqual(out["cernavoda"]["history"]["as_of"], "2026-08-04")
        self.assertEqual(out["cernavoda"]["history"]["rank_low_to_high"], 2)
        self.assertEqual(current["model_context"]["start"], "2026-08-04")
        self.assertEqual(current["model_context"]["minimum"]["date"], "2026-08-04")
        self.assertNotIn("2026-08-05", str(current["model_context"]))

    def test_model_rollover_uses_new_date_only_after_dated_evidence_arrives(self):
        afdj, inhga, sen = self.inputs()
        stats = self.stats()
        stats["generat"] = "2026-08-05"
        cern = next(row for row in stats["debit"] if "Cernavod" in row["name"])
        cern.update({"data": "2026-08-05", "azi_m3s": 2700.0})
        archive = self.archive()
        archive["time"].append("2026-08-05")
        archive["discharge"].append(2700.0)

        out = romania.build_report(stats, archive, afdj, inhga, sen, self.snn(),
                                   "2026-08-05")
        current = out["cernavoda"]["operational_history"][-1]

        self.assertEqual(out["data_as_of"]["glofas"], "2026-08-05")
        self.assertEqual(out["data_as_of"]["glofas_lag_days"], 0)
        self.assertEqual(current["model_context"]["minimum"],
                         {"date": "2026-08-05", "value_m3s": 2700.0})

    def test_undated_model_value_is_not_assigned_the_generation_date(self):
        afdj, inhga, sen = self.inputs()
        stats = self.stats()
        stats["generat"] = "2026-08-05"

        out = romania.build_report(stats, {"time": [], "discharge": []},
                                   afdj, inhga, sen, self.snn())
        claims = {claim["key"]: claim for claim in out["claims"]}
        current = out["cernavoda"]["operational_history"][-1]

        self.assertIsNone(out["data_as_of"]["glofas"])
        self.assertEqual(claims["physical"]["status"], "insufficient")
        self.assertFalse(current["model_context"]["available"])
        self.assertIsNone(current["model_context"]["start"])

    def test_new_operational_snn_pdf_is_not_silently_treated_as_audited(self):
        page = (
            '<a href="https://nuclearelectrica.ro/ir/wp-content/uploads/sites/3/2026/08/new.pdf">'
            'Raport privind reconectarea Unitatii 1 CNE Cernavoda</a>'
            '<a href="https://nuclearelectrica.ro/ir/wp-content/uploads/sites/3/2026/08/RC-Status-Update-U2-bvb.pdf">'
            'Unitatea 2 CNE Cernavodă funcționează la capacitate nominală</a>')

        parsed = C._parse_snn_cernavoda_reports(page)

        self.assertEqual(parsed[0]["filename"], "new.pdf")
        self.assertFalse(parsed[0]["audited"])
        self.assertTrue(parsed[1]["audited"])

    def test_changed_snn_pdf_invalidates_known_url_summary(self):
        url = ("https://nuclearelectrica.ro/ir/wp-content/uploads/sites/3/2026/08/"
               "RC-Status-Update-U2-bvb.pdf")
        page = f'<a href="{url}">Unitatea 2 CNE Cernavodă funcționează la capacitate nominală</a>'

        def no_cache(key, ttl, fetch_fn, stale_ok=True):
            return {"data": fetch_fn(), "stale": False, "cache_age_s": 0}

        with mock.patch.object(C, "cached", side_effect=no_cache), \
                mock.patch.object(C, "http_get", side_effect=[page, b"changed-pdf"]):
            out = C.snn_cernavoda_status()

        self.assertTrue(out["data"]["needs_review"])
        self.assertFalse(out["data"]["status_available"])
        self.assertIn("s-a schimbat", out["data"]["reason"])


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

    def test_sen_history_comparison_self_enables_only_after_minimum_days(self):
        start = date(2026, 7, 20)
        for offset in range(13):
            day = start + timedelta(days=offset)
            C.cache_put(f"hist:sen:{day.isoformat()}", {
                "consum_mw": 6000 + offset, "sold_mw": 1000 + offset,
                "hidro_mw": 1500, "nuclear_mw": 700,
            }, 10 ** 9)

        accumulating = C.sen_history_context(min_days=14)
        self.assertFalse(accumulating["enough_for_comparison"])
        self.assertEqual(accumulating["days"], 13)

        C.cache_put("hist:sen:2026-08-02", {
            "consum_mw": 6100, "sold_mw": 1200,
            "hidro_mw": 1400, "nuclear_mw": 700,
        }, 10 ** 9)
        ready = C.sen_history_context(min_days=14)
        self.assertTrue(ready["enough_for_comparison"])
        self.assertEqual(ready["metrics"]["consum_mw"]["samples"], 14)

    def test_sensitive_hydroweb_cache_is_self_healed_without_losing_history(self):
        signed = "https://hydroweb.next.theia-land.fr/file?token=secret"
        C.cache_put("hist:hydroweb:2026-08-04", {
            "statii": [{"source_url": signed, "nivel_m": 1.2}]}, 10 ** 9)
        C.cache_put("hydroweb:v3", {"statii": [{"source_url": signed}]}, 3600)

        changed = C.scrub_sensitive_cache()

        self.assertEqual(changed, 2)
        history = C.cache_get("hist:hydroweb:2026-08-04", max_age=10 ** 9)["data"]
        self.assertEqual(history["statii"][0]["nivel_m"], 1.2)
        self.assertEqual(history["statii"][0]["source_url"], C.HYDROWEB_PUBLIC)
        self.assertIsNone(C.cache_get("hydroweb:v3", max_age=10 ** 9))
        self.assertEqual(oct(os.stat(C.DB_PATH).st_mode & 0o777), "0o600")


class ConnectorTests(unittest.TestCase):
    @staticmethod
    def danubehis_chart(points):
        payload = {"series": [{"data": points}]}
        encoded = html.escape(json.dumps(payload), quote=True)
        return (f'<div class="charts-highchart chart" data-chart="{encoded}" '
                'id="hydro_results__attachment_chart_Q"></div>')

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

    def test_anar_parser_keeps_qualitative_current_context_partial(self):
        posts = [{
            "date": "2026-07-28T16:29:16",
            "link": "https://rowater.ro/2026/07/28/context/",
            "title": {"rendered": "28 iulie 2026"},
            "content": {"rendered": """
              <p>În prezent, pe sectorul Călărași-Cernavodă, se mențin
              restricțiile la folosințe (irigații), respectiv treapta III.</p>
              <p>Nu sunt impuse restricții în ceea ce privește alimentarea cu
              apă a populației, coeficientul de umplere în principalele 40 de
              lacuri, volumul de apă fiind suficient pentru alimentarea în sistem centralizat.</p>
            """},
        }]

        out = C._parse_anar_water_resources_posts(posts, date(2026, 8, 5))

        self.assertTrue(out["current"])
        self.assertIsNone(out["reservoirs"]["fill_pct"])
        self.assertEqual(out["reservoirs"]["count"], 40)
        self.assertTrue(out["reservoirs"]["sufficient_for_centralized_supply"])
        self.assertFalse(out["restrictions"]["drinking_water"])
        self.assertTrue(out["restrictions"]["official_statements"])
        self.assertFalse(out["quantitative_complete"])

    def test_anar_parser_reacts_to_new_numbers_and_expires_old_state(self):
        numeric = [{
            "date": "2026-08-04T10:00:00",
            "link": "https://rowater.ro/current/",
            "title": {"rendered": "Rezervele de apă"},
            "content": {"rendered": (
                "<p>La nivel național, coeficientul de umplere în principalele "
                "40 de lacuri este 61,5%, cu un volum de 2,4 miliarde m3.</p>")},
        }]
        current = C._parse_anar_water_resources_posts(numeric, date(2026, 8, 5))
        old = C._parse_anar_water_resources_posts(numeric, date(2026, 8, 25))

        self.assertEqual(current["reservoirs"]["fill_pct"], 61.5)
        self.assertEqual(current["reservoirs"]["volume_billion_m3"], 2.4)
        self.assertTrue(current["quantitative_complete"])
        self.assertTrue(current["current"])
        self.assertFalse(old["current"])
        self.assertEqual(old["status"], "historical_only")

    def test_opcom_parser_reacts_to_delivery_prices_volume_and_month_reference(self):
        page = """
          Prețul mediu ponderat pe PZU pentru Iulie 2026:
          <span>636,46 lei/MWh</span>
          <div>Piata pentru ziua urmatoare - Ziua de livrare 6/8/2026 [lei/MWh]</div>
          <table>
            <tr><td>ROPEX_DAM [lei/MWh]</td><td>960,17</td><td>867,53</td><td>1.052,80</td></tr>
            <tr><td>Volum [MWh]</td><td>38.621,8</td><td>20.416,5</td><td>18.205,3</td></tr>
          </table>
        """

        out = C._parse_opcom_market_html(page)

        self.assertEqual(out["delivery_date"], "2026-08-06")
        self.assertEqual(out["base_lei_mwh"], 960.17)
        self.assertEqual(out["off_peak_lei_mwh"], 1052.8)
        self.assertEqual(out["volume_total_mwh"], 38621.8)
        self.assertEqual(out["previous_month"], {
            "month": "2026-07", "weighted_average_lei_mwh": 636.46,
            "is_previous_to_delivery": True,
        })
        self.assertEqual(out["base_vs_previous_month_pct"], 50.9)

        stale_reference = C._parse_opcom_market_html(
            page.replace("Iulie 2026", "Iunie 2026"))
        self.assertFalse(stale_reference["previous_month"]["is_previous_to_delivery"])
        self.assertIsNone(stale_reference["base_vs_previous_month_pct"])

    def test_damas_summaries_keep_contract_activation_and_consumption_separate(self):
        interval = {"from": "2026-08-04T21:00:00.000Z",
                    "to": "2026-08-04T21:15:00.000Z"}
        consumption = C._summarize_damas_consumption({"itemList": [
            {"timeInterval": interval, "grossForecastConsumption": 6000,
             "grossRealizedConsumption": 6150},
            {"timeInterval": {"from": "2026-08-04T21:15:00.000Z",
                              "to": "2026-08-04T21:30:00.000Z"},
             "grossForecastConsumption": 6200,
             "grossRealizedConsumption": "N/A"},
        ]}, "2026-08-05")
        reserves = C._summarize_damas_reserves({"itemList": [{
            "timeInterval": {"from": "2026-08-04T21:00:00.000Z",
                             "to": "2026-08-05T21:00:00.000Z"},
            "businessState": "tenderResultsPublished",
            "tenderServiceList": [{
                "serviceCode": "FCR",
                "tenderStatistics": {"timeIntervalList": [
                    {"tenderDemand": 128, "tenderSatisfiedDemand": 0.703},
                    {"tenderDemand": 128, "tenderSatisfiedDemand": 1.0},
                ]},
            }],
        }]}, "2026-08-05")
        later_interval = {"from": "2026-08-04T21:15:00.000Z",
                          "to": "2026-08-04T21:30:00.000Z"}
        balancing = C._summarize_damas_balancing(
            {"itemList": [{"timeInterval": interval, "type": "Deficit",
                            "estimatedSystemImbalance": -100.0,
                            "activatedReserve": 98.0},
                           {"timeInterval": later_interval, "type": "Deficit",
                            "estimatedSystemImbalance": -120.0,
                            "activatedReserve": 119.0}]},
            {"itemList": [{"timeInterval": interval,
                            "estimatedPriceNegativeImbalance": 3000.0,
                            "estimatedPricePositiveImbalance": 2800.0}]},
            "2026-08-05")

        self.assertEqual(consumption["realized_intervals"], 1)
        self.assertEqual(consumption["forecast_intervals"], 2)
        self.assertFalse(consumption["complete"])
        self.assertEqual(reserves["minimum_satisfaction_pct"], 70.3)
        self.assertEqual(reserves["minimum_satisfaction_service"], "FCR")
        self.assertFalse(reserves["all_reported_intervals_fully_satisfied"])
        self.assertIn("nu rezerva operațională", reserves["limit"])
        self.assertEqual(balancing["latest"]["activated_reserve_mw"], 119.0)
        self.assertIsNone(
            balancing["latest"]["estimated_negative_imbalance_price_lei_mwh"])
        self.assertEqual(balancing["estimated_negative_price_lei_mwh"]["max"],
                         3000.0)
        self.assertIn("nu arată marja", balancing["limit"])

    def test_romania_market_day_preserves_dst_length(self):
        start, end = C._romania_day(date(2026, 10, 25))
        self.assertEqual((end.astimezone(timezone.utc) -
                          start.astimezone(timezone.utc)).total_seconds(), 25 * 3600)

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

    def test_inhga_monthly_listing_selects_latest_forecast_article(self):
        listing = """
          <article><h2 class="entry-title"><a href="https://example/august">
            Prognoza hidrologică lunară pentru intervalul august-octombrie 2026
          </a></h2></article>
          <article><h2 class="entry-title"><a href="https://example/old">
            Prognoza hidrologică lunară pentru intervalul iulie-septembrie 2026
          </a></h2></article>
        """

        url, title = C._inhga_monthly_latest_url(listing)

        self.assertEqual(url, "https://example/august")
        self.assertIn("august-octombrie 2026", title)

    def test_inhga_monthly_parser_scopes_only_selected_danube_tributaries(self):
        page = """
          <span class="entry-title">Prognoza hidrologică lunară august-octombrie 2026</span>
          <span class="updated">2026-07-31T10:00:00+03:00</span>
          <p>În luna august 2026 regimul hidrologic se va situa la valori cuprinse
          între 30-50% din mediile lunare, mai mari (50-80%) pe Prahova și cursul
          superior al Ialomiței și mai mici (sub 30%) pe cursul superior al Siretului
          și pe afluenții Prutului.</p>
        """

        out = C._parse_inhga_monthly_tributaries(page, "https://example/monthly")
        month = out["months"][0]
        basins = {item["id"]: item for item in
                  month["upstream_cernavoda"] + month["downstream_cernavoda"]}

        self.assertEqual(len(basins), 9)
        self.assertEqual(basins["jiu"]["band_pct"]["max"], 50)
        self.assertEqual(basins["ialomita"]["basis"], "explicit_higher")
        self.assertEqual(basins["ialomita"]["band_pct"]["max"], 80)
        self.assertEqual(basins["siret"]["basis"], "explicit_lower")
        self.assertEqual(basins["prut"]["band_pct"],
                         {"min": None, "max": 30, "operator": "lt"})
        self.assertNotIn("mures", basins)
        self.assertNotIn("som es", basins)

    def test_inhga_monthly_connector_fails_closed_without_stale_fallback(self):
        listing = ('<article><h2 class="entry-title">'
                   '<a href="https://www.hidro.ro/monthly">'
                   'Prognoza hidrologică lunară</a></h2></article>')
        page = ('<span class="entry-title">Prognoza lunară</span>'
                '<span class="updated">2026-07-31T10:00:00+03:00</span>'
                '<p>În luna august 2026 regimul hidrologic se va situa la valori '
                'cuprinse între 30-50% din mediile lunare.</p>')

        def no_cache(key, ttl, fetch_fn, stale_ok=True):
            self.assertFalse(stale_ok)
            return {"data": fetch_fn(), "stale": False}

        with mock.patch.object(C, "cached", side_effect=no_cache) as cache, \
                mock.patch.object(C, "http_get", side_effect=[listing, page]):
            out = C.inhga_danube_tributaries()

        self.assertTrue(out["data"]["available"])
        self.assertEqual(cache.call_args.args[:2],
                         ("inhga_tributaries:v1", 6 * 3600))
        self.assertFalse(cache.call_args.kwargs["stale_ok"])

    def test_inhga_monthly_refuses_href_pointing_off_host(self):
        """href-ul vine din HTML public: nu are voie să devină un GET oriunde."""
        for href in ("https://evil.example/monthly",
                     "file:///etc/passwd",
                     "http://127.0.0.1:7300/api/raport",
                     "https://www.hidro.ro.evil.example/monthly"):
            listing = ('<article><h2 class="entry-title">'
                       f'<a href="{href}">Prognoza hidrologică lunară</a>'
                       '</h2></article>')

            def no_cache(key, ttl, fetch_fn, stale_ok=True):
                return {"data": fetch_fn(), "stale": False}

            with mock.patch.object(C, "cached", side_effect=no_cache), \
                    mock.patch.object(C, "http_get",
                                      side_effect=[listing, "nu ar trebui cerut"]) as get:
                with self.assertRaises(RuntimeError):
                    C.inhga_danube_tributaries()
            # o singură cerere: listingul. Pagina nu a fost descărcată.
            self.assertEqual(get.call_count, 1)

    def test_cross_host_redirect_drops_the_api_key(self):
        """Un activ semnat servit prin 302 nu are voie să ducă cheia altundeva."""
        handler = C._StripAuthOnCrossHost("hydroweb.next.theia-land.fr")
        req = urllib.request.Request(
            "https://hydroweb.next.theia-land.fr/asset",
            headers={"X-API-Key": "cheie-secreta", "User-Agent": "x"})

        moved = handler.redirect_request(
            req, None, 302, "Found", {}, "https://cdn.example/signed-asset")
        kept = handler.redirect_request(
            req, None, 302, "Found", {}, "https://hydroweb.next.theia-land.fr/b")

        headers_moved = {k.lower(): v for k, v in moved.headers.items()}
        headers_kept = {k.lower(): v for k, v in kept.headers.items()}
        self.assertNotIn("x-api-key", headers_moved)
        self.assertEqual(headers_kept.get("x-api-key"), "cheie-secreta")
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(req, None, 302, "Found", {},
                                     "http://hydroweb.next.theia-land.fr/b")

    def test_relaxed_tls_redirect_refuses_cleartext_downgrade(self):
        handler = C._SameHostRedirect("www.hidmet.gov.rs")
        req = urllib.request.Request("https://www.hidmet.gov.rs/a")
        for target in ("http://www.hidmet.gov.rs/a",
                       "https://www.hidmet.gov.rs.evil.example/a"):
            with self.assertRaises(urllib.error.HTTPError):
                handler.redirect_request(req, None, 302, "Found", {}, target)

    def test_response_size_is_bounded(self):
        class _Resp:
            def __init__(self, payload):
                self.payload = payload

            def read(self, amt=None):
                return self.payload[:amt] if amt is not None else self.payload

        self.assertEqual(C._read_bounded(_Resp(b"abc"), 10), b"abc")
        with self.assertRaises(RuntimeError):
            C._read_bounded(_Resp(b"x" * 5000), 100)

    def test_png_decoder_refuses_a_decompression_bomb(self):
        header = struct.pack(">IIBBBBB", 40000, 40000, 8, 6, 0, 0, 0)
        raw = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(header)) + b"IHDR" \
            + header + b"\x00\x00\x00\x00"
        with self.assertRaises(ValueError):
            C._png_rgba_stats(raw)

    def test_inhga_bulletin_refresh_window_is_thirty_minutes(self):
        cached = {"data": {}, "stale": False, "cache_age_s": 0}
        with mock.patch.object(C, "cached", return_value=cached) as cache:
            out = C.inhga_bulletin()

        self.assertEqual(out, cached)
        self.assertEqual(cache.call_args.args[0], "inhga_bulletin:v2")
        self.assertEqual(cache.call_args.args[1], 30 * 60)

    def test_inhga_bulletin_replaces_all_fields_and_official_text_on_new_issue(self):
        def listing(ds):
            return (f'<a href="https://www.hidro.ro/bulletin/diagnoza-si-prognoza-'
                    f'hidrologica-pentru-dunare-la-intrarea-in-tara-si-pe-sectorul-'
                    f'romanesc-{ds}/">buletin</a>')

        page_04 = (
            "<p>Debitul la intrarea în țară (secțiunea Baziaș) a fost în scădere "
            "la valoarea de 1.450 m³/s, sub media multianuală a lunii august "
            "(3.900 m³/s).</p><p>Debitul la intrarea în țară (secțiunea Baziaș) "
            "va fi în scădere ușoară în prima zi până la valoarea de 1.400 m³/s, "
            "apoi staționar până la sfârșitul intervalului.</p>")
        page_05 = (
            "<p>Debitul la intrarea în țară (secțiunea Baziaș) a fost în creștere "
            "la valoarea de 1.525 m³/s, sub media multianuală a lunii august "
            "(3.950 m³/s).</p><p>Debitul la intrarea în țară (secțiunea Baziaș) "
            "va fi în creștere ușoară în prima zi până la valoarea de 1.575 m³/s, "
            "apoi staționar până la sfârșitul intervalului.</p>")

        responses = iter([listing("04-08-2026"), page_04,
                          listing("05-08-2026"), page_05])

        def uncached(key, ttl, fetch_fn, stale_ok=True):
            return {"data": fetch_fn(), "stale": False, "cache_age_s": 0}

        with mock.patch.object(C, "http_get", side_effect=lambda *a, **k: next(responses)), \
                mock.patch.object(C, "cached", side_effect=uncached), \
                mock.patch.object(C, "cache_put"):
            old = C.inhga_bulletin()["data"]
            new = C.inhga_bulletin()["data"]

        self.assertEqual(old["data_buletin"], "2026-08-04")
        self.assertEqual(old["debit_bazias_m3s"], 1450.0)
        self.assertEqual(old["prognoza_debit_m3s"], 1400.0)
        self.assertEqual(new["data_buletin"], "2026-08-05")
        self.assertEqual(new["debit_bazias_m3s"], 1525.0)
        self.assertEqual(new["media_multianuala_m3s"], 3950.0)
        self.assertEqual(new["tendinta"], "creștere")
        self.assertEqual(new["prognoza_debit_m3s"], 1575.0)
        self.assertNotEqual(old["text_oficial"], new["text_oficial"])
        self.assertIn("1.525", new["text_oficial"][0])
        self.assertTrue(new["url"].endswith("05-08-2026/"))

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

    def test_danubehis_public_chart_builds_descriptive_section_statistics(self):
        def stamp(value):
            return int(datetime.fromisoformat(value).replace(
                tzinfo=timezone.utc).timestamp() * 1000)

        page = self.danubehis_chart([
            [stamp("2025-01-01T05:00:00"), 10.0],
            [stamp("2025-08-03T05:00:00"), 20.0],
            [stamp("2026-01-01T05:00:00"), 4.0],
            [stamp("2026-08-01T04:00:00"), 3.0],
            [stamp("2026-08-01T05:00:00"), 3.5],
            [stamp("2026-08-02T05:00:00"), 4.5],
            [stamp("2026-08-03T05:00:00"), 5.5],
        ])
        spec = next(item for item in C.DANUBEHIS_RO_TRIBUTARY_SECTIONS
                    if item["river_id"] == "vedea")

        parsed = C._parse_danubehis_q_chart(page)
        out = C._danubehis_ro_section_stats(spec, page, date(2026, 8, 5))

        self.assertEqual(len(parsed), 6)
        self.assertEqual(parsed[-3], {"date": "2026-08-01", "value_m3s": 3.5})
        self.assertEqual(out["latest"], {"date": "2026-08-03", "value_m3s": 5.5})
        self.assertEqual(out["current_month"]["median_m3s"], 4.5)
        self.assertEqual(out["ytd"]["min_m3s"], 3.5)
        self.assertEqual(out["ytd"]["max_m3s"], 5.5)
        self.assertEqual(out["ytd"]["days"], 4)
        self.assertEqual(out["previous_year_same_window"]["year"], 2025)
        self.assertIn("nu include", out["coverage"])

    def test_danubehis_tributary_parser_fails_when_chart_schema_changes(self):
        with self.assertRaisesRegex(RuntimeError, "graficul Q"):
            C._parse_danubehis_q_chart("<html>fără grafic</html>")

    def test_glofas_tributary_climatology_uses_only_its_own_model_history(self):
        times, values = [], []
        for year in range(2000, 2026):
            start = date(year, 7, 28)
            for offset in range(15):
                times.append((start + timedelta(days=offset)).isoformat())
                values.append(100.0)
        times.append("2026-08-04")
        values.append(50.0)
        payload = {
            "latitude": 43.975006, "longitude": 25.325012,
            "daily": {"time": times, "river_discharge": values},
        }
        spec = next(item for item in C.DANUBEHIS_RO_TRIBUTARY_SECTIONS
                    if item["river_id"] == "vedea")

        out = C._glofas_tributary_climate_summary(
            spec, payload, date(2026, 8, 4))

        self.assertEqual(out["model_date"], "2026-08-04")
        self.assertEqual(out["model_m3s"], 50.0)
        self.assertEqual(out["climate_median_m3s"], 100.0)
        self.assertEqual(out["percentile"], 0.0)
        self.assertEqual(out["deviation_pct"], -50.0)
        self.assertEqual(out["reference_samples"], 390)
        self.assertEqual(out["reference_period"], {"start": 2000, "end": 2025})

    def test_glofas_multi_request_keeps_coordinate_order(self):
        seen = []

        def fake_json(url, timeout=25):
            seen.append(url)
            return [{"daily": {"time": [], "river_discharge": []}},
                    {"daily": {"time": [], "river_discharge": []}}]

        points = [{"lat": 44.25, "lon": 23.78},
                  {"lat": 45.99, "lon": 25.30}]
        with mock.patch.object(C, "http_json", side_effect=fake_json):
            rows = C._flood_multi_call(points, {
                "start_date": "1991-01-01", "end_date": "2026-08-04"})

        query = parse_qs(urlparse(seen[0]).query)
        self.assertEqual(len(rows), 2)
        self.assertEqual(query["latitude"], ["44.25000,45.99000"])
        self.assertEqual(query["longitude"], ["23.78000,25.30000"])
        self.assertEqual(query["start_date"], ["1991-01-01"])

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
        self.assertEqual(entry["source_url"], C.HYDROWEB_PUBLIC)
        self.assertNotIn("token", json.dumps(entry).lower())

    def test_hidmet_preserves_observation_time_and_marks_tls_limit(self):
        page = """<h1>Hydrological data: &nbsp;WEDNESDAY&nbsp;05.08.2026.
          &nbsp;&nbsp;time:&nbsp;8:00&nbsp;(06:00 UTC)</h1>
          <tr><td>DUNAV</td><td>x</td><td><a href="prognoza.php?hm_id=42035">NOVI SAD</a></td>
          <td>x</td><td>x</td><td>-73</td><td>-1</td><td>770</td><td>27.0</td>
          <td><img src="tendencije/stag.gif"></td></tr>"""

        def uncached(key, ttl, fetch_fn, stale_ok=True):
            return {"data": fetch_fn(), "stale": False}

        with mock.patch.object(C, "http_get", return_value=page), \
                mock.patch.object(C, "cached", side_effect=uncached), \
                mock.patch.object(C, "daily_snapshot"):
            out = C.hidmet_report()["data"]

        self.assertEqual(out["data"], "2026-08-05")
        self.assertIn("2026-08-05T08:00:00", out["observation_time"])
        self.assertFalse(out["transport_verified"])
        self.assertEqual(out["statii"][0]["debit_m3s"], 770.0)

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
        self.assertEqual(C.EVIDENCE_SOURCES["glofas"]["family"],
                         C.EVIDENCE_SOURCES["glofas_ro_tributaries"]["family"])
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

    def test_serbia_check_does_not_count_unverified_rhmz_transport(self):
        rhmz = {"data": {"transport_verified": False, "statii": [
            {"statie": "Novi Sad", "debit_m3s": 700.0}]}}
        model = {"data": {"time": ["2026-08-05"], "discharge": [1000.0]}}
        with mock.patch.object(C, "hidmet_report", return_value=rhmz), \
                mock.patch.object(C, "hydroinfo_danube", side_effect=OSError("down")), \
                mock.patch.object(C, "glofas_recent", return_value=model):
            contextual = anomalii.serbia_check()

        self.assertFalse(contextual["integrity_eligible"])
        self.assertEqual(contextual["verification_family"], "gauge_rs_rhmz")
        self.assertIn("TLS", contextual["limit"])

        hydroinfo = {"data": {"statii": [
            {"statie": "Novi Sad", "debit_m3s": 770.0}]}}
        with mock.patch.object(C, "hidmet_report", return_value=rhmz), \
                mock.patch.object(C, "hydroinfo_danube", return_value=hydroinfo), \
                mock.patch.object(C, "glofas_recent", return_value=model):
            verified_delivery = anomalii.serbia_check()

        self.assertTrue(verified_delivery["integrity_eligible"])
        self.assertEqual(verified_delivery["verification_family"], "gauge_hu_ovf")
        self.assertEqual(verified_delivery["source"], "OVF/Hydroinfo")

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

    def test_precipitation_surfaces_use_the_same_rolling_90_day_reference(self):
        start, end = date(2000, 1, 1), date(2026, 7, 29)
        days = (end - start).days + 1
        dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
        values = [0.5 if ds.startswith("2026-") else 1.0 for ds in dates]
        series = {"time": dates, "precip": values, "snow": [0.0] * days}
        all_points = {pid: series for pid in C.PRECIP_POINTS}

        with mock.patch.object(C, "era5_precip_all",
                               return_value={"data": all_points}):
            coherence = anomalii.precip_coherence(0)
            stats = anomalii.precip_stats()

        by_id = {row["id"]: row for row in stats}
        for row, (pid, _) in zip(coherence["zone"], anomalii.PRECIP_VS):
            self.assertEqual(row["cum90_mm"], by_id[pid]["ultimele90"]["cumul_mm"])
            self.assertEqual(row["pct"], by_id[pid]["ultimele90"]["pct"])
            self.assertEqual(row["mostre_referinta"],
                             by_id[pid]["ultimele90"]["mostre_referinta"])


class AiAnalysisAuditTests(unittest.TestCase):
    def test_ai_default_is_status_only_and_never_calls_model(self):
        with mock.patch.object(analiza_ai, "_ai_key",
                               side_effect=AssertionError("cheia nu trebuie citită")), \
                mock.patch.object(analiza_ai, "_analiza_locked") as locked:
            result = analiza_ai.analiza()

        self.assertFalse(result["activ"])
        self.assertTrue(result["manual_only"])
        locked.assert_not_called()

    def test_ai_manual_run_is_fresh_and_explicit(self):
        expected = {"data": {"activ": True}, "stale": False}
        with mock.patch.object(analiza_ai, "_ai_key", return_value="secret"), \
                mock.patch.object(analiza_ai, "AI_WEB_SEARCH", False), \
                mock.patch.object(analiza_ai, "_analiza_locked",
                                  return_value=expected) as locked:
            result = analiza_ai.analiza(run=True)

        self.assertEqual(result, expected)
        locked.assert_called_once_with("secret")

    def test_http_endpoint_cannot_trigger_ai_even_with_run_query(self):
        status = {"activ": False, "manual_only": True, "motiv": "manual"}
        with mock.patch.object(server.analiza_ai, "analiza",
                               return_value=status) as run:
            result = server.api_analiza_ai({"run": ["1"]})

        self.assertEqual(result, status)
        run.assert_called_once_with(run=False)

    def test_background_maintenance_has_no_ai_call(self):
        source = inspect.getsource(server.maintenance_watcher)
        self.assertNotIn("analiza_ai", source)

    def test_digest_carries_errors_lineage_hungary_freshness_and_context(self):
        report = {
            "climatologie": [{"id": "bazias", "recent": [1, 2],
                               "azi": {"date": "2026-08-03", "value": 1200},
                               "sursa": "GloFAS test", "rezolutie_spatiala_aprox_km": 5,
                               "celula_model": {"lat": 44.775, "lon": 21.375}}],
            "bilant": {"z": 0.2},
            "masurat_vs_model": {"serie": [1], "z": 0.1,
                                  "ultima_pereche_aceeasi_data": {
                                      "date": "2026-08-03", "oficial": 900,
                                      "model": 1200, "raport": 0.75}},
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
                mock.patch.object(analiza_ai, "_inhga", return_value={
                    "data_buletin": "2026-08-04", "debit_bazias_m3s": 900}), \
                mock.patch.object(C, "inhga_danube_tributaries",
                                  return_value={"data": {"tip": "prognoza"},
                                                "stale": False, "cache_age_s": 4}), \
                mock.patch.object(C, "danubehis_romanian_tributaries",
                                  return_value={"data": {"tip": "masurat"},
                                                "stale": False, "cache_age_s": 6}), \
                mock.patch.object(C, "glofas_romanian_tributary_climatology",
                                  return_value={"data": {"tip": "model_climatic"},
                                                "stale": False, "cache_age_s": 7}), \
                mock.patch.object(C, "anar_water_resources",
                                  return_value={"data": {"tip": "anar"},
                                                "stale": False, "cache_age_s": 8}), \
                mock.patch.object(C, "snn_cernavoda_status",
                                  return_value={"data": {"tip": "snn"},
                                                "stale": False, "cache_age_s": 9}), \
                mock.patch.object(C, "sen_live",
                                  return_value={"data": {"tip": "sen"},
                                                "stale": False, "cache_age_s": 10}), \
                mock.patch.object(C, "sen_history_context",
                                  return_value={"tip": "sen_history"}), \
                mock.patch.object(C, "sen_market_context",
                                  return_value={"tip": "energy_market",
                                                "available_components": 4}), \
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
        self.assertEqual(digest["prognoza_afluenti_inhga"]["date"]["tip"],
                         "prognoza")
        self.assertEqual(digest["statistici_afluenti_masurati"]["date"]["tip"],
                         "masurat")
        self.assertEqual(digest["statistici_afluenti_masurati"]["livrare"]["cache_age_s"],
                         6)
        self.assertEqual(digest["climatologie_modelata_afluenti"]["date"]["tip"],
                         "model_climatic")
        self.assertEqual(digest["context_resurse_apa_anar"]["date"]["tip"], "anar")
        self.assertEqual(digest["stare_cne_snn"]["date"]["tip"], "snn")
        self.assertEqual(digest["stare_sen"]["date"]["tip"], "sen")
        self.assertEqual(digest["istoric_sen_local"]["date"]["tip"], "sen_history")
        self.assertEqual(digest["context_piata_energie"]["date"]["tip"],
                         "energy_market")
        bazias = digest["reconciliere_bazias"]
        self.assertEqual(bazias["valoare_oficiala_curenta"]["debit_m3s"], 900)
        self.assertEqual(bazias["reper_modelat_climatologic"]["debit_m3s"], 1200)
        self.assertFalse(bazias["date_curente_aliniate"])
        self.assertEqual(bazias["ultima_pereche_aceeasi_data"]["raport"], 0.75)

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
        self.assertEqual(web["body"]["text"]["verbosity"], "low")
        self.assertEqual(web["body"]["tools"][0]["type"], "web_search")
        self.assertEqual(web["body"]["tool_choice"], "required")
        self.assertGreaterEqual(web["body"]["max_output_tokens"], 3000)
        self.assertEqual(web["body"]["model"], "web-model")

    def test_responses_parser_marks_deduplicated_clickable_sources(self):
        citation = "([hidro.ro](https://example.org/report))"
        text = f"Nivel scăzut. {citation} Context suplimentar."
        start, end = text.index(citation), text.index(citation) + len(citation)
        out = {"output": [
            {"type": "web_search_call", "action": {"queries": ["Danube official flow"]}},
            {"type": "message", "content": [{
                "type": "output_text", "text": text,
                "annotations": [
                    {"type": "url_citation", "url": "https://example.org/report",
                     "title": "Official report", "start_index": start,
                     "end_index": end},
                    {"type": "url_citation", "url": "https://example.org/report",
                     "title": "Official report", "start_index": start,
                     "end_index": end},
                    {"type": "url_citation", "url": "javascript:alert(1)",
                     "title": "unsafe", "start_index": start,
                     "end_index": end},
                ],
            }]},
        ]}

        parsed, citations, queries = analiza_ai._parse_responses_output(out)

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["url"], "https://example.org/report")
        self.assertEqual(parsed.count("⟦WEB:1⟧"), 1)
        self.assertNotIn("[hidro.ro]", parsed)
        self.assertNotIn("https://example.org/report", parsed)
        self.assertEqual(queries, ["Danube official flow"])

    def test_prompt_requires_integrity_checks_and_external_separation(self):
        prompt = analiza_ai.PROMPT_SISTEM
        self.assertIn("ANOMALII DE DATE ȘI CONTRADICȚII", prompt)
        self.assertIn("VERIFICARE EXTERNĂ", prompt)
        self.assertIn("registru_provenienta", prompt)
        self.assertIn("două căi de livrare", prompt)
        self.assertIn("catalog_only", prompt)
        self.assertIn("nu a fost efectuată", prompt)
        self.assertIn("validarea sursei deja ingerate", prompt)
        self.assertIn("nu adaugă independență", prompt)
        self.assertIn("intervalul datelor disponibile", prompt)
        self.assertIn("cifra canonică a monitorului", prompt)
        self.assertIn("nu este „contradicție”", prompt)
        self.assertIn("statistici_afluenti_masurati", prompt)
        self.assertIn("nu sunt medii zilnice", prompt)
        self.assertIn("nu se însumează", prompt)
        self.assertIn("un singur an, nu climatologia", prompt)
        self.assertIn("climatologie_modelata_afluenti", prompt)
        self.assertIn("nu transferă percentila modelului", prompt)
        self.assertIn("fereastra calendaristică ±7 zile", prompt)
        self.assertIn("ani_mai_mici folosește separat aceeași dată exactă", prompt)
        self.assertIn("context_resurse_apa_anar", prompt)
        self.assertIn("stare_cne_snn", prompt)
        self.assertIn("istoric_sen_local", prompt)
        self.assertIn("context_piata_energie", prompt)
        self.assertIn("Nu numi rezervele contractate „disponibile”", prompt)
        self.assertIn("nu atribui un preț PZU", prompt)

    def test_response_heading_normalization_is_narrow_and_auditable(self):
        text = "## SITUAȚIA\nFapt.\n\n**CE AR SCHIMBĂ CONCLUZIA:**\nAlt fapt."
        normalized, missing = analiza_ai._normalize_response(text)

        self.assertIn("SITUAȚIA\nFapt.", normalized)
        self.assertIn("CE AR SCHIMBA CONCLUZIA\nAlt fapt.", normalized)
        self.assertIn("CAUZE PROBABILE", missing)


class WindowStatisticsTests(unittest.TestCase):
    """Etalonul unui z pe fereastră e distribuția mediilor de aceeași lungime."""

    @staticmethod
    def _series(days, start="2020-01-01", step=1.0):
        first = date.fromisoformat(start)
        return {(first + timedelta(days=i)).isoformat(): i * step
                for i in range(days)}

    def test_rolling_means_skip_windows_with_a_gap(self):
        smap = self._series(40)
        del smap["2020-01-20"]           # un gol în mijloc
        means = anomalii._rolling_window_means(smap, 14)
        # nicio fereastră nu are voie să acopere golul
        self.assertTrue(means)
        full = anomalii._rolling_window_means(self._series(40), 14)
        self.assertLess(len(means), len(full))

    def test_rolling_means_filter_by_month_and_exclude_year(self):
        smap = self._series(800)          # acoperă martie 2020, 2021 și 2022
        all_march = anomalii._rolling_window_means(smap, 14, month=3)
        means = anomalii._rolling_window_means(
            smap, 14, month=3, exclude_year=2021)
        self.assertTrue(means)
        self.assertLess(len(means), len(all_march))
        # ferestrele rămase se încheie toate în martie, niciuna în 2021
        self.assertEqual(len(all_march) - len(means), 31)

    def test_contiguous_tail_refuses_a_broken_run(self):
        smap = self._series(20)
        self.assertEqual(len(anomalii._contiguous_tail(smap, 14)), 14)
        del smap["2020-01-15"]
        self.assertIsNone(anomalii._contiguous_tail(smap, 14))
        self.assertIsNone(anomalii._contiguous_tail(self._series(5), 14))

    def test_report_declares_which_inputs_came_from_cache(self):
        """`stale: false` pe raport înseamnă doar „recalculat recent". Dacă
        intrările au venit din cache expirat, verdictele se sprijină pe date
        vechi cu raportul proaspăt — iar asta trebuie spus, nu dedus."""
        anomalii._track_reset()
        self.assertEqual(anomalii._tracked(), [])
        # o sursă proaspătă nu se contorizează
        self.assertEqual(anomalii._d({"data": 1, "stale": False}, "proaspata"), 1)
        self.assertEqual(anomalii._tracked(), [])
        # două surse expirate, una repetată
        anomalii._d({"data": 1, "stale": True}, "afdj")
        anomalii._d({"data": 1, "stale": True}, "rhmz")
        anomalii._d({"data": 1, "stale": True}, "afdj")
        self.assertEqual(anomalii._tracked(), ["afdj", "rhmz"])
        # o valoare fără înveliș trece neatinsă
        self.assertEqual(anomalii._d({"x": 1}, "brut"), {"x": 1})
        anomalii._track_reset()
        self.assertEqual(anomalii._tracked(), [])

    def test_rank_refuses_a_degenerate_reference(self):
        self.assertIsNone(anomalii._rank(1.0, [1.0, 2.0]))
        self.assertIsNotNone(anomalii._rank(1.0, list(range(40))))

    def test_snn_is_not_accepted_from_a_stale_or_unreviewed_report(self):
        today = date(2026, 8, 7)
        fresh = {"status_available": True, "needs_review": False,
                 "date": "2026-08-06"}
        self.assertTrue(romania._snn_accepted(fresh, False, today))
        # cache expirat: prospețimea înghețată la preluare nu mai contează
        self.assertFalse(romania._snn_accepted(fresh, True, today))
        self.assertFalse(romania._snn_accepted(
            {**fresh, "needs_review": True}, False, today))
        # raport vechi de două săptămâni
        self.assertFalse(romania._snn_accepted(
            {**fresh, "date": "2026-07-24"}, False, today))
        self.assertFalse(romania._snn_accepted({"status_available": True},
                                               False, today))

    def test_third_party_text_reaching_the_prompt_is_bounded(self):
        payload = {"eroare": "x" * 5000, "lista": ["y" * 5000],
                   "control": "a\x00b\x1fc", "numar": 12.5}
        out = analiza_ai._defang(payload)
        self.assertLessEqual(len(out["eroare"]), analiza_ai.MAX_PROMPT_STRING + 20)
        self.assertLessEqual(len(out["lista"][0]), analiza_ai.MAX_PROMPT_STRING + 20)
        self.assertNotIn("\x00", out["control"])
        self.assertNotIn("\x1f", out["control"])
        self.assertEqual(out["numar"], 12.5)

    def test_damas_interval_is_read_not_assumed(self):
        hourly = [{"timeInterval": {"from": "2026-08-05T00:00:00Z",
                                    "to": "2026-08-05T01:00:00Z"}}]
        quarter = [{"timeInterval": {"from": "2026-08-05T00:00:00Z",
                                     "to": "2026-08-05T00:15:00Z"}}]
        self.assertEqual(C._damas_interval_minutes(hourly), 60)
        self.assertEqual(C._damas_interval_minutes(quarter), 15)
        self.assertEqual(C._damas_interval_minutes([]), 15)

    def test_anar_decimal_is_not_read_as_thousands(self):
        pattern = r"([\d.,]+)\s*miliarde"
        self.assertEqual(C._anar_number(pattern, "2.4 miliarde m3"), 2.4)
        self.assertEqual(C._anar_number(pattern, "2,4 miliarde m3"), 2.4)
        # separatorul de mii în română rămâne interpretat corect
        self.assertEqual(C._num("1.450"), 1450.0)


class CalendarTests(unittest.TestCase):
    def test_calendar_window_wraps_at_new_year(self):
        self.assertEqual(anomalii._doy_diff("01-02", "12-31"), 2)
        self.assertEqual(anomalii._doy_diff("12-31", "01-02"), -2)


if __name__ == "__main__":
    unittest.main()
