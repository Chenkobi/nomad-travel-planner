import unittest
from pathlib import Path

import server

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
WORKER = (ROOT / "service-worker.js").read_text(encoding="utf-8")


class CurrencyRatesCacheTests(unittest.TestCase):
    def setUp(self):
        server.RATES_CACHE.update({"fetched_at": 0.0, "payload": None})

    def tearDown(self):
        server.RATES_CACHE.update({"fetched_at": 0.0, "payload": None})

    def test_rates_are_cached_and_last_good_answer_survives_provider_failure(self):
        calls = []

        def good():
            calls.append(1)
            return {"base": "ILS", "date": "2026-10-28", "rates": {"EUR": 3.9}, "source": "Frankfurter"}

        def broken():
            raise OSError("down")

        self.assertEqual(server.currency_rates(now=1000, fetcher=good)["rates"], {"EUR": 3.9})
        server.currency_rates(now=1500, fetcher=good)
        self.assertEqual(len(calls), 1)
        stale = server.currency_rates(now=1000 + server.RATES_TTL_SECONDS + 1, fetcher=broken)
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["rates"], {"EUR": 3.9})

    def test_no_rates_without_any_successful_fetch(self):
        self.assertIsNone(server.currency_rates(now=1, fetcher=lambda: (_ for _ in ()).throw(OSError("down"))))


class CurrencyFrontendTests(unittest.TestCase):
    def test_saved_currency_order_is_rendered_on_load(self):
        self.assertIn("function syncCurrencyRows()", INDEX)
        self.assertIn("syncCurrencyRows();function openCurrencyEditor()", INDEX)

    def test_last_real_rates_are_kept_on_the_device(self):
        self.assertIn("localStorage.setItem('tripy-rates'", INDEX)
        self.assertIn("localStorage.getItem('tripy-rates')", INDEX)
        self.assertNotIn("מוצגים השערים האחרונים", INDEX)

    def test_converter_is_in_hebrew(self):
        for english in ("<h2>Currency</h2>", "Enter Amount", ">Convert<", "'United States Dollar'", "'Georgian Lari'"):
            self.assertNotIn(english, INDEX)


class OfflineShellTests(unittest.TestCase):
    def test_page_registers_service_worker_without_waiting_for_push_opt_in(self):
        self.assertIn("navigator.serviceWorker.register('/service-worker.js',{scope:'/'}).catch(()=>{})", INDEX)

    def test_worker_caches_shell_but_never_api_data(self):
        self.assertIn("caches.open(SHELL_CACHE)", WORKER)
        self.assertIn("url.pathname.startsWith('/api/')) return;", WORKER)
        self.assertIn("addEventListener('push'", WORKER)
        self.assertIn("addEventListener('notificationclick'", WORKER)


class ScheduleDuplicateTests(unittest.TestCase):
    def test_restaurant_events_are_not_shown_twice_next_to_trip_restaurants(self):
        self.assertIn("e[4]==='מסעדה'&&(activeTrip?.restaurants||[]).some(", INDEX)


class ReminderPanelTests(unittest.TestCase):
    def test_notification_panel_lists_upcoming_reminders(self):
        self.assertIn('id="reminderList"', INDEX)
        self.assertIn("API_BASE+'/api/reminders'", INDEX)
        self.assertTrue(server.api_requires_auth("/api/reminders"))


if __name__ == "__main__":
    unittest.main()
