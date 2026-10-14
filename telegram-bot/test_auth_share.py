import json
import threading
import unittest
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path
from tempfile import TemporaryDirectory

import server


class AuthShareTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.old = {name: getattr(server, name) for name in ("DATA", "TRIPS", "UPLOADS", "EMAIL_DIR", "EMAIL_INDEX", "AUTH_STORE", "SHARE_STORE", "API_AUTH_TOKEN", "TRIPY_SETUP_TOKEN")}
        server.DATA = root / "events.json"
        server.TRIPS = root / "trips.json"
        server.UPLOADS = root / "uploads"
        server.EMAIL_DIR = root / "email"
        server.EMAIL_INDEX = server.EMAIL_DIR / "index.json"
        server.AUTH_STORE = server.EMAIL_DIR / "auth.json"
        server.SHARE_STORE = server.EMAIL_DIR / "share-tokens.json"
        server.API_AUTH_TOKEN = ""
        server.TRIPY_SETUP_TOKEN = "setup-test-token"
        server.save_trips([])
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for name, value in self.old.items():
            setattr(server, name, value)
        self.tempdir.cleanup()

    def request(self, path, method="GET", payload=None, cookie=""):
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            return urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            return exc

    @staticmethod
    def cookie_header(response, name):
        cookie = SimpleCookie()
        cookie.load(response.headers.get("Set-Cookie", ""))
        return f"{name}={cookie[name].value}"

    def test_setup_hashes_password_and_session_protects_private_api(self):
        response = self.request("/api/trips")
        self.assertEqual(response.status, 401)
        response = self.request("/api/auth/setup", method="POST", payload={"setup_token": "setup-test-token", "username": "owner", "password": "a-long-test-password"})
        self.assertEqual(response.status, 201)
        self.assertNotIn("a-long-test-password", server.AUTH_STORE.read_text())
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", response.headers["Set-Cookie"])
        session = self.cookie_header(response, server.SESSION_COOKIE)
        response = self.request("/api/trips", method="POST", payload={"title": "Scoped trip"}, cookie=session)
        self.assertEqual(response.status, 201)
        self.assertEqual(json.loads(response.read())["trip"]["title"], "Scoped trip")

    def test_share_token_is_one_time_hashed_and_scoped(self):
        self.request("/api/auth/setup", method="POST", payload={"setup_token": "setup-test-token", "username": "owner", "password": "a-long-test-password"})
        login = self.request("/api/auth/login", method="POST", payload={"username": "owner", "password": "a-long-test-password"})
        session = self.cookie_header(login, server.SESSION_COOKIE)
        created = self.request("/api/trips", method="POST", payload={"title": "Private"}, cookie=session)
        trip_id = json.loads(created.read())["trip"]["id"]
        shared = self.request(f"/api/trips/{trip_id}/share", method="POST", cookie=session)
        self.assertEqual(shared.status, 201)
        token = json.loads(shared.read())["token"]
        stored = server.SHARE_STORE.read_text()
        self.assertNotIn(token, stored)
        consumed = self.request(f"/api/share/{token}")
        self.assertEqual(consumed.status, 200)
        self.assertEqual(json.loads(consumed.read())["trip"]["title"], "Private")
        share_cookie = self.cookie_header(consumed, server.SHARE_COOKIE)
        scoped = self.request("/api/share/me", cookie=share_cookie)
        self.assertEqual(scoped.status, 200)
        self.assertEqual(list(json.loads(scoped.read()),), ["trip"])
        self.assertEqual(self.request(f"/api/share/{token}").status, 404)

    @unittest.skipUnless(server.generate_registration_options, "webauthn dependency unavailable")
    def test_passkey_registration_options_are_challenge_bound(self):
        self.request("/api/auth/setup", method="POST", payload={"setup_token": "setup-test-token", "username": "owner", "password": "a-long-test-password"})
        login = self.request("/api/auth/login", method="POST", payload={"username": "owner", "password": "a-long-test-password"})
        session = self.cookie_header(login, server.SESSION_COOKIE)
        response = self.request("/api/auth/passkey/register/options", method="POST", payload={}, cookie=session)
        self.assertEqual(response.status, 200)
        options = json.loads(response.read())
        self.assertIn("challenge", options)
        self.assertIn("rp", options)
        self.assertIn("user", options)
        stored = json.loads(server.AUTH_STORE.read_text())
        self.assertEqual(stored["webauthn_challenges"]["registration"]["challenge"], options["challenge"])

    @unittest.skipUnless(server.verify_authentication_response, "webauthn dependency unavailable")
    def test_passkey_assertion_cannot_be_accepted_without_verification(self):
        self.request("/api/auth/setup", method="POST", payload={"setup_token": "setup-test-token", "username": "owner", "password": "a-long-test-password"})
        response = self.request("/api/auth/passkey/authenticate", method="POST", payload={"credential": {"id": "fake", "response": {}}})
        self.assertIn(response.status, (400, 401))
        self.assertNotIn("Set-Cookie", response.headers)


if __name__ == "__main__":
    unittest.main()
