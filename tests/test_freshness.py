import contextlib
import io
import json
import subprocess
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import freshness
import server
from ops import source_freshness

NOW = datetime.fromisoformat('2026-09-12T12:00:00+03:00')


class ObservationFreshnessTests(unittest.TestCase):
    def test_mixed_dates_do_not_turn_successful_delivery_into_fresh_observations(self):
        original = {'stale': False, 'statii': [
            {'statie': 'Budapest', 'data': '2026-09-12'},
            {'statie': 'Gönyű', 'data': '2026-08-13'},
            {'statie': 'Mohács', 'data': '2026-09-10'},
        ]}
        result = freshness.annotate('danubehis', original, now=NOW)
        self.assertFalse(result['stale'])
        f = result['observation_freshness']
        self.assertEqual(f['status'], 'partial_stale')
        self.assertEqual(f['counts'], {'fresh': 2, 'stale': 1, 'unknown': 0})
        self.assertEqual(f['problems'][0]['age'], 30)
        self.assertNotIn('observation_freshness', original['statii'][1])
        problems = source_freshness._observation_problems(result)
        self.assertEqual(len(problems), 1)
        self.assertIn('Gönyű', problems[0])

    def test_missing_future_invalid_and_empty_dates_are_unknown(self):
        for value in (None, 'invalid', '2026-09-13', ''):
            result = freshness.annotate('inhga', {'data_buletin': value}, now=NOW)
            self.assertEqual(result['observation_freshness']['status'], 'unknown')
        result = freshness.annotate('afdj', {'statii': []}, now=NOW)
        self.assertEqual(result['observation_freshness']['status'], 'unknown')
        self.assertTrue(source_freshness._observation_problems(result))

    def test_publication_tolerances_and_timestamp_offsets(self):
        for day, expected in [('2026-09-11', 'fresh'), ('2026-09-10', 'stale')]:
            result = freshness.annotate('inhga', {'data_buletin': day}, now=NOW)
            self.assertEqual(result['observation_freshness']['status'], expected)
        for ts, expected in [('26/9/12 11:30:00', 'fresh'), ('26/9/12 11:29:59', 'stale')]:
            result = freshness.annotate('sen', {'actualizat': ts}, now=NOW)
            self.assertEqual(result['observation_freshness']['status'], expected)
        result = freshness.annotate('danubeportal', {'mire': [
            {'statie': 'test', 'masurat_utc': '2026-09-12T09:00:00Z'}]}, now=NOW)
        self.assertEqual(result['observation_freshness']['status'], 'fresh')
        # Source calendar day is retained across the UTC midnight boundary.
        result = freshness.annotate('afdj', {'statii': [
            {'actualizat': '2026-09-10T00:00:00+03:00'}]}, now=NOW)
        self.assertEqual(result['observation_freshness']['status'], 'fresh')

    def test_each_pegel_parameter_is_evaluated_and_history_is_not_reclassified(self):
        result = freshness.annotate('pegelonline', {'stations': [{
            'name': 'Test', 'q': {'ts': '2026-09-12T09:00:00Z'},
            'w': {'ts': '2026-09-10T09:00:00Z'}}]}, now=NOW)
        self.assertEqual(result['observation_freshness']['status'], 'partial_stale')
        self.assertEqual(result['observation_freshness']['problems'][0]['station'], 'Test W')
        history = {'status': 'historical_only', 'age_days': 46}
        self.assertEqual(freshness.annotate('anar_resurse_apa', history, now=NOW), history)
        self.assertEqual(source_freshness._observation_problems(history), [])

    def test_existing_monitor_detects_observation_age_and_maintenance_failure(self):
        old = freshness.annotate('danubehis', {'stale': False, 'statii': [
            {'statie': 'Gönyű', 'data': '2026-08-13'}]}, now=NOW)
        def fetch(base, path, timeout):
            if path == '/api/health':
                return {'status': 'ok', 'warmup_done': True, 'anomaly_report_age_s': 60,
                        'maintenance': {'inhga': {'status': 'failed'}}}
            return old if path == '/api/danubehis' else {}
        with mock.patch.object(source_freshness, '_fetch_json', fetch):
            result = source_freshness.collect_evidence('http://local', 1, 12)
        self.assertEqual(result['state'], 'failed')
        self.assertTrue(any('Gönyű' in p for p in result['staleSources']))
        self.assertIn('/api/health: maintenance inhga: failed', result['failures'])

    def test_romania_preserves_input_fallback_and_optional_failure(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(server.C, 'cached', side_effect=lambda k, t, f: {'data': f(), 'stale': False}))
            stack.enter_context(mock.patch.object(server, '_stats_cached', return_value={'data': {}, 'stale': True, 'cache_age_s': 25000}))
            for name in ('glofas_archive', 'afdj_cote', 'inhga_bulletin', 'inhga_danube_tributaries',
                         'danubehis_romanian_tributaries', 'glofas_romanian_tributary_climatology',
                         'sen_live', 'anar_water_resources', 'snn_cernavoda_status'):
                stack.enter_context(mock.patch.object(server.C, name, return_value={'data': {}, 'stale': False}))
            stack.enter_context(mock.patch.object(server.C, 'afdj_cote', side_effect=RuntimeError('private detail')))
            for name in ('sen_history_context', 'sen_market_context'):
                stack.enter_context(mock.patch.object(server.C, name, return_value={}))
            stack.enter_context(mock.patch.object(server.romania, 'build_report', return_value={}))
            report = server.api_romania({})
        self.assertFalse(report['stale'])
        self.assertTrue(report['source_freshness']['statistici']['stale'])
        self.assertEqual(report['source_freshness']['statistici']['cache_age_s'], 25000)
        self.assertTrue(report['source_freshness']['afdj']['stale'])
        self.assertNotIn('private detail', json.dumps(report))

    def test_download_report_preserves_nested_source_delivery(self):
        with contextlib.ExitStack() as stack:
            for name in ('inhga_bulletin', 'inhga_danube_tributaries', 'danubehis_romanian_tributaries',
                         'glofas_romanian_tributary_climatology', 'afdj_cote', 'hidmet_report',
                         'hydroinfo_danube', 'danubehis_danube', 'edo_status', 'opera_surface_status',
                         'copernicus_land_context', 'earthdata_satellite_catalog', 'sen_live',
                         'anar_water_resources', 'cached'):
                stack.enter_context(mock.patch.object(server.C, name, return_value={'data': {'sentinel': 42}, 'stale': True, 'cache_age_s': 90000}))
            for name in ('evidence_source_registry', 'sen_history_context', 'sen_market_context'):
                stack.enter_context(mock.patch.object(server.C, name, return_value={}))
            for name in ('api_missing_data', 'api_romania'):
                stack.enter_context(mock.patch.object(server, name, return_value={}))
            report = server._build_report_snapshot()
        self.assertTrue(report['sectiuni']['hydroinfo']['stale'])
        self.assertEqual(report['sectiuni']['hydroinfo']['cache_age_s'], 90000)
        self.assertEqual(report['sectiuni']['hydroinfo']['sentinel'], 42)

    def test_maintenance_failure_is_isolated_and_due_work_retries(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(server.MAINTENANCE_STATUS, {}, clear=True))
            stack.enter_context(mock.patch.object(server.C, 'cache_get', return_value=None))
            stack.enter_context(mock.patch.object(server.C, 'inhga_bulletin', side_effect=RuntimeError('secret')))
            failed = stack.enter_context(mock.patch.object(server.C, 'inhga_danube_tributaries', side_effect=[RuntimeError(), {'stale': False}]))
            passed = []
            for name in ('danubehis_romanian_tributaries', 'anar_water_resources', 'glofas_romanian_tributary_climatology', 'cache_gc'):
                passed.append(stack.enter_context(mock.patch.object(server.C, name, return_value={})))
            report = stack.enter_context(mock.patch.object(server, '_refresh_report_snapshot', return_value={'stale': False}))
            stream = io.StringIO()
            with contextlib.redirect_stderr(stream):
                server.maintenance_cycle()
                self.assertEqual(server.MAINTENANCE_STATUS['inhga_tributaries']['status'], 'failed')
                server.maintenance_cycle()
            self.assertNotIn('secret', stream.getvalue())
            self.assertEqual(report.call_count, 2)
            self.assertEqual(failed.call_count, 2)
            self.assertTrue(all(fn.call_count == 1 for fn in passed))
            self.assertEqual(server.MAINTENANCE_STATUS['inhga_tributaries']['status'], 'ok')

    def test_browser_refresh_contract(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['node', '--test', 'tests/refresh.test.mjs'], cwd=root, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
