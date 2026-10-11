"""Optimizations preserve layouts, invalidate stale comparisons, and bound parallel I/O."""

from copy import deepcopy
from io import BytesIO
import math
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from panel_plan import fit_grid, propose_panels
from panel_recommendations import build_recommendations
from process_solar_layers import download_layers, LAYERS
from roof_details import add_roof_addresses
from test_panel_plan import fixture
from test_panel_recommendations import model


class LayoutSpeedTests(unittest.TestCase):
    def test_batched_layout_preserves_original_scan_order_with_holes_and_sparse_roofs(self):
        polygons = [box(0, 0, 20, 20).difference(box(2, 2, 18, 18)), box(0, 0, 40, 1.5).union(box(0, 0, 1.5, 40))]
        for polygon in polygons:
            for width_m, height_m in [(1, 2), (2, 1), (0.5, 0.8)]:
                for resolution in (0.5, 1):
                    minimum_x, minimum_y, maximum_x, maximum_y = polygon.bounds
                    width, height = width_m / resolution, height_m / resolution
                    step_x, step_y = (width_m + 0.2) / resolution, (height_m + 0.2) / resolution
                    rows = max(0, math.floor((maximum_y - minimum_y - height) / step_y) + 1)
                    cols = max(0, math.floor((maximum_x - minimum_x - width) / step_x) + 1)
                    reference = []
                    for index in range(min(rows * cols, 50000)):
                        row, col = divmod(index, cols)
                        x, y = minimum_x + col * step_x, minimum_y + row * step_y
                        panel = box(x, y, x + width, y + height)
                        if polygon.covers(panel):
                            reference.append(panel)
                        if len(reference) == 15:
                            break
                    actual, limited = fit_grid(polygon, width_m, height_m, resolution, 15)
                    self.assertEqual([mapping(p) for p in actual], [mapping(p) for p in reference])
                    self.assertEqual(limited, rows * cols > 50000 and len(reference) < 15)

    def test_comparison_reuse_only_replans_chosen_model(self):
        catalog = [model('first'), model('second', watts=200)]
        result = build_recommendations(fixture(), 5000, 600, catalog=catalog)
        selected = next(
            m['model_id'] for m in catalog if m['model_id'] != result['proposal']['panel_model']['model_id']
        )
        with patch('panel_recommendations.propose_panels', wraps=propose_panels) as planner:
            cached = build_recommendations(
                result, 5000, 600, catalog=catalog, selected_model_id=selected, reuse_comparison=True
            )
            self.assertEqual(planner.call_count, 1)
            build_recommendations(result, 5000, 600, catalog=catalog, selected_model_id=selected, reuse_comparison=True)
            self.assertEqual(planner.call_count, 1)
        fresh = build_recommendations(fixture(), 5000, 600, catalog=catalog, selected_model_id=selected)
        self.assertEqual(cached['proposal'], fresh['proposal'])
        self.assertEqual(cached['panel_recommendations'], fresh['panel_recommendations'])

    def test_changed_constraints_geometry_and_catalog_invalidate_reuse(self):
        catalog = [model('first'), model('second', watts=200)]
        base = build_recommendations(fixture(), 5000, 600, catalog=catalog)
        for change in (
            'budget',
            'target',
            'space',
            'priority',
            'exclusion',
            'geometry',
            'yield',
            'resolution',
            'price',
        ):
            with self.subTest(change=change):
                result = deepcopy(base)
                models = deepcopy(catalog)
                inputs = {
                    'budget_usd': 5000,
                    'energy_target_kwh_year': 600,
                    'catalog': models,
                    'reuse_comparison': True,
                }
                if change == 'budget':
                    inputs['budget_usd'] = 4000
                if change == 'target':
                    inputs['energy_target_kwh_year'] = 700
                if change == 'space':
                    inputs['space_limit_m2'] = 2
                if change == 'priority':
                    inputs['priority'] = 'longer_warranty'
                if change == 'exclusion':
                    inputs['excluded_roof_ids'] = ['roof-1']
                if change == 'geometry':
                    result['roofs'][0]['preview_geometry'] = mapping(box(0, 0, 10, 10))
                if change == 'yield':
                    result['roofs'][0]['properties']['annual_yield_kwh_per_kw'] = 1200
                if change == 'resolution':
                    result['analysis']['resolution_m'] = 0.5
                if change == 'price':
                    models[0]['price_usd'] += 20
                with patch('panel_recommendations.propose_panels', wraps=propose_panels) as planner:
                    build_recommendations(result, **inputs)
                    self.assertEqual(planner.call_count, 2)


class ParallelIOTests(unittest.TestCase):
    def roofs(self):
        return {
            'roofs': [
                {'geometry': mapping(box(index * 3, 0, index * 3 + 2, 2)), 'properties': {'id': f'roof-{index}'}}
                for index in range(8)
            ]
        }

    def test_addresses_use_four_workers_preserve_matches_and_timeout(self):
        barrier = threading.Barrier(4)

        def lookup(url, params, *, timeout):
            self.assertEqual(timeout, 8)
            barrier.wait(timeout=3)
            lat, lng = map(float, params['latlng'].split(','))
            return {
                'status': 'OK',
                'results': [
                    {'formatted_address': params['latlng'], 'geometry': {'location': {'lat': lat, 'lng': lng}}}
                ],
            }

        with patch('roof_details.get_json', side_effect=lookup) as google:
            result = add_roof_addresses(self.roofs(), 'test-key')
            self.assertEqual(google.call_count, 8)
            self.assertTrue(all(r['properties']['address_status'] == 'reverse_geocoded' for r in result['roofs']))
            add_roof_addresses(result, 'test-key')
            self.assertEqual(google.call_count, 8)

    def test_failed_address_service_stops_after_inflight_group(self):
        barrier = threading.Barrier(4)

        def lookup(*args, **kwargs):
            barrier.wait(timeout=3)
            return {'status': 'REQUEST_DENIED'}

        with patch('roof_details.get_json', side_effect=lookup) as google:
            result = add_roof_addresses(self.roofs(), 'test-key')
            self.assertEqual(google.call_count, 4)
            self.assertEqual(len(result['roofs']), 8)
            self.assertTrue(all(r['properties']['address'] == 'Address unavailable' for r in result['roofs']))

    def test_all_four_downloads_overlap_and_remain_valid_tiffs(self):
        with MemoryFile() as memory:
            with memory.open(
                driver='GTiff',
                width=2,
                height=2,
                count=1,
                dtype='uint8',
                crs='EPSG:4326',
                transform=from_origin(0, 2, 1, 1),
            ) as dataset:
                dataset.write(np.ones((1, 2, 2), dtype=np.uint8))
            content = memory.read()
        barrier = threading.Barrier(4)

        class Response(BytesIO):
            def __enter__(self):
                barrier.wait(timeout=3)
                return self

        def download(*args, **kwargs):
            return Response(content)

        data = {field: f'https://solar.googleapis.com/v1/geoTiff:get?id={name}' for name, field in LAYERS.items()}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch('process_solar_layers.urlopen', side_effect=download) as google,
        ):
            paths = download_layers(data, 'test-key', directory)
            self.assertEqual(google.call_count, 4)
            self.assertEqual(set(paths), set(LAYERS))
            self.assertTrue(all(path.read_bytes() == content for path in paths.values()))
            self.assertFalse(list(Path(directory).glob('*.part')))


if __name__ == '__main__':
    unittest.main()
