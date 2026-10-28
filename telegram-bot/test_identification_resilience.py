"""Booking identification must not depend on the AI step being available."""
import tempfile
from datetime import timedelta
import unittest
from email import message_from_bytes, policy
from pathlib import Path
from unittest.mock import patch

import server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]


def run_email(raw, *, gemini=None, pushes=None, extra=None):
    with tempfile.TemporaryDirectory() as directory:
        d = Path(directory)
        (d / "up").mkdir()
        patches = [
            patch.object(server, "EMAIL_DIR", d / "email"), patch.object(server, "EMAIL_INDEX", d / "email" / "index.json"),
            patch.object(server, "UPLOADS", d / "up"), patch.object(server, "DATA", d / "trip-data.json"),
            patch.object(server, "TRIPS", d / "trips.json"), patch.object(server, "NOTIFICATION_STATE", d / "email" / "ns.json"),
            patch.object(server, "PUSH_SUBSCRIPTIONS", d / "ps.json"),
            patch.object(server, "send_push_notification", lambda title, body, tag="": (pushes.append(title) if pushes is not None else None) or 1),
            patch.object(server, "gemini_extract", gemini or (lambda *a, **k: server._GEMINI_STATE.__setattr__("error", "gemini_http_429"))),
        ]
        for item in patches:
            item.start()
        try:
            record = server.store_incoming_email(raw)
            record = record[0] if isinstance(record, tuple) else record
            server.process_incoming_email(record)
            if extra:
                extra()
            return server.load_email_index()[0], server.load_trips(), server.load_notifications()
        finally:
            for item in reversed(patches):
                item.stop()


class HtmlTableTextTests(unittest.TestCase):
    def test_table_cells_do_not_glue_together(self):
        message = message_from_bytes((FIXTURES / "wizz-itinerary-table.eml").read_bytes(), policy=policy.default)
        text = server.html_to_text(message.get_body(("html",)).get_content())
        self.assertNotIn("10:5010/12/2026", text.replace(" ", " ").replace(" ", ""))

    def test_stacked_labels_and_parenthesized_airports(self):
        message = message_from_bytes((FIXTURES / "wizz-itinerary-table.eml").read_bytes(), policy=policy.default)
        text = server.html_to_text(message.get_body(("html",)).get_content())
        flight = server.normalize_flight_fields({}, text)
        self.assertEqual(flight["flight_number"], "W4 6406")
        self.assertEqual(flight["origin"], "Tel-Aviv (TLV)")
        self.assertEqual(flight["destination"], "Milan Malpensa - Terminal 1 (MXP)")
        self.assertEqual((flight["departure_date"], flight["departure_time"]), ("2026-12-10", "10:50"))
        self.assertEqual((flight["arrival_date"], flight["arrival_time"]), ("2026-12-10", "14:15"))

    def test_labels_never_take_the_next_line_as_their_value(self):
        self.assertEqual(server._label_values("Departs from:\n Arrives to:\nTel-Aviv", ("departs from",)), [])
        self.assertEqual(server._label_values("From: Rome", ("from",)), ["Rome"])


class AiFailureFallbackTests(unittest.TestCase):
    def test_wizz_itinerary_is_saved_from_text_when_ai_fails(self):
        pushes = []
        record, trips, _ = run_email((FIXTURES / "wizz-itinerary-table.eml").read_bytes(), pushes=pushes)
        self.assertEqual(record["status"], "processed")
        self.assertEqual(record["extraction_method"], "text_fallback")
        self.assertEqual(record["ai_error"], "gemini_http_429")
        flight = trips[0]["flights"][0]
        self.assertEqual((flight["number"], flight["departure_date"], flight["departure_time"]), ("W4 6406", "2026-12-10", "10:50"))
        self.assertEqual(pushes, ["TRIPY: הזמנה נקלטה"])

    def test_plain_airline_and_car_rental_confirmations_survive_ai_failure(self):
        for name, collection in (("airline-confirmation.eml", "flights"), ("car-rental.eml", "rentals")):
            record, trips, _ = run_email((FIXTURES / name).read_bytes())
            self.assertEqual(record["status"], "processed", name)
            self.assertEqual(len(trips[0][collection]), 1, name)

    def test_unreadable_email_is_retried_quietly_before_asking_for_review(self):
        pushes = []
        record, _, notifications = run_email((FIXTURES / "ambiguous-non-booking.eml").read_bytes(), pushes=pushes)
        self.assertEqual(record["review_reason"], "ai_extraction_failed")
        self.assertEqual(pushes, [])
        self.assertEqual(notifications, [])

    def test_retry_loop_backs_off_and_notifies_only_after_last_attempt(self):
        pushes, retried = [], []

        def retry_all():
            now = server._utc_now()
            # too early: nothing is retried yet
            retried.append(server.retry_failed_ai_extractions(now=now + timedelta(minutes=1)))
            for attempt in range(server.AI_RETRY_LIMIT):
                now = now + timedelta(seconds=server.AI_RETRY_BASE_SECONDS * (2 ** attempt) + 1)
                with patch.object(server, "_utc_now", lambda moment=now: moment):
                    retried.append(server.retry_failed_ai_extractions(now=now))

        record, _, notifications = run_email((FIXTURES / "ambiguous-non-booking.eml").read_bytes(), pushes=pushes, extra=retry_all)
        self.assertEqual([len(x) for x in retried], [0] + [1] * server.AI_RETRY_LIMIT)
        self.assertEqual(record["ai_retry_count"], server.AI_RETRY_LIMIT)
        self.assertEqual(pushes, ["TRIPY: נדרשת בדיקה"])
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["ai_error"], "gemini_http_429")

    def test_ai_success_still_wins(self):
        ai = {"type": "flight", "status": "confirmed", "airline": "Wizz Air", "flight_number": "W4 6406", "origin": "TLV",
              "destination": "MXP", "departure_date": "2026-12-10", "departure_time": "10:50", "arrival_time": "14:15"}
        record, trips, _ = run_email((FIXTURES / "wizz-itinerary-table.eml").read_bytes(), gemini=lambda *a, **k: dict(ai))
        self.assertEqual(record["extraction_method"], "ai")
        self.assertEqual(trips[0]["flights"][0]["origin"], "TLV")


class RestoreHiddenFlightRowTests(unittest.TestCase):
    def test_schedule_offers_to_restore_a_hidden_flight_row(self):
        index = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("function restoreFlightButton(e)", index)
        self.assertIn("restore-flight-row", index)
        self.assertIn("if(action==='restore-flight-row')", index)


if __name__ == "__main__":
    unittest.main()
