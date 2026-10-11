"""Integration tests exercise real GeoTIFF reprojection, scoring, and dashboard jobs."""

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import shape

from demo_roofready import create_demo, run_demo
from process_solar_layers import aligned_band, download_layers, metric_grid, process_layers, run_pipeline
from roofready_app import create_app
from solar_address import InputError


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_full_pipeline_geometry_scoring_and_exports(self):
        result = run_demo(self.directory)
        self.assertTrue(result["demo"])
        self.assertEqual(result["summary"]["candidate_roofs"], 6)
        scores = [roof["properties"]["estimated_energy_kwh_year"] for roof in result["roofs"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for roof in result["roofs"]:
            props = roof["properties"]
            self.assertAlmostEqual(props["estimated_energy_kwh_year"],
                                   props["area_m2"] * .65 * .2 * props["annual_yield_kwh_per_kw"], delta=1)
            self.assertTrue(shape(roof["geometry"]).is_valid)
            self.assertLess(abs(props["longitude"] + 122.084), .002)
        for name in ("aerial.png", "sunlight.png", "roofs.csv", "roofs.geojson", "results.json", "clipped_dsm.tif"):
            self.assertTrue((self.directory / name).is_file())
        geojson = json.loads((self.directory / "roofs.geojson").read_text())
        self.assertEqual(len(geojson["features"]), 6)
        self.assertNotIn("preview_geometry", geojson["features"][0])

    def test_live_pipeline_leaves_roof_addresses_for_separate_lookup(self):
        request, paths = create_demo(self.directory / "layers")
        with patch("process_solar_layers.prepare_request", return_value=request), \
                patch("process_solar_layers.get_json", return_value={"imageryDate": {}}), \
                patch("process_solar_layers.download_layers", return_value=paths), \
                patch("roof_details.get_json") as reverse_geocode:
            result = run_pipeline("123 Test St", 100, "meters", "test-key", self.directory)
        reverse_geocode.assert_not_called()
        self.assertEqual(result["summary"]["candidate_roofs"], 6)
        self.assertTrue(all(r["properties"]["address_status"] == "not_looked_up" for r in result["roofs"]))
        self.assertIn("candidate", result["report"]["findings"])

    def test_nodata_excluded_and_boundary_flagged(self):
        request, paths = create_demo(self.directory / "layers")
        request["solar_parameters"]["pixelSizeMeters"] = 1
        with rasterio.open(paths["annual_flux"], "r+") as dataset:
            data = dataset.read(1)
            data[120:152, 30:60] = -9999
            dataset.write(data, 1)
        # A roof spanning the search edge should contribute only its inner portion.
        with rasterio.open(paths["mask"], "r+") as dataset:
            data = dataset.read(1)
            data[85:115, 0:15] = 1
            dataset.write(data, 1)
        result = process_layers(paths, request, self.directory)
        missing = [roof for roof in result["roofs"] if roof["properties"]["estimated_energy_kwh_year"] is None]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["properties"]["flux_coverage_percent"], 0)
        self.assertIsNone(missing[0]["properties"]["estimated_capacity_kw"])
        self.assertTrue(any(roof["properties"]["partial_roof"] for roof in result["roofs"]))
        json.dumps(result, allow_nan=False)

    def test_no_roofs_is_valid_result(self):
        request, paths = create_demo(self.directory / "layers")
        request["solar_parameters"]["pixelSizeMeters"] = 1
        with rasterio.open(paths["mask"], "r+") as dataset:
            dataset.write(np.zeros((dataset.height, dataset.width), np.uint8), 1)
        result = process_layers(paths, request, self.directory)
        self.assertEqual(result["summary"]["candidate_roofs"], 0)
        self.assertEqual(result["summary"]["estimated_energy_kwh_year"], 0)

    def test_coarser_source_aligned_to_metric_grid(self):
        grid = metric_grid(37.422, -122.084, 100, 1)
        source = self.directory / "coarse.tif"
        with rasterio.open(source, "w", driver="GTiff", height=100, width=100, count=1,
                           dtype="float32", crs=grid["crs"], nodata=-9999,
                           transform=from_origin(grid["transform"].c, grid["transform"].f, 2, 2)) as dataset:
            dataset.write(np.full((100, 100), 1500, dtype=np.float32), 1)
        aligned = aligned_band(source, 1, grid)
        self.assertEqual(aligned.shape, (200, 200))
        self.assertTrue(np.allclose(aligned, 1500))

    @patch("process_solar_layers.urlopen")
    def test_foreign_download_host_rejected(self, open_url):
        with self.assertRaises(InputError):
            download_layers({"rgbUrl": "https://example.com/file.tif"}, "key", self.directory)
        open_url.assert_not_called()


class DashboardTests(unittest.TestCase):
    def test_page_validation_demo_job_and_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            client = app.test_client()
            try:
                page = client.get("/")
                self.assertEqual(page.status_code, 200)
                self.assertIn(b'id="top-panels"', page.data)
                self.assertIn(b'id="panel-priority"', page.data)
                asset = client.get("/static/app.js")
                self.assertEqual(asset.status_code, 200)
                asset.close()
                self.assertEqual(client.post("/api/analyze", json={"demo": True}).status_code, 403)
                origin = {"Origin": "http://localhost"}
                for field in ("budget_usd", "energy_target_kwh_year"):
                    invalid = client.post("/api/analyze", headers=origin, json={"demo": True, field: -1})
                    self.assertEqual(invalid.status_code, 400)
                response = client.post("/api/analyze", headers=origin,
                                       json={"address": "test", "radius": 1001, "unit": "meters", "api_key": "test-key"})
                self.assertEqual(response.status_code, 400)
                response = client.post("/api/analyze", headers=origin, json={"demo": True})
                self.assertEqual(response.status_code, 202)
                job = response.get_json()["job_id"]
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    data = client.get(f"/api/jobs/{job}").get_json()
                    if data["status"] != "running":
                        break
                    time.sleep(.05)
                self.assertEqual(data["status"], "complete", data)
                self.assertEqual(data["result"]["summary"]["candidate_roofs"], 6)
                self.assertIsInstance(data["result"]["search_seconds"], float)
                self.assertGreaterEqual(data["result"]["search_seconds"], 0)
                # Street addresses are a separate step after the analysis.
                self.assertTrue(all(r["properties"]["address_status"] == "not_looked_up"
                                    for r in data["result"]["roofs"]))
                lookup = client.post(f"/api/addresses/{job}", headers=origin, json={})
                self.assertEqual(lookup.status_code, 202)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    data = client.get(f"/api/jobs/{job}").get_json()
                    if data["status"] != "running":
                        break
                    time.sleep(.05)
                self.assertEqual(data["status"], "complete", data)
                self.assertIn("synthetic", data["result"]["roofs"][0]["properties"]["address"])
                self.assertIn("findings", data["result"]["report"])
                self.assertGreater(data["result"]["proposal"]["panel_count"], 0)
                replanned = client.post(f"/api/plan/{job}", headers=origin, json={
                    "budget_usd": 2000, "energy_target_kwh_year": 10000, "installed_cost_per_watt": 3})
                self.assertEqual(replanned.status_code, 200)
                proposal = replanned.get_json()["proposal"]
                self.assertGreater(proposal["panel_count"], 0)
                self.assertIsNotNone(proposal["panel_model"])
                self.assertEqual(len(replanned.get_json()["panel_recommendations"]["recommendations"]), 15)
                self.assertLessEqual(proposal["estimated_cost_usd"], 2000)
                self.assertFalse(proposal["target_met"])
                saved = json.loads((Path(directory) / job / "results.json").read_text())
                self.assertEqual(saved["proposal"]["budget_usd"], 2000)
                self.assertIn("shortfall", saved["report"]["findings"])
                alternatives = saved["panel_recommendations"]["recommendations"]
                chosen = next(item for item in alternatives if item["panel_count"] > 0
                              and item["model"]["model_id"] != proposal["panel_model"]["model_id"])
                changed = client.post(f"/api/plan/{job}", headers=origin, json={
                    "budget_usd": 2000, "energy_target_kwh_year": 10000,
                    "space_limit_m2": 10, "priority": "longer_warranty", "selected_model_id": chosen["model"]["model_id"]})
                self.assertEqual(changed.status_code, 200)
                result = changed.get_json()
                self.assertEqual(result["proposal"]["panel_model"]["model_id"], chosen["model"]["model_id"])
                self.assertLessEqual(result["proposal"]["panel_area_used_m2"], 10)
                model = result["proposal"]["panel_model"]
                resolution = result["analysis"]["resolution_m"]
                for panel in result["proposal"]["panels"]:
                    x0, y0, x1, y1 = shape(panel["preview_geometry"]).bounds
                    for actual, expected in zip(sorted(((x1-x0)*resolution, (y1-y0)*resolution)),
                                                sorted((model["width_m"], model["height_m"]))):
                        self.assertAlmostEqual(actual, expected)
                self.assertTrue(all(panel["model_id"] == chosen["model"]["model_id"] for panel in result["proposal"]["panels"]))
                invalid = client.post(f"/api/plan/{job}", headers=origin, json={
                    "budget_usd": 2000, "energy_target_kwh_year": 10000, "selected_model_id": "goalzero-13007"})
                self.assertEqual(invalid.status_code, 400)
                invalid = client.post(f"/api/plan/{job}", headers=origin, json={"space_limit_m2": -1})
                self.assertEqual(invalid.status_code, 400)
                persisted = json.loads((Path(directory) / job / "results.json").read_text())
                self.assertEqual(persisted["panel_recommendations"]["selected_model_id"], chosen["model"]["model_id"])
                for filename in ("aerial.png", "sunlight.png", "roofs.csv", "roofs.geojson", "summary.txt"):
                    response = client.get(f"/results/{job}/{filename}")
                    self.assertEqual(response.status_code, 200)
                    response.close()
                self.assertEqual(client.get(f"/results/{job}/layers").status_code, 404)
            finally:
                app.extensions["roofready_executor"].shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
