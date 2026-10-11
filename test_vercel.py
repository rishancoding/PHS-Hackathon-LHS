"""Exercise gateway boundaries and the real Flask analysis flow offline."""
import io
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from vercel.app import create_app
from roofready_app import create_app as backend_app

ENV = {
    "VERCEL": "1", "VERCEL_URL": "photogrid-preview.vercel.app",
    "VERCEL_PROJECT_PRODUCTION_URL": "photogrid.vercel.app",
    "PHOTOGRID_PUBLIC_URL": "", "PHOTOGRID_BACKEND_URL": "https://photogrid.onrender.com",
}
BASE = "https://photogrid.vercel.app"
ORIGIN = {"Origin": BASE}


class Upstream(io.BytesIO):
    def __init__(self, data, code=200, headers=None):
        super().__init__(data)
        self.code = code
        self.headers = headers or {"Content-Type": "application/json"}


class GatewayTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, ENV):
            self.app = create_app()
        self.client = self.app.test_client()

    def test_setup_and_invalid_backend(self):
        with patch.dict(os.environ, {**ENV, "PHOTOGRID_BACKEND_URL": ""}):
            self.assertEqual(create_app().test_client().get("/", base_url=BASE).status_code, 503)
        for url in ("http://localhost", "https://user:password@example.com", "https://example.com/path", BASE):
            with patch.dict(os.environ, {**ENV, "PHOTOGRID_BACKEND_URL": url}):
                with self.assertRaises(ValueError):
                    create_app()

    def test_origin_and_host_rejected_before_backend(self):
        with patch("urllib.request.OpenerDirector.open") as opened:
            for origin in ("https://evil.example", ""):
                self.assertEqual(self.client.post("/api/analyze", base_url=BASE, headers={"Origin": origin}, json={}).status_code, 403)
            self.assertEqual(self.client.get("/health", base_url="https://evil.example", headers={"X-Forwarded-Host": "photogrid.vercel.app"}).status_code, 400)
            opened.assert_not_called()

    def test_backend_errors_and_timeouts_preserved_safely(self):
        error = HTTPError("https://backend/api", 409, "Conflict", {"Content-Type": "application/json"}, io.BytesIO(b'{"error":"already running"}'))
        with patch("urllib.request.OpenerDirector.open", side_effect=error):
            response = self.client.post("/api/analyze", base_url=BASE, headers=ORIGIN, json={})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.get_json()["error"], "already running")
        with patch("urllib.request.OpenerDirector.open", side_effect=URLError("private server detail")):
            response = self.client.get("/health", base_url=BASE)
            self.assertEqual(response.status_code, 502)
            self.assertNotIn(b"private server detail", response.data)

    def test_large_download_and_response_limit(self):
        with patch("urllib.request.OpenerDirector.open") as opened:
            response = self.client.get("/results/abc/clipped_annual_flux.tif", base_url=BASE)
            self.assertEqual(response.status_code, 307)
            self.assertEqual(response.location, "https://photogrid.onrender.com/results/abc/clipped_annual_flux.tif")
            opened.assert_not_called()
        with patch("urllib.request.OpenerDirector.open", return_value=Upstream(b"x" * 4_000_001)):
            self.assertEqual(self.client.get("/results/abc/results.json", base_url=BASE).status_code, 502)

    def test_gateway_real_backend_demo_poll_preview_and_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"RENDER_EXTERNAL_HOSTNAME": "photogrid.onrender.com"}):
                backend = backend_app(folder, project_store=False)
            backend_client = backend.test_client()
            requests = []

            def forward(upstream, timeout):
                requests.append(upstream)
                parsed = urlsplit(upstream.full_url)
                path = parsed.path + ("?" + parsed.query if parsed.query else "")
                response = backend_client.open(path, method=upstream.method, base_url="https://photogrid.onrender.com", headers=dict(upstream.header_items()), data=upstream.data)
                payload = Upstream(response.data, response.status_code, dict(response.headers))
                response.close()
                return payload

            try:
                with patch("urllib.request.OpenerDirector.open", side_effect=forward):
                    self.assertIn(b"PhotoGrid", self.client.get("/photogrid", base_url=BASE).data)
                    start = self.client.post("/api/analyze", base_url=BASE, headers=ORIGIN, json={"demo": True})
                    self.assertEqual(start.status_code, 202)
                    self.assertEqual(requests[-1].get_header("Origin"), "https://photogrid.onrender.com")
                    job_id = start.get_json()["job_id"]
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        job = self.client.get(f"/api/jobs/{job_id}", base_url=BASE).get_json()
                        if job["status"] != "running":
                            break
                        time.sleep(0.05)
                    self.assertEqual(job["status"], "complete")
                    image = self.client.get(f"/results/{job_id}/aerial.jpg", base_url=BASE)
                    self.assertEqual(image.status_code, 200)
                    self.assertEqual(image.mimetype, "image/jpeg")
                    self.assertEqual(image.headers["Cache-Control"], "no-store")
                    # Validate PDF content and its download headers through the gateway.
                    pdf = self.client.get(f"/api/report/{job_id}", base_url=BASE)
                    self.assertEqual(pdf.status_code, 200)
                    self.assertTrue(pdf.data.startswith(b"%PDF"))
                    self.assertIn("attachment", pdf.headers["Content-Disposition"])
            finally:
                backend.extensions["roofready_executor"].shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
