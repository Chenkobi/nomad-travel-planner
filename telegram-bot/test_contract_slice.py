import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import server
from event_contract import event_identity, normalize_event


class ContractSliceTests(unittest.TestCase):
    def test_event_identity_is_stable_and_typed(self):
        event = ["19:00", "🍽️", "Dinner", "1 Main St", "מסעדה", "Email", "2026-10-10", "1 Main St"]
        first = event_identity(event, "trip-1")
        second = event_identity(list(event), "trip-1")
        other_trip = event_identity(event, "trip-2")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_trip)
        self.assertTrue(first.startswith("event:restaurant:"))

    def test_normalized_event_preserves_address_and_trip_scope(self):
        event = ["12:00", "🎟️", "Museum", "Museum", "אטרקציה", "PDF", "2026-10-10", "1 Museum Way"]
        normalized = normalize_event(event, "trip-1")
        self.assertEqual(normalized["location"], "1 Museum Way")
        self.assertEqual(normalized["trip_id"], "trip-1")
        self.assertEqual(normalized["kind"], "attraction")

    def test_document_content_type_follows_file_extension(self):
        self.assertEqual(server.document_content_type(Path("booking.txt")), "text/plain; charset=utf-8")
        self.assertEqual(server.document_content_type(Path("booking.html")), "text/html; charset=utf-8")
        self.assertEqual(server.document_content_type(Path("booking.pdf")), "application/pdf")
        self.assertEqual(server.document_content_type(Path("booking.png")), "image/png")
        self.assertEqual(server.document_content_type(Path("booking.webp")), "image/webp")

    def test_document_content_type_does_not_guess_pdf_for_unknown_text(self):
        self.assertEqual(server.document_content_type(Path("booking.csv")), "application/octet-stream")

    def test_waze_destination_requires_real_address_not_name(self):
        self.assertTrue(server.waze_destination("Le Kitchen", "12 Main Street, Zurich"))
        self.assertFalse(server.waze_destination("Le Kitchen", ""))
        self.assertFalse(server.waze_destination("Le Kitchen", "   "))
        self.assertFalse(server.waze_destination("Le Kitchen", "Le Kitchen"))

    def test_normalization_does_not_collapse_same_event_across_trips(self):
        events = [
            normalize_event(["19:00", "🍽️", "Dinner", "1 Main St", "מסעדה", "Email", "2026-10-10", "1 Main St"], "trip-1"),
            normalize_event(["19:00", "🍽️", "Dinner", "1 Main St", "מסעדה", "Email", "2026-10-10", "1 Main St"], "trip-2"),
        ]
        self.assertEqual(len({item["event_id"] for item in events}), 2)


if __name__ == "__main__":
    unittest.main()
