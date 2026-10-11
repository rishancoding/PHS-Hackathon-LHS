"""The budget scope changes affordability while preserving full installed estimates."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from panel_plan import propose_panels
from panel_recommendations import build_recommendations, validate_recommendation_inputs
from roofready_app import create_app
from solar_address import InputError
from solar_report import PDFPages
from test_panel_plan import fixture
from test_panel_recommendations import model


class BudgetScopeTests(unittest.TestCase):
    def test_panels_only_budget_fits_more_without_hiding_extra_cost(self):
        catalog = [model('small'), model('large', watts=200, price=150)]
        installed = deepcopy(build_recommendations(fixture(), 500, 10000, catalog=catalog))
        hardware = build_recommendations(fixture(), 500, 10000, catalog=catalog, budget_includes_installation=False)
        self.assertGreater(hardware['proposal']['panel_count'], installed['proposal']['panel_count'])
        for item in hardware['panel_recommendations']['recommendations']:
            self.assertFalse(item['budget_includes_installation'])
            self.assertLessEqual(item['budget_cost_usd'], 500)
            self.assertEqual(item['budget_cost_usd'], item['estimated_panel_hardware_cost_usd'])
            self.assertAlmostEqual(
                item['estimated_cost_usd'],
                item['estimated_panel_hardware_cost_usd'] + item['estimated_other_equipment_installation_cost_usd'],
                places=2,
            )
            self.assertGreater(item['estimated_cost_usd'], 500)
        self.assertEqual(hardware['proposal']['panel_count'], 10)
        self.assertEqual(hardware['proposal']['budget_cost_usd'], 500)
        self.assertTrue(
            all(item['estimated_cost_usd'] <= 500 for item in installed['panel_recommendations']['recommendations'])
        )
        empty = build_recommendations(
            fixture(), 49, 10000, catalog=[model('small')], budget_includes_installation=False
        )
        self.assertEqual(empty['proposal']['panel_count'], 0)
        self.assertEqual(empty['proposal']['budget_cost_usd'], 0)

    def test_scope_invalidates_comparison_cache_and_switching_back_restores_layout(self):
        catalog = [model('small'), model('large', watts=200, price=150)]
        data = build_recommendations(fixture(), 500, 10000, catalog=catalog)
        original = deepcopy(data['proposal'])
        signature = data['panel_recommendations']['comparison_signature']
        with patch('panel_recommendations.propose_panels', wraps=propose_panels) as planner:
            build_recommendations(
                data, 500, 10000, catalog=catalog, reuse_comparison=True, budget_includes_installation=False
            )
            self.assertEqual(planner.call_count, 2)
        self.assertNotEqual(signature, data['panel_recommendations']['comparison_signature'])
        build_recommendations(data, 500, 10000, catalog=catalog, reuse_comparison=True)
        self.assertEqual(data['proposal']['panels'], original['panels'])
        self.assertTrue(data['proposal']['budget_includes_installation'])

    def test_defaults_and_boolean_validation(self):
        self.assertTrue(validate_recommendation_inputs({})['budget_includes_installation'])
        self.assertFalse(
            validate_recommendation_inputs({'budget_includes_installation': False})['budget_includes_installation']
        )
        for value in ('false', 'true', 0, 1, None, []):
            with self.subTest(value=value), self.assertRaises(InputError):
                validate_recommendation_inputs({'budget_includes_installation': value})
        with self.assertRaises(InputError):
            propose_panels(fixture(), budget_includes_installation=False)

    def test_api_persistence_pdf_and_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            client = app.test_client()
            headers = {'Origin': 'http://localhost'}
            try:
                self.assertIn(b'id="budget-includes-installation"', client.get('/').data)
                self.assertEqual(
                    client.post(
                        '/api/analyze', headers=headers, json={'demo': True, 'budget_includes_installation': 'false'}
                    ).status_code,
                    400,
                )
                response = client.post(
                    '/api/analyze',
                    headers=headers,
                    json={'demo': True, 'budget_usd': 2000, 'budget_includes_installation': False},
                )
                self.assertEqual(response.status_code, 202)
                job = response.get_json()['job_id']
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    state = client.get('/api/jobs/' + job).get_json()
                    if state['status'] != 'running':
                        break
                    time.sleep(0.05)
                self.assertEqual(state['status'], 'complete', state)
                plan = state['result']['proposal']
                self.assertFalse(plan['budget_includes_installation'])
                self.assertLessEqual(plan['budget_cost_usd'], 2000)
                self.assertGreater(plan['estimated_cost_usd'], 2000)
                saved = json.loads((Path(directory) / job / 'results.json').read_text())
                self.assertFalse(saved['panel_recommendations']['budget_includes_installation'])
                self.assertIn('cost extra', saved['report']['findings'])
                restored = create_app(directory)
                try:
                    reopened_plan = restored.test_client().get('/api/jobs/' + job).get_json()['result']['proposal']
                    self.assertFalse(reopened_plan['budget_includes_installation'])
                    self.assertEqual(reopened_plan['budget_cost_usd'], plan['budget_cost_usd'])
                finally:
                    restored.extensions['roofready_executor'].shutdown(wait=True)
                with patch.object(PDFPages, 'text', autospec=True) as text:
                    pdf = client.get('/api/report/' + job)
                    self.assertEqual(pdf.status_code, 200)
                    self.assertTrue(pdf.data.startswith(b'%PDF-'))
                    self.assertTrue(
                        any(
                            'module hardware only; other equipment and installation cost extra' in str(call.args[1])
                            for call in text.call_args_list
                        )
                    )
                    pdf.close()
                changed = client.post(
                    '/api/plan/' + job, headers=headers, json={'budget_usd': 2000, 'budget_includes_installation': True}
                )
                self.assertTrue(changed.get_json()['proposal']['budget_includes_installation'])
                self.assertLessEqual(changed.get_json()['proposal']['estimated_cost_usd'], 2000)
            finally:
                app.extensions['roofready_executor'].shutdown(wait=True)
            reopened = create_app(directory)
            try:
                self.assertTrue(
                    reopened.test_client()
                    .get('/api/jobs/' + job)
                    .get_json()['result']['proposal']['budget_includes_installation']
                )
            finally:
                reopened.extensions['roofready_executor'].shutdown(wait=True)
