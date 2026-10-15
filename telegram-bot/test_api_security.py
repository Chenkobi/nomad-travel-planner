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

    def test_email_intake_uses_its_dedicated_secret_boundary(self):
        self.assertFalse(server.api_requires_auth("/api/intake/email"))

    def test_trip_title_patch_persists_custom_title_marker(self):
        server.save_trips([{"id": "trip-title", "title": "Auto title", "start": "2026-10-01", "end": "2026-10-02"}])
        request = urllib.request.Request(
            self.base + "/api/trips/trip-title",
            data=json.dumps({"title": "Family vacation", "title_custom": True}).encode(),
            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
            method="PATCH",
        )
        response = urllib.request.urlopen(request, timeout=3)
        saved = json.loads(response.read())["trip"]
        self.assertEqual(saved["title"], "Family vacation")
        self.assertTrue(saved["title_custom"])

    def test_authorized_sensitive_read_is_allowed(self):
        response = self.request("/api/trips", {"Authorization": "Bearer test-token"})
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read()), {"trips": []})

    def test_frontend_session_cookie_authorizes_same_origin_api(self):
        page = self.request("/")
        self.assertEqual(page.status, 200)
        cookie = page.headers.get("Set-Cookie") or ""
        self.assertIn("tripy_session=", cookie)
        response = self.request("/api/trips", {"Cookie": cookie.split(";", 1)[0]})
        self.assertEqual(response.status, 200)

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

    def test_notifications_endpoint_returns_review_and_processed_items(self):
        server.EMAIL_DIR = Path(self.tempdir.name) / "email"
        server.EMAIL_INDEX = server.EMAIL_DIR / "index.json"
        server.EMAIL_DIR.mkdir()
        server.save_email_index([
            {"id": "review-1", "status": "needs_review", "subject": "Avis", "received_at": "2026-10-11T10:00:00+00:00", "review_reason": "ai_status_missing"},
            {"id": "done-1", "status": "processed", "intent": "confirmed", "subject": "Hotel", "received_at": "2026-10-11T09:00:00+00:00", "processed_at": "2026-10-11T09:01:00+00:00"},
        ])
        response = self.request("/api/notifications", {"Authorization": "Bearer test-token"})
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read())
        self.assertEqual([item["id"] for item in payload["notifications"]], ["review-1", "done-1"])
        self.assertEqual(payload["unread"], 2)

    def test_disallowed_cors_origin_is_not_granted(self):
        response = self.request(
            "/api/trips",
            {"Authorization": "Bearer test-token", "Origin": "https://evil.example"},
        )
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)


if __name__ == "__main__":
    unittest.main()
