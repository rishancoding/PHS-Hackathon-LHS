"""Snapshot persistence, restart restoration, missing files and redacted Atlas errors."""

from copy import deepcopy
from pathlib import Path
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from pymongo.errors import ServerSelectionTimeoutError

from demo_roofready import run_demo
from panel_plan import load_sunlight
from panel_recommendations import build_recommendations
from project_store import MongoProjectStore, ProjectStorageError, settings, snapshot
from roof_details import save_report
from roofready_app import create_app
from solar_address import InputError


class MemoryCursor(list):
    def sort(self, key, direction):
        return MemoryCursor(sorted(self, key=lambda doc: doc[key], reverse=direction < 0))

    def limit(self, count):
        return self[:count]


class MemoryCollection:
    def __init__(self):
        self.documents = {}

    def insert_one(self, document):
        self.documents[document["_id"]] = deepcopy(document)

    def find(self, query, projection):
        return MemoryCursor(
            {k: deepcopy(v) for k, v in doc.items() if projection.get(k) != 0}
            for doc in self.documents.values()
        )

    def find_one(self, query):
        return deepcopy(self.documents.get(query["_id"]))


class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = tempfile.TemporaryDirectory()
        cls.seed_folder = Path(cls.seed.name) / "seed"
        cls.seed_result = run_demo(cls.seed_folder)
        build_recommendations(
            cls.seed_result, budget_usd=12000, budget_includes_installation=False,
            energy_target_kwh_year=12000, space_limit_m2=35, priority="longer_warranty",
            system_derate=.8, blocked_zones=[[20, 20, 80, 80]], excluded_roof_ids=[cls.seed_result["roofs"][0]["properties"]["id"]],
            sunlight=load_sunlight(cls.seed_folder),
        )
        # Pick a non-default model so restoration must preserve the previewed choice.
        alternate = cls.seed_result["panel_recommendations"]["recommendations"][1]["model"]["model_id"]
        build_recommendations(
            cls.seed_result, budget_usd=12000, budget_includes_installation=False,
            energy_target_kwh_year=12000, space_limit_m2=35, priority="longer_warranty",
            system_derate=.8, selected_model_id=alternate, sunlight=load_sunlight(cls.seed_folder),
        )

    @classmethod
    def tearDownClass(cls):
        cls.seed.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.job = "a" * 24
        shutil.copytree(self.seed_folder, self.root / self.job)
        self.result = json.loads(json.dumps(self.seed_result))
        save_report(self.result, self.root / self.job)
        self.collection = MemoryCollection()
        self.store = MongoProjectStore("test-only", collection=self.collection)
        self.origin = {"Origin": "http://localhost"}
        self.app = self.make_app()
        self.client = self.app.test_client()

    def tearDown(self):
        self.directory.cleanup()

    def make_app(self, store=None):
        app = create_app(self.root, project_store=self.store if store is None else store)
        self.addCleanup(app.extensions["roofready_executor"].shutdown, wait=True)
        return app

    def save(self, name="School rooftop"):
        response = self.client.post("/api/projects", headers=self.origin, json={"job_id": self.job, "name": name})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["project"]

    def open(self, project, client=None):
        return (client or self.client).post(f'/api/projects/{project["id"]}/open', headers=self.origin)

    def test_save_list_and_reopen_after_restart_preserve_plan_and_settings(self):
        saved = self.save()
        listing = self.client.get("/api/projects").get_json()
        self.assertTrue(listing["configured"])
        self.assertEqual(listing["projects"][0]["name"], "School rooftop")
        self.assertNotIn("result", listing["projects"][0])
        restarted = self.make_app().test_client()
        with patch("roofready_app.run_pipeline", side_effect=AssertionError("Google pipeline called")):
            response = self.open(saved, restarted)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertNotEqual(data["job_id"], self.job)
        restored = data["result"]
        for field in ("proposal", "blocked_zones", "roof_selection", "panel_recommendations", "impact", "assumptions"):
            self.assertEqual(restored[field], self.result[field], field)
        self.assertTrue(all(restored["project_assets"].values()))
        report = restarted.get(f'/api/report/{data["job_id"]}')
        self.assertEqual(report.status_code, 200)
        report.close()
        image = restarted.get(f'/results/{data["job_id"]}/aerial.jpg')
        self.assertEqual(image.status_code, 200)
        image.close()
        exported = restarted.get(f'/results/{data["job_id"]}/results.json')
        self.assertEqual(exported.get_json()["proposal"], restored["proposal"])
        exported.close()

    def test_replanning_opened_project_does_not_mutate_saved_snapshot(self):
        saved = self.save()
        opened = self.open(saved).get_json()
        response = self.client.post(
            f'/api/plan/{opened["job_id"]}', headers=self.origin,
            json={"budget_usd": 1000, "energy_target_kwh_year": 10000},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["proposal"]["budget_usd"], 1000)
        self.assertEqual(self.store.get(saved["id"])["result"]["proposal"]["budget_usd"], 12000)
        original = json.loads((self.root / self.job / "results.json").read_text())
        self.assertEqual(original["proposal"], self.result["proposal"])

    def test_resaving_opened_project_keeps_original_artifact_reference(self):
        first = self.save()
        opened = self.open(first).get_json()
        response = self.client.post(
            "/api/projects", headers=self.origin, json={"job_id": opened["job_id"], "name": "Second snapshot"},
        )
        second = response.get_json()["project"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.store.get(second["id"])["asset_job_id"], self.job)
        self.assertEqual(self.client.get("/api/projects").get_json()["projects"][0]["id"], second["id"])

    def test_missing_imagery_still_opens_saved_layout_and_blocks_replanning(self):
        saved = self.save()
        shutil.rmtree(self.root / self.job)
        opened = self.open(saved)
        self.assertEqual(opened.status_code, 200)
        data = opened.get_json()
        self.assertFalse(any(data["result"]["project_assets"].values()))
        self.assertEqual(data["result"]["proposal"], self.result["proposal"])
        response = self.client.post(f'/api/plan/{data["job_id"]}', headers=self.origin, json={"budget_usd": 5000})
        self.assertEqual(response.status_code, 409)
        report = self.client.get(f'/api/report/{data["job_id"]}')
        self.assertEqual(report.status_code, 200)
        report.close()

    def test_restart_rechecks_artifacts_for_previously_opened_project(self):
        opened = self.open(self.save()).get_json()
        shutil.rmtree(self.root / self.job)
        state = self.make_app().test_client().get(f'/api/jobs/{opened["job_id"]}').get_json()
        self.assertFalse(any(state["result"]["project_assets"].values()))

    def test_origin_and_input_validation(self):
        self.assertEqual(self.client.post("/api/projects", json={}).status_code, 403)
        for name in (None, [], "", " " * 4, "x" * 81):
            response = self.client.post("/api/projects", headers=self.origin, json={"job_id": self.job, "name": name})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/api/projects", headers=self.origin, json=[]).status_code, 400)
        saved = self.save()
        self.assertEqual(self.client.post(f'/api/projects/{saved["id"]}/open').status_code, 403)
        self.assertEqual(self.client.post('/api/projects/bad-id/open', headers=self.origin).status_code, 404)

    def test_disabled_cloud_storage_does_not_affect_local_plans(self):
        client = self.make_app(False).test_client()
        self.assertFalse(client.get("/api/projects").get_json()["configured"])
        self.assertEqual(client.get(f"/api/jobs/{self.job}").status_code, 200)
        self.assertEqual(client.post("/api/projects", headers=self.origin, json={"job_id": self.job}).status_code, 503)

    def test_atlas_failure_is_redacted_and_keeps_local_plan(self):
        with patch.object(self.collection, "insert_one", side_effect=ServerSelectionTimeoutError("private-connection-credential")):
            response = self.client.post("/api/projects", headers=self.origin, json={"job_id": self.job, "name": "Test"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private-connection-credential", response.get_data(as_text=True))
        self.assertEqual(self.client.get(f"/api/jobs/{self.job}").status_code, 200)
        with patch.object(self.collection, "find", side_effect=ServerSelectionTimeoutError("private-connection-credential")):
            self.assertEqual(self.client.get("/api/projects").status_code, 503)

    def test_credentials_and_download_urls_are_excluded_from_snapshot(self):
        data = deepcopy(self.result)
        data["api_key"] = "private-test-key"
        data["request"]["api_key"] = "private-test-key"
        data["request"]["solar_request_url"] = "https://example.test/?key=private-test-key"
        data["imagery"] = {"rgbUrl": "https://example.test/?token=private-test-key", "imageryQuality": "HIGH"}
        clean = snapshot(data)
        self.assertNotIn("private-test-key", json.dumps(clean))
        self.assertEqual(clean["imagery"], {"imageryQuality": "HIGH"})
        self.assertNotIn("project_assets", clean)

    def test_project_listing_only_exposes_approved_summary_fields(self):
        saved = self.save()
        self.collection.documents[saved["id"]]["api_key"] = "private-test-key"
        listing = self.client.get("/api/projects")
        self.assertEqual(listing.status_code, 200)
        self.assertNotIn("private-test-key", listing.get_data(as_text=True))
        self.assertNotIn("asset_job_id", listing.get_data(as_text=True))

    def test_invalid_file_reference_is_rejected(self):
        for source in ("../.env", "/tmp", "", None):
            with self.assertRaises(InputError):
                self.store.save("Bad reference", self.result, source)
        saved = self.save()
        self.collection.documents[saved["id"]]["asset_job_id"] = "../.env"
        self.assertEqual(self.open(saved).status_code, 400)

    def test_oversize_document_is_rejected_without_database_write(self):
        data = deepcopy(self.result)
        data["notes"] = ["x" * (15 * 1024 * 1024)]
        with self.assertRaises(InputError):
            self.store.save("Oversize", data, self.job)
        self.assertEqual(self.collection.documents, {})

    def test_environment_overrides_local_settings_and_startup_is_lazy(self):
        env = self.root / "test.env"
        env.write_text("MONGODB_URI=local-test-uri\nMONGODB_DATABASE=localdb\n")
        with patch.dict(os.environ, {"MONGODB_URI": "environment-test-uri", "MONGODB_DATABASE": "testdb"}):
            values = settings(env)
        self.assertEqual(values["MONGODB_URI"], "environment-test-uri")
        self.assertEqual(values["MONGODB_DATABASE"], "testdb")
        with patch("project_store.MongoClient", side_effect=AssertionError("Connected at startup")):
            MongoProjectStore("test-only")


if __name__ == "__main__":
    unittest.main()
