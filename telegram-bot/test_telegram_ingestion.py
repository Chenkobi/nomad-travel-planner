import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import server


class TelegramIngestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = {
            "data": server.DATA,
            "trips": server.TRIPS,
            "uploads": server.UPLOADS,
            "intake": server.TELEGRAM_INTAKE,
            "token": server.TOKEN,
            "allowed": server.ALLOWED_CHAT,
        }
        server.DATA = root / "events.json"
        server.TRIPS = root / "trips.json"
        server.UPLOADS = root / "uploads"
        server.TELEGRAM_INTAKE = root / "telegram-intake.json"
        server.TOKEN = "test-token"
        server.ALLOWED_CHAT = "123"
        server.save_events([])
        server.save_trips([])

    def tearDown(self):
        for key, value in self.old.items():
            setattr(server, {"data": "DATA", "trips": "TRIPS", "uploads": "UPLOADS", "intake": "TELEGRAM_INTAKE", "token": "TOKEN", "allowed": "ALLOWED_CHAT"}[key], value)
        self.tmp.cleanup()

    def test_handle_message_ingests_html_document_using_declared_metadata(self):
        ai = {
            "type": "flight", "status": "confirmed", "airline": "Arkia",
            "flight_number": "IZ123", "origin": "Tel Aviv", "destination": "Athens",
            "departure_date": "2026-12-10", "departure_time": "08:30",
            "arrival_date": "2026-12-10", "arrival_time": "11:00",
        }
        sent = []
        message = {"chat": {"id": 123}, "document": {
            "file_id": "file-1", "file_name": "boarding.html", "mime_type": "text/html"
        }}
        with patch.object(server, "api", side_effect=lambda method, payload=None: {"file_path": "documents/remote.bin"} if method == "getFile" else {}), \
             patch.object(server, "download_telegram_file", return_value=b"<html>flight</html>"), \
             patch.object(server, "gemini_extract", return_value=ai), \
             patch.object(server, "send", side_effect=lambda chat, text: sent.append((chat, text))):
            server.handle_message(message)

        trips = json.loads(server.TRIPS.read_text())
        intake = json.loads(server.TELEGRAM_INTAKE.read_text())
        self.assertEqual(trips[0]["flights"][0]["number"], "IZ123")
        self.assertEqual(intake[0]["declared_mime_type"], "text/html")
        self.assertEqual(intake[0]["filename"], "boarding.html")
        self.assertEqual(intake[0]["status"], "processed")
        self.assertEqual(intake[0]["stage"], "acknowledged")
        self.assertTrue(any("✅" in text for _, text in sent))

    def test_handle_message_ingests_photo_with_image_mime(self):
        ai = {
            "type": "flight", "status": "confirmed", "airline": "Arkia",
            "flight_number": "IZ124", "origin": "Tel Aviv", "destination": "Athens",
            "departure_date": "2026-12-11", "departure_time": "08:30",
            "arrival_date": "2026-12-11", "arrival_time": "11:00",
        }
        message = {"chat": {"id": 123}, "photo": [{"file_id": "photo-small"}, {"file_id": "photo-large"}]}
        with patch.object(server, "api", return_value={"file_path": "photos/abc.jpg"}), \
             patch.object(server, "download_telegram_file", return_value=b"jpeg-bytes"), \
             patch.object(server, "gemini_extract", return_value=ai), \
             patch.object(server, "send"):
            server.handle_message(message)

        intake = json.loads(server.TELEGRAM_INTAKE.read_text())
        self.assertEqual(intake[0]["declared_mime_type"], "image/jpeg")
        self.assertEqual(intake[0]["filename"], "telegram-photo.jpg")
        self.assertEqual(intake[0]["status"], "processed")

    def test_failed_gemini_processing_persists_failed_stage(self):
        message = {"chat": {"id": 123}, "document": {
            "file_id": "file-2", "file_name": "ticket.pdf", "mime_type": "application/pdf"
        }}
        with patch.object(server, "api", return_value={"file_path": "documents/ticket.pdf"}), \
             patch.object(server, "download_telegram_file", return_value=b"pdf"), \
             patch.object(server, "gemini_extract", return_value=None), \
             patch.object(server, "send"):
            server.handle_message(message)

        intake = json.loads(server.TELEGRAM_INTAKE.read_text())
        self.assertEqual(intake[0]["status"], "failed")
        self.assertEqual(intake[0]["stage"], "extract")
        self.assertIn("extract", intake[0]["error"])

    def test_acknowledgement_failure_does_not_report_ingestion_failure(self):
        ai = {
            "type": "flight", "status": "confirmed", "airline": "Arkia",
            "flight_number": "IZ125", "origin": "Tel Aviv", "destination": "Athens",
            "departure_date": "2026-12-12", "departure_time": "08:30",
            "arrival_date": "2026-12-12", "arrival_time": "11:00",
        }
        message = {"chat": {"id": 123}, "document": {
            "file_id": "file-3", "file_name": "ticket.pdf", "mime_type": "application/pdf"
        }}
        with patch.object(server, "api", return_value={"file_path": "documents/ticket.pdf"}), \
             patch.object(server, "download_telegram_file", return_value=b"pdf"), \
             patch.object(server, "gemini_extract", return_value=ai), \
             patch.object(server, "send", side_effect=RuntimeError("telegram unavailable")):
            server.handle_message(message)

        intake = json.loads(server.TELEGRAM_INTAKE.read_text())
        self.assertEqual(intake[0]["status"], "processed")
        self.assertEqual(intake[0]["stage"], "acknowledgement_failed")
        self.assertIn("telegram unavailable", intake[0]["error"])
        self.assertEqual(len(json.loads(server.TRIPS.read_text())), 1)

    def test_future_flight_requires_valid_iso_dates_and_times(self):
        source = Path(self.tmp.name) / "ticket.pdf"
        source.write_bytes(b"pdf")
        ai = {
            "type": "flight", "airline": "Arkia", "flight_number": "IZ126",
            "origin": "Tel Aviv", "destination": "Athens", "departure_date": "2026-99-99",
            "departure_time": "8:30", "arrival_time": "11:00",
        }
        with self.assertRaisesRegex(ValueError, "flight"):
            server.create_trip_from_document("ticket.pdf", source, ai_override=ai)


if __name__ == "__main__":
    unittest.main()
