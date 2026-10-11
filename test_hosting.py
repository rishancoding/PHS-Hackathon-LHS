"""Hosted origin and hostname checks, without Google requests."""

import os
import tempfile
import unittest
from unittest.mock import patch

from roofready_app import create_app


class HostingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.folder.cleanup()

    def make_app(self, hostname=""):
        with patch.dict(os.environ, {"RENDER_EXTERNAL_HOSTNAME": hostname}):
            app = create_app(self.folder.name)
        self.addCleanup(app.extensions["roofready_executor"].shutdown, wait=True)
        return app

    def test_local_mode_does_not_accept_public_hosts_or_forwarded_https(self):
        client = self.make_app().test_client()
        self.assertEqual(client.get("/health", base_url="https://evil.example").status_code, 400)
        response = client.post(
            "/api/address-suggestions",
            json={"query": "x"},
            headers={"Origin": "https://localhost", "X-Forwarded-Proto": "https"},
        )
        self.assertEqual(response.status_code, 403)

    def test_render_https_accepts_same_origin_and_rejects_other_origins(self):
        client = self.make_app("photogrid-example.onrender.com").test_client()
        base = "http://photogrid-example.onrender.com"
        headers = {"Origin": "https://photogrid-example.onrender.com", "X-Forwarded-Proto": "https"}
        response = client.post("/api/address-suggestions", base_url=base, headers=headers, json={"query": "x"})
        self.assertEqual(response.status_code, 400)  # Reaches input validation, no Google call.
        for origin in ("https://evil.example", "http://photogrid-example.onrender.com", ""):
            response = client.post(
                "/api/address-suggestions", base_url=base, headers={**headers, "Origin": origin}, json={"query": "x"}
            )
            self.assertEqual(response.status_code, 403)

    def test_forwarded_host_cannot_override_host_allowlist(self):
        client = self.make_app("photogrid-example.onrender.com").test_client()
        response = client.get(
            "/health", base_url="http://evil.example", headers={"X-Forwarded-Host": "photogrid-example.onrender.com"}
        )
        self.assertEqual(response.status_code, 400)

    def test_health_and_branded_route_in_hosted_mode(self):
        client = self.make_app("photogrid-example.onrender.com").test_client()
        base = "https://photogrid-example.onrender.com"
        self.assertEqual(client.get("/health", base_url=base).get_json(), {"status": "ok", "app": "PhotoGrid"})
        page = client.get("/photogrid", base_url=base)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Shared hackathon demo", page.data)
        self.assertIn(b"PhotoGrid", page.data)
        self.assertNotIn(b"RoofReady", page.data)

    @patch("roofready_app.DEFAULT_API_KEY", "")
    def test_hosted_site_hides_key_box_when_server_has_a_google_key(self):
        base = "https://photogrid-example.onrender.com"
        hosted = self.make_app("photogrid-example.onrender.com").test_client()
        with patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": "test-server-key"}):
            page = hosted.get("/photogrid", base_url=base).get_data(as_text=True)
            local = self.make_app().test_client().get("/photogrid").get_data(as_text=True)
        self.assertIn('<details id="google-key" hidden>', page)
        self.assertIn('id="api-key"', page)  # The scripts still read the (empty) input.
        self.assertNotIn("test-server-key", page)
        self.assertRegex(local, r'<details id="google-key"\s*>')  # Local override stays available.
        with patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": ""}):
            page = hosted.get("/photogrid", base_url=base).get_data(as_text=True)
        self.assertIn('<details id="google-key" open>', page)


if __name__ == "__main__":
    unittest.main()
