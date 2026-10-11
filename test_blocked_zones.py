"""User-drawn no-panel zones: re-plan from saved data, keep panels out, persist with the analysis."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from shapely.geometry import box, shape

from demo_roofready import run_demo
from panel_recommendations import build_recommendations
from roof_details import describe_results
from roofready_app import create_app
from solar_address import InputError


def panel_boxes(result):
    return [shape(panel["preview_geometry"]) for panel in result["proposal"]["panels"]]


class BlockedZoneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.result = run_demo(self.temporary.name)
        build_recommendations(self.result)

    def tearDown(self):
        self.temporary.cleanup()

    def test_panels_rearrange_out_of_a_zone(self):
        before = panel_boxes(self.result)
        self.assertTrue(before)
        left, top, right, bottom = before[0].bounds
        zone = [left - 3, top - 3, right + 20, bottom + 20]
        build_recommendations(self.result, blocked_zones=[zone])
        after = panel_boxes(self.result)
        self.assertTrue(after)
        self.assertFalse(any(panel.intersects(box(*zone)) for panel in after))
        self.assertEqual(self.result["blocked_zones"][0]["bounds"], [round(value, 2) for value in zone])
        self.assertGreater(self.result["blocked_zones"][0]["area_m2"], 0)
        self.assertIn("no-panel zone", describe_results(self.result)["report"]["findings"])

    def test_zone_over_every_roof_leaves_no_panels(self):
        width, height = self.result["analysis"]["raster_width"], self.result["analysis"]["raster_height"]
        build_recommendations(self.result, blocked_zones=[[0, 0, width, height]])
        self.assertEqual(self.result["proposal"]["panel_count"], 0)
        self.assertFalse(self.result["proposal"]["target_met"])

    def test_zones_are_kept_until_cleared_and_clipped_to_the_image(self):
        width = self.result["analysis"]["raster_width"]
        build_recommendations(self.result, blocked_zones=[[-50, 10, width + 50, 20]])
        self.assertEqual(self.result["blocked_zones"][0]["bounds"], [0, 10, width, 20])
        build_recommendations(self.result, budget_usd=9000)  # other updates keep saved zones
        self.assertEqual(len(self.result["blocked_zones"]), 1)
        build_recommendations(self.result, blocked_zones=[])
        self.assertEqual(self.result["blocked_zones"], [])

    def test_invalid_zones_are_rejected(self):
        for zones in ("box", [[1, 2, 3]], [[1, 2, 3, True]], [[1, 2, 3, float("nan")]], [[5, 5, 5, 9]], [[0, 0, 1, 1]] * 201):
            with self.subTest(zones=str(zones)[:40]), self.assertRaises(InputError):
                build_recommendations(self.result, blocked_zones=zones)


class BlockedZoneRouteTests(unittest.TestCase):
    def test_replan_with_zones_saves_and_reloads(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            client = app.test_client()
            origin = {"Origin": "http://localhost"}
            try:
                job = client.post("/api/analyze", headers=origin, json={"demo": True}).get_json()["job_id"]
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and client.get(f"/api/jobs/{job}").get_json()["status"] == "running":
                    time.sleep(.05)
                self.assertEqual(client.post(f"/api/plan/{job}", headers=origin, json={"blocked_zones": [[1, 2, 3]]}).status_code, 400)
                response = client.post(f"/api/plan/{job}", headers=origin, json={"blocked_zones": [[20, 20, 80, 80]]})
                self.assertEqual(response.status_code, 200)
                data = response.get_json()
                self.assertEqual(data["blocked_zones"][0]["bounds"], [20, 20, 80, 80])
                self.assertFalse(any(panel.intersects(box(20, 20, 80, 80)) for panel in panel_boxes(data)))
                saved = json.loads((Path(directory) / job / "results.json").read_text())
                self.assertEqual(saved["blocked_zones"][0]["bounds"], [20, 20, 80, 80])
            finally:
                app.extensions["roofready_executor"].shutdown(wait=True)
            reopened = create_app(directory)
            try:
                result = reopened.test_client().get(f"/api/jobs/{job}").get_json()["result"]
                self.assertEqual(len(result["blocked_zones"]), 1)
            finally:
                reopened.extensions["roofready_executor"].shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
