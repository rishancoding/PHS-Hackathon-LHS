"""Roof filters affect every alternative, persist across replans, and match report maps."""

from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image
from shapely.geometry import box, mapping

from panel_recommendations import build_recommendations, validate_recommendation_inputs
from roofready_app import create_app
from solar_address import InputError
from solar_report import annotated_map, PDFPages, report_models
from test_panel_plan import fixture
from test_panel_recommendations import model


class RoofSelectionTests(unittest.TestCase):
    def area(self):
        data = fixture()
        second = deepcopy(data['roofs'][0])
        second['properties'].update(id='roof-2', annual_yield_kwh_per_kw=2000)
        second['preview_geometry'] = mapping(box(30, 0, 50, 20))
        data['roofs'].append(second)
        return data

    def test_exclusion_changes_all_alternatives_without_removing_candidates(self):
        data = self.area()
        catalog = [model('standard'), model('compact', watts=200)]
        full = deepcopy(build_recommendations(data, 5000, 600, catalog=catalog))
        self.assertTrue(all(p['roof_id'] == 'roof-2' for p in full['proposal']['panels']))
        filtered = build_recommendations(data, 5000, 600, excluded_roof_ids=['roof-2'], catalog=catalog)
        self.assertEqual(len(filtered['roofs']), 2)
        self.assertTrue(all(p['roof_id'] == 'roof-1' for p in filtered['proposal']['panels']))
        full_counts = {
            item['model']['model_id']: item['panel_count'] for item in full['panel_recommendations']['recommendations']
        }
        filtered_counts = {
            item['model']['model_id']: item['panel_count']
            for item in filtered['panel_recommendations']['recommendations']
        }
        self.assertEqual(full_counts, {'standard': 3, 'compact': 2})
        self.assertEqual(filtered_counts, {'standard': 6, 'compact': 3})
        self.assertTrue(
            all(r['available_roof_area_m2'] == 260 for r in filtered['panel_recommendations']['recommendations'])
        )
        reasons = {r['roof_id']: r for r in filtered['proposal']['roof_placement_reasons']}
        self.assertTrue(reasons['roof-2']['excluded'])
        self.assertIn('1,000 kWh', reasons['roof-1']['reason'])
        self.assertIn('not an installation design', filtered['proposal']['placement_explanation'])
        persisted = build_recommendations(data, 5000, 600, selected_model_id='compact', catalog=catalog)
        self.assertEqual(persisted['roof_selection']['excluded_roof_ids'], ['roof-2'])
        self.assertTrue(all(p['roof_id'] == 'roof-1' for p in persisted['proposal']['panels']))
        restored = build_recommendations(data, 5000, 600, excluded_roof_ids=[], catalog=catalog)
        self.assertTrue(all(p['roof_id'] == 'roof-2' for p in restored['proposal']['panels']))

    def test_all_roofs_excluded_is_valid_zero_panel_layout(self):
        result = build_recommendations(
            self.area(),
            5000,
            600,
            excluded_roof_ids=['roof-1', 'roof-2'],
            catalog=[model('standard'), model('compact', watts=200)],
        )
        self.assertEqual(result['proposal']['panels'], [])
        self.assertEqual(result['proposal']['available_roof_area_m2'], 0)
        self.assertEqual(result['proposal']['estimated_cost_usd'], 0)
        self.assertTrue(all(item['tier'] == 3 for item in result['panel_recommendations']['recommendations']))
        self.assertEqual(len(result['roofs']), 2)

    def test_invalid_filters_rejected_and_duplicate_ids_normalized(self):
        for value in ('roof-1', {}, [1], [None], [True]):
            with self.subTest(value=value), self.assertRaises(InputError):
                validate_recommendation_inputs({'excluded_roof_ids': value})
        with self.assertRaises(InputError):
            build_recommendations(self.area(), excluded_roof_ids=['another-search-roof'])
        result = build_recommendations(self.area(), excluded_roof_ids=['roof-1', 'roof-1'], catalog=[model('standard')])
        self.assertEqual(result['roof_selection']['excluded_roof_ids'], ['roof-1'])


