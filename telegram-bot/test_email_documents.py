"""Bookings that arrived in the email body (no PDF) must stay viewable inside the app."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class EmailViewTests(unittest.TestCase):
    def test_document_names_map_to_their_email(self):
        name = "email_20261028103210785390-1ea6b5f854d5_email-20261028103210785390-1ea6b5f854d5.txt"
        self.assertEqual(server.email_id_for_document(name), "20261028103210785390-1ea6b5f854d5")
        self.assertEqual(server.email_id_for_document("hotel.pdf"), "")

    def test_email_html_is_sanitized_for_display(self):
        dirty = '<p onclick="steal()">Hi</p><script>alert(1)</script><a href="javascript:evil()">x</a><iframe src="https://x"></iframe><img src="https://cdn/x.png">'
        clean = server.sanitize_email_html(dirty)
        for bad in ("<script", "onclick", "javascript:", "<iframe"):
            self.assertNotIn(bad, clean)
        self.assertIn('<img src="https://cdn/x.png">', clean)

    def test_view_serves_original_html_body_and_requires_login(self):
        self.assertTrue(server.api_requires_auth("/api/emails/20261028103210785390-1ea6b5f854d5/view"))
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            with patch.object(server, "EMAIL_DIR", d), patch.object(server, "EMAIL_INDEX", d / "index.json"), \
                 patch.object(server, "UPLOADS", d / "up"):
                record = server.store_incoming_email((FIXTURES / "wizz-itinerary-table.eml").read_bytes())
                record = record[0] if isinstance(record, tuple) else record
                page = server.email_view_html(record["id"])
                self.assertIn("Flight Number", page)
                self.assertIn("<base target=\"_blank\">", page)
                self.assertIsNone(server.email_view_html("20990101000000000000-000000000000"))


class NoInventedHotelsTests(unittest.TestCase):
    def test_hotel_name_guess_stays_on_one_line(self):
        self.assertEqual(server.extract_hotel_name("flights\nhotel\n \ncar rentals"), "")
        self.assertEqual(server.extract_hotel_name("Your stay at Hotel Bristol Palace is confirmed"), "Hotel Bristol Palace is confirmed")

    def test_flight_only_trip_gets_no_guessed_hotel(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            (d / "up").mkdir()
            (d / "up" / "email_x_doc.txt").write_text("Wizz Air flights hotel car rentals\nHotel Booking Deals for you", encoding="utf-8")
            trips = [{"id": "t", "title": "x", "start": "2026-12-10", "end": "2026-12-10", "document": "email_x_doc.txt",
                      "document_titles": {"email_x_doc.txt": "כרטיס טיסה · Wizz Air W4 6406"},
                      "flights": [{"number": "W4 6406", "departure_date": "2026-12-10", "departure_time": "10:50"}], "hotels": []}]
            (d / "trips.json").write_text(json.dumps(trips, ensure_ascii=False), encoding="utf-8")
            with patch.object(server, "UPLOADS", d / "up"), patch.object(server, "TRIPS", d / "trips.json"), \
                 patch.object(server, "DATA", d / "trip-data.json"):
                server.ensure_hotel_events()
                self.assertEqual(server.load_trips()[0]["hotels"], [])


class FrontendTests(unittest.TestCase):
    def test_email_documents_open_summary_then_in_app_email_view(self):
        self.assertIn("function emailIdForDocument(filename)", INDEX)
        self.assertIn("&&!emailIdForDocument(filename)){openDocumentSource(filename);return}", INDEX)
        self.assertIn("/api/emails/${encodeURIComponent(emailId)}/view", INDEX)
        self.assertIn("allow-same-origin allow-popups allow-popups-to-escape-sandbox", INDEX)
        self.assertIn("fieldLabels={airline:'חברת תעופה'", INDEX)

    def test_bookings_can_bring_hidden_flights_back_to_the_schedule(self):
        self.assertIn("data-restore-flight=", INDEX)
        self.assertIn("{schedule_departure_hidden:false,schedule_arrival_hidden:false}", INDEX)


if __name__ == "__main__":
    unittest.main()
