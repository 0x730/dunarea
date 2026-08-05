import inspect
import html
import json
import os
import sqlite3
import struct
import tempfile
import time
import unittest
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
        self.assertIn('data-view="romania"', page)
        self.assertIn("/api/romania", app)
        self.assertIn("rare_not_unprecedented", app)
        self.assertIn("nicio concluzie veche nu este păstrată", app)
        self.assertIn('id="ro-operational-history"', page)
        self.assertIn('id="ro-historical-thresholds"', page)
        self.assertIn('id="ro-parameter-coverage"', page)
        self.assertIn('id="ro-tributaries"', page)
        self.assertIn("gauge?.temp_apa_c", app)
        self.assertIn("gauge?.tendinte_cm", app)
        self.assertIn("mediană ian.–azi", app)
        self.assertIn("point.ytd_mm", app)
        self.assertIn("secțiuni măsurate", page)
        self.assertNotIn("1.450 m³/s", page.split('data-view="romania"', 1)[1])
        self.assertNotIn("-214 cm", page.split('data-view="romania"', 1)[1])
        self.assertNotIn("27.5°C", page.split('data-view="romania"', 1)[1])
        self.assertNotIn("-226 cm", page.split('data-view="romania"', 1)[1])
        self.assertNotIn("U1 oprită", page.split('data-view="romania"', 1)[1])


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
        self.assertEqual(claims["national_energy_crisis"]["status"], "not_demonstrated")
        self.assertIn("nu este demonstrată", out["headline"])
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
            tributary_observations=self.tributary_observations())
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
        self.assertEqual(out["data_as_of"]["danubehis_ro_tributaries"],
                         "2026-08-03")

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
                                   sen, self.snn())
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

        out = romania.build_report(stats, archive, afdj, inhga, sen, self.snn())
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
        listing = ('<article><h2 class="entry-title"><a href="https://example/monthly">'
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