class ReportIntegrationTests(unittest.TestCase):
    def test_report_preserves_transparent_outside_search_mask(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Image.new('RGBA', (2, 2), (30, 90, 40, 0))
            image.putpixel((1, 1), (30, 90, 40, 255))
            image.save(Path(directory) / 'aerial.png')
            result = {'analysis': {'raster_width': 2, 'raster_height': 2}, 'roofs': []}
            preview = annotated_map(result, directory)
            self.assertEqual(preview.getpixel((0, 0)), (226, 232, 235))
            self.assertEqual(preview.getpixel((1199, 1199)), (30, 90, 40))

    def test_replan_persistence_and_pdf_match_current_roof_filter_and_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            client = app.test_client()
            origin = {'Origin': 'http://localhost'}
            try:
                response = client.post('/api/analyze', json={'demo': True}, headers=origin)
                job = response.get_json()['job_id']
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    state = client.get(f'/api/jobs/{job}').get_json()
                    if state['status'] != 'running':
                        break
                    time.sleep(0.05)
                self.assertEqual(state['status'], 'complete')
                original = state['result']
                roof_id = original['proposal']['panels'][0]['roof_id']
                response = client.post(f'/api/plan/{job}', json={'excluded_roof_ids': [roof_id]}, headers=origin)
                self.assertEqual(response.status_code, 200)
                result = response.get_json()
                self.assertTrue(all(p['roof_id'] != roof_id for p in result['proposal']['panels']))
                self.assertEqual(len(result['roofs']), 6)
                saved = json.loads((Path(directory) / job / 'results.json').read_text())
                self.assertEqual(saved['roof_selection']['excluded_roof_ids'], [roof_id])
                self.assertIn('1 were excluded by you', saved['report']['findings'])
                ids = [item['model']['model_id'] for item in result['panel_recommendations']['recommendations'][:2]]
                self.assertEqual([r['model']['model_id'] for r in report_models(result, ids)], ids)
                text_lines = []
                original_text = PDFPages.text

                def capture_text(pdf, text, *args, **kwargs):
                    text_lines.append(str(text))
                    return original_text(pdf, text, *args, **kwargs)

                with patch.object(PDFPages, 'text', capture_text):
                    pdf = client.get(f'/api/report/{job}', query_string=[('model', mid) for mid in ids])
                self.assertEqual(pdf.status_code, 200)
                self.assertEqual(pdf.mimetype, 'application/pdf')
                self.assertIn('attachment', pdf.headers['Content-Disposition'])
                self.assertTrue(pdf.data.startswith(b'%PDF-'))
                self.assertGreater(len(re.findall(rb'/Type /Page\b', pdf.data)), 1)
                content = '\n'.join(text_lines)
                for item in result['panel_recommendations']['recommendations'][:2]:
                    self.assertIn(item['model']['model_name'], content)
                self.assertIn('Excluded by you', content)
                self.assertIn('SYNTHETIC DEMO', content)
                self.assertIn('Your panel comparison', content)
                self.assertIn('Why these panel locations?', content)
                self.assertIsNotNone(annotated_map(result, Path(directory) / job))
                self.assertEqual(client.get(f'/api/report/{job}', query_string={'model': 'unknown'}).status_code, 400)
                self.assertEqual(
                    client.get(f'/api/report/{job}', query_string=[('model', ids[0]), ('model', ids[0])]).status_code,
                    400,
                )
                self.assertEqual(client.get('/api/report/missing').status_code, 404)
                unknown = client.post(f'/api/plan/{job}', json={'excluded_roof_ids': ['unknown']}, headers=origin)
                self.assertEqual(unknown.status_code, 400)
                all_ids = [r['properties']['id'] for r in result['roofs']]
                none = client.post(f'/api/plan/{job}', json={'excluded_roof_ids': all_ids}, headers=origin)
                self.assertEqual(none.get_json()['proposal']['panel_count'], 0)
                pdf = client.get(f'/api/report/{job}')
                self.assertEqual(pdf.status_code, 200)
                self.assertTrue(pdf.data.startswith(b'%PDF-'))
            finally:
                app.extensions['roofready_executor'].shutdown(wait=True)


if __name__ == '__main__':
    unittest.main()
