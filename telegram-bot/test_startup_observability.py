import json
import threading
import unittest
from http.client import HTTPConnection
from unittest.mock import patch

import server


class StartupObservabilityTests(unittest.TestCase):
    def test_http_health_and_readiness_do_not_depend_on_telegram(self):
        server.HTTP_READY = True
        server.EMAIL_READY = True
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            for path in ("/healthz", "/readyz"):
                connection = HTTPConnection("127.0.0.1", httpd.server_port, timeout=2)
                connection.request("GET", path)
                response = connection.getresponse()
                payload = json.loads(response.read())
                connection.close()
                self.assertEqual(response.status, 200)
                self.assertIn("status", payload)
            with patch.object(server, "TOKEN", ""), patch.object(server, "api") as api:
                server.start_telegram_polling()
                api.assert_not_called()
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_telegram_startup_retries_delete_webhook_with_a_bound(self):
        with patch.object(server, "TOKEN", "present"), \
             patch.object(server, "TELEGRAM_STARTUP_RETRIES", 3), \
             patch.object(server, "TELEGRAM_RETRY_DELAY_SECONDS", 0), \
             patch.object(server, "api", side_effect=RuntimeError("telegram unavailable")) as api, \
             patch.object(server.time, "sleep") as sleep:
            server.start_telegram_polling()

        self.assertEqual(api.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(server.telegram_status()["status"], "unavailable")
        self.assertNotIn("present", json.dumps(server.telegram_status()))

    def test_healthy_telegram_startup_keeps_delete_webhook_before_polling(self):
        calls = []
        with patch.object(server, "TOKEN", "present"), \
             patch.object(server, "api", side_effect=lambda method, payload=None: calls.append(method) or []), \
             patch.object(server, "poll", side_effect=lambda: calls.append("poll")):
            server.start_telegram_polling()

        self.assertEqual(calls, ["deleteWebhook", "poll"])
        self.assertEqual(server.telegram_status()["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
