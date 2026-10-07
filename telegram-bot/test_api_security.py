import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

import server


class ApiSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.old = {
            "data": server.DATA,
            "trips": server.TRIPS,
            "uploads": server.UPLOADS,
            "token": getattr(server, "API_AUTH_TOKEN", None),
            "origin": getattr(server, "API_ALLOWED_ORIGIN", None),
        }
        server.DATA = root / "events.json"
        server.TRIPS = root / "trips.json"
        server.UPLOADS = root / "uploads"
        server.API_AUTH_TOKEN = "test-token"
        server.API_ALLOWED_ORIGIN = "https://app.example.test"
        server.save_trips([])
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.DATA = self.old["data"]
        server.TRIPS = self.old["trips"]
        server.UPLOADS = self.old["uploads"]
        server.API_AUTH_TOKEN = self.old["token"]
        server.API_ALLOWED_ORIGIN = self.old["origin"]
        self.tempdir.cleanup()

    def request(self, path, headers=None, method="GET"):
        request = urllib.request.Request(self.base + path, headers=headers or {}, method=method)
        try:
            return urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            return exc

    def test_unauthorized_sensitive_read_is_rejected(self):
        response = self.request("/api/trips")
        self.assertEqual(response.status, 401)
        self.assertEqual(json.loads(response.read()), {"error": "unauthorized"})

    def test_authorized_sensitive_read_is_allowed(self):
        response = self.request("/api/trips", {"Authorization": "Bearer test-token"})
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read()), {"trips": []})

    def test_local_development_fallback_allows_api_without_token(self):
        server.API_AUTH_TOKEN = ""
        response = self.request("/api/trips")
        self.assertEqual(response.status, 200)

    def test_configured_cors_is_allowlisted_and_supports_auth_header(self):
        response = self.request(
            "/api/trips",
            {"Authorization": "Bearer test-token", "Origin": "https://app.example.test"},
        )
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "https://app.example.test")
        self.assertEqual(response.headers.get("Access-Control-Allow-Credentials"), "true")
        self.assertNotEqual(response.headers.get("Access-Control-Allow-Origin"), "*")

        options = self.request(
            "/api/trips",
            {
                "Origin": "https://app.example.test",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
            method="OPTIONS",
        )
        self.assertEqual(options.status, 204)
        self.assertIn("Authorization", options.headers.get("Access-Control-Allow-Headers", ""))

    def test_disallowed_cors_origin_is_not_granted(self):
        response = self.request(
            "/api/trips",
            {"Authorization": "Bearer test-token", "Origin": "https://evil.example"},
        )
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)


if __name__ == "__main__":
    unittest.main()
