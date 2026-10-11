"""Behavior tests for catalog comparisons, constraint tiers and chosen model layouts."""

import json
import tempfile
from pathlib import Path
import unittest

from shapely.geometry import box, mapping, shape

from panel_recommendations import build_recommendations, load_catalog, validate_recommendation_inputs
from solar_address import InputError
from test_panel_plan import fixture


def model(mid, watts=100, price=50, width=1, height=1, warranty=2, kind='rigid'):
    return {
        'model_id': mid,
        'model_name': mid,
        'manufacturer': 'Test',
        'width_m': width,
        'height_m': height,
        'rated_power_w': watts,
        'price_usd': price,
        'product_warranty_years': warranty,
        'performance_warranty_years': 25,
        'temperature_coefficient_pct_per_c': None,
        'panel_type': kind,
        'area_m2': width * height,
        'price_per_watt_usd': price / watts,
        'power_density_w_per_m2': watts / (width * height),
    }


class RecommendationTests(unittest.TestCase):
    def test_catalog_ignores_stock_and_keeps_unknown_specs(self):
        models = load_catalog()
        self.assertEqual(len(models), 24)
        self.assertTrue(all(m['ignore_stock'] == 'true' for m in models))
        rich = next(m for m in models if m['model_id'] == 'richsolar-rs-m200')
        self.assertIsNone(rich['product_warranty_years'])
        result = build_recommendations(fixture(), 5000, 1000)
        # 24 models: 14 rigid rooftop modules compared; 5 portable/flexible and 5 under 100 W excluded.
        self.assertEqual(len(result['panel_recommendations']['recommendations']), 14)
        self.assertEqual(len(result['panel_recommendations']['excluded_models']), 10)
        self.assertTrue(
            any('Under 100 W' in item['reason'] for item in result['panel_recommendations']['excluded_models'])
        )
        residential = [
            m
            for m in models
            if m['model_id']
            in {
                'qcells-q-tron-blk-m-g2-plus-425',
                'rec-alpha-pure-rx-460',
                'aptos-dna-108-mfn10-440w',
                'silfab-sil-430-qd',
            }
        ]
        self.assertEqual(len(residential), 4)
        self.assertTrue(
            all(
                m['specifications_source_url'].startswith('https://') and m['price_source_url'].startswith('https://')
                for m in residential
            )
        )
        json.dumps(result, allow_nan=False)

    def test_impact_estimates_use_state_prices_and_stated_factors(self):
        from impact import estimate_impact

        impact = estimate_impact({"estimated_energy_kwh_year": 10000, "estimated_cost_usd": 26000}, "CA")
        self.assertEqual(impact["annual_bill_savings_usd"], 3254.0)
        self.assertEqual(impact["simple_payback_years"], 8.0)
        self.assertAlmostEqual(impact["co2_avoided_tonnes_per_year"], 6.23, places=2)
        self.assertAlmostEqual(impact["homes_powered"], 0.96, places=2)
        self.assertEqual(estimate_impact({"estimated_energy_kwh_year": 0}, "ZZ")["price_region"], "US average")
        self.assertIsNone(estimate_impact({"estimated_energy_kwh_year": 0}, "CA")["simple_payback_years"])

    def test_priority_changes_recommendation_and_map_dimensions(self):
        catalog = [model('value'), model('compact', watts=200, price=100, warranty=10)]
        balanced = build_recommendations(fixture(), 2000, 300, catalog=catalog)
        self.assertEqual(balanced['proposal']['panel_model']['model_id'], 'value')
        self.assertEqual(balanced['proposal']['panel_count'], 3)
        compact = build_recommendations(fixture(), 2000, 300, priority='space_efficiency', catalog=catalog)
        self.assertEqual(compact['proposal']['panel_model']['model_id'], 'compact')
        self.assertEqual(compact['proposal']['panel_count'], 2)
        warranty = build_recommendations(fixture(), 2000, 300, priority='longer_warranty', catalog=catalog)
        self.assertEqual(warranty['proposal']['panel_model']['model_id'], 'compact')
        self.assertTrue(
            any(
                '10-year product warranty' in r
                for r in warranty['panel_recommendations']['recommendations'][0]['reasons']
            )
        )

    def test_changing_models_scales_physical_dimensions_at_each_image_resolution(self):
        catalog = [model('small', width=0.5, height=1), model('large', watts=200, width=1.25, height=2.1)]
        for resolution in (0.25, 0.5, 2):
            data = fixture()
            data['analysis']['resolution_m'] = resolution
            data['roofs'][0]['properties']['area_m2'] = 400 * resolution**2
            layouts = []
            for chosen in catalog:
                with self.subTest(resolution=resolution, model=chosen['model_id']):
                    result = build_recommendations(
                        data, 10000, 1000, selected_model_id=chosen['model_id'], catalog=catalog
                    )
                    plan = result['proposal']
                    self.assertGreater(plan['panel_count'], 0)
                    self.assertEqual(plan['panel_model']['model_id'], chosen['model_id'])
                    expected = sorted((chosen['width_m'], chosen['height_m']))
                    for panel in plan['panels']:
                        polygon = shape(panel['preview_geometry'])
                        left, top, right, bottom = polygon.bounds
                        physical = sorted(((right - left) * resolution, (bottom - top) * resolution))
                        for actual, dimension in zip(physical, expected):
                            self.assertAlmostEqual(actual, dimension)
                        self.assertAlmostEqual(panel['width_m'], (right - left) * resolution)
                        self.assertAlmostEqual(panel['height_m'], (bottom - top) * resolution)
                        self.assertAlmostEqual(polygon.area * resolution**2, chosen['width_m'] * chosen['height_m'])
                        self.assertEqual(panel['model_id'], chosen['model_id'])
                        self.assertTrue(shape(data['roofs'][0]['preview_geometry']).covers(polygon))
                    layouts.append(plan['panels'][0]['preview_geometry'])
            self.assertNotEqual(layouts[0], layouts[1])

    def test_tiers_and_constraints_override_warranty_preference(self):
        catalog = [model('affordable'), model('premium', watts=200, price=100, warranty=10)]
        result = build_recommendations(fixture(), 1000, 300, priority='longer_warranty', catalog=catalog)
        items = result['panel_recommendations']['recommendations']
        self.assertEqual([(r['model']['model_id'], r['tier']) for r in items], [('affordable', 1), ('premium', 2)])
        self.assertTrue(all(r['estimated_cost_usd'] <= 1000 for r in items))
        no_budget = build_recommendations(fixture(), 1, 300, catalog=catalog)
        self.assertTrue(all(r['tier'] == 3 for r in no_budget['panel_recommendations']['recommendations']))

    def test_space_cap_and_selected_alternative_are_applied_to_map(self):
        catalog = [model('value'), model('compact', watts=200, price=100)]
        result = build_recommendations(
            fixture(), 5000, 500, space_limit_m2=2, selected_model_id='value', catalog=catalog
        )
        plan = result['proposal']
        self.assertEqual(plan['panel_model']['model_id'], 'value')
        self.assertEqual(plan['panel_count'], 2)
        self.assertEqual(plan['panel_area_used_m2'], 2)
        self.assertEqual(plan['estimated_energy_kwh_year'], 200)
        polygons = [shape(panel['preview_geometry']) for panel in plan['panels']]
        for index, polygon in enumerate(polygons):
            self.assertAlmostEqual(polygon.area, 1)
            self.assertTrue(shape(result['roofs'][0]['preview_geometry']).covers(polygon))
            for previous in polygons[:index]:
                self.assertEqual(polygon.intersection(previous).area, 0)

    def test_rotation_and_holes_affect_fit_and_no_yield_is_excluded(self):
        data = fixture()
        data['assumptions']['usable_roof_fraction'] = 1
        data['roofs'][0]['preview_geometry'] = mapping(box(0, 0, 2.1, 1.1))
        data['roofs'][0]['properties']['area_m2'] = 2.31
        result = build_recommendations(data, 5000, 50, catalog=[model('rotated', width=1, height=2)])
        self.assertEqual(result['proposal']['panel_count'], 1)
        self.assertTrue(
            shape(data['roofs'][0]['preview_geometry']).covers(
                shape(result['proposal']['panels'][0]['preview_geometry'])
            )
        )
        data['roofs'][0]['properties']['annual_yield_kwh_per_kw'] = None
        result = build_recommendations(data, 5000, 50, catalog=[model('rotated', width=1, height=2)])
        self.assertEqual(result['proposal']['panel_count'], 0)
        self.assertEqual(result['panel_recommendations']['recommendations'][0]['tier'], 3)

    def test_hardware_cost_and_geographic_installation_are_not_double_counted(self):
        result = build_recommendations(fixture(), 5000, 100, catalog=[model('value')])
        plan = result['proposal']
        self.assertEqual(plan['estimated_cost_usd'], 330)
        self.assertEqual(plan['estimated_panel_hardware_cost_usd'], 50)
        self.assertEqual(plan['estimated_other_equipment_installation_cost_usd'], 280)
        self.assertEqual(plan['estimated_cost_range_usd'], [247.5, 412.5])

    def test_bad_inputs_and_ineligible_selection_rejected(self):
        for value in (0, -1, 'bad', float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(InputError):
                validate_recommendation_inputs({'space_limit_m2': value})
        with self.assertRaises(InputError):
            validate_recommendation_inputs({'priority': []})
        with self.assertRaises(InputError):
            build_recommendations(fixture(), selected_model_id='missing')
        with self.assertRaises(InputError):
            build_recommendations(fixture(), selected_model_id='goalzero-13007')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.csv'
            path.write_text('model_id,width_m\nbroken,nan\n')
            with self.assertRaises(InputError):
                load_catalog(path)


if __name__ == '__main__':
    unittest.main()
