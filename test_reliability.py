"""Demo reliability: saved-imagery reuse, MEDIUM fallback, saved-search ordering, error logging."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from demo_roofready import create_demo
from process_solar_layers import find_saved_layers, run_pipeline
from roofready_app import create_app
from solar_address import InputError


def saved_run(root, name, request, mtime=None):
    """A saved run folder with the four demo layers and a results.json for `request`."""
    folder = Path(root) / name
    _, paths = create_demo(folder / "layers")
    for layer in paths.values():
        layer.rename(folder / "layers" / layer.name)
    result = {"request": request, "roofs": [], "summary": {}, "analysis": {"flux_coverage_percent": 100},
              "imagery": {"imageryQuality": "HIGH", "imageryDate": {"year": 2024, "month": 5, "day": 1}}}
    (folder / "results.json").write_text(json.dumps(result))
    if mtime:
        os.utime(folder / "results.json", (mtime, mtime))
    return folder


class ImageryReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.request, _ = create_demo(self.root / "scratch")
        self.request["solar_parameters"]["pixelSizeMeters"] = 1

    def tearDown(self):
        self.temporary.cleanup()

    def test_repeat_search_reuses_saved_layers_without_solar_requests(self):
        saved_run(self.root, "earlier", self.request)
        with patch("process_solar_layers.prepare_request", return_value=json.loads(json.dumps(self.request))), \
                patch("process_solar_layers.get_json", side_effect=AssertionError("Solar API called")) as solar, \
                patch("process_solar_layers.download_layers", side_effect=AssertionError("download")):
            result = run_pipeline("123 Test St", 100, "meters", "key", self.root / "new")
        solar.assert_not_called()
        self.assertIn("reused_from", result["imagery"])
        self.assertEqual(result["summary"]["candidate_roofs"], 6)
        self.assertTrue((self.root / "new" / "layers" / "rgb.tif").is_file())

    def test_reuse_can_be_turned_off_and_ignores_other_locations(self):
        other = json.loads(json.dumps(self.request)); other["latitude"] += .01
        saved_run(self.root, "elsewhere", other)
        self.assertIsNone(find_saved_layers(self.root, self.request))
        saved_run(self.root, "same", self.request)
        self.assertIsNotNone(find_saved_layers(self.root, self.request))
        with patch("process_solar_layers.prepare_request", return_value=json.loads(json.dumps(self.request))), \
                patch("process_solar_layers.get_json", side_effect=InputError("network")) as solar:
            with self.assertRaises(InputError):
                run_pipeline("123 Test St", 100, "meters", "key", self.root / "fresh", reuse_imagery=False)
        solar.assert_called_once()

    def test_falls_back_to_medium_quality_when_high_is_unavailable(self):
        _, paths = create_demo(self.root / "fallback-layers")
        data = {"imageryQuality": "MEDIUM"}
        calls = []
        def solar(url, params, **kwargs):
            calls.append(params["requiredQuality"])
            if params["requiredQuality"] == "HIGH":
                raise InputError("Google API HTTP 404: Requested entity was not found.")
            return data
        with patch("process_solar_layers.prepare_request", return_value=json.loads(json.dumps(self.request))), \
                patch("process_solar_layers.get_json", side_effect=solar), \
                patch("process_solar_layers.download_layers", return_value=paths):
            result = run_pipeline("123 Test St", 100, "meters", "key", self.root / "medium", reuse_imagery=False)
        self.assertEqual(calls, ["HIGH", "MEDIUM"])
        self.assertEqual(result["imagery"]["imageryQuality"], "MEDIUM")


class AppReliabilityTests(unittest.TestCase):
    def test_saved_searches_are_newest_first_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            request, _ = create_demo(Path(directory) / "scratch")
            request["address"] = "1 Main St, Boston, MA 02110, USA"
            now = time.time()
            saved_run(directory, "older", request, now - 600)
            saved_run(directory, "newer", request, now - 60)
            other = {**request, "address": "9 Elm St, Austin, TX 78701, USA"}
            saved_run(directory, "oldest-other", other, now - 3600)
            app = create_app(directory)
            try:
                runs = app.test_client().get("/api/history").get_json()
            finally:
                app.extensions["roofready_executor"].shutdown(wait=True)
        self.assertEqual([run["job_id"] for run in runs], ["newer", "oldest-other"])
        self.assertTrue(all(run["completed_at"] and run["radius_meters"] for run in runs))

    def test_unexpected_errors_are_logged_without_the_key(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory)
            client = app.test_client()
            try:
                with patch("roofready_app.run_pipeline", side_effect=RuntimeError("boom secret-test-key")), \
                        patch.object(app.logger, "error") as log:
                    job = client.post("/api/analyze", headers={"Origin": "http://localhost"}, json={
                        "address": "1 Main St", "radius": 50, "unit": "meters", "api_key": "secret-test-key"}).get_json()["job_id"]
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and client.get(f"/api/jobs/{job}").get_json()["status"] == "running":
                        time.sleep(.05)
                    state = client.get(f"/api/jobs/{job}").get_json()
            finally:
                app.extensions["roofready_executor"].shutdown(wait=True)
        self.assertEqual(state["status"], "failed")
        self.assertNotIn("secret-test-key", state["message"])
        logged = " ".join(str(argument) for argument in log.call_args.args)
        self.assertIn("RuntimeError", logged)
        self.assertNotIn("secret-test-key", logged)


if __name__ == "__main__":
    unittest.main()
