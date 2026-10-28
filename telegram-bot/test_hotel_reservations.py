"""A second reservation at the same hotel (another room / supplier / price) is its own booking."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server

ROOT = Path(__file__).resolve().parents[1]


def hotel_ai(confirmation, supplier="Booking.com"):
    return {"type": "hotel", "status": "confirmed", "hotel": "Duca D'Aosta Hotel", "city": "Aosta", "country": "Italy",
            "check_in": "2026-12-10", "check_out": "2026-12-12", "check_in_time": "15:00", "check_out_time": "11:00",
            "confirmation_number": confirmation, "supplier": supplier}


class HotelReservationTests(unittest.TestCase):
    def ingest(self, directory, name, ai):
        path = directory / "up" / name
        path.write_text("Duca D'Aosta Hotel reservation Aosta 10/12/2026 - 12/12/2026", encoding="utf-8")
        return server.create_trip_from_document(name, path, "Email", ai_override=ai)

    def run_case(self, first, second):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "up").mkdir()
            with patch.object(server, "UPLOADS", d / "up"), patch.object(server, "TRIPS", d / "trips.json"), \
                 patch.object(server, "DATA", d / "trip-data.json"):
                self.ingest(d, "first.txt", first)
                self.ingest(d, "second.txt", second)
                trips = server.load_trips()
                return trips

    def test_second_room_from_another_supplier_is_kept(self):
        trips = self.run_case(hotel_ai("111"), hotel_ai("222", supplier="Expedia"))
        self.assertEqual(len(trips), 1)
        self.assertEqual(sorted(h["confirmation_number"] for h in trips[0]["hotels"]), ["111", "222"])
        self.assertIn("first.txt", trips[0]["documents"])
        self.assertIn("second.txt", trips[0]["documents"])

    def test_same_confirmation_updates_instead_of_duplicating(self):
        updated = hotel_ai("111")
        updated["check_out_time"] = "12:00"
        trips = self.run_case(hotel_ai("111"), updated)
        self.assertEqual(len(trips[0]["hotels"]), 1)
        self.assertEqual(trips[0]["hotels"][0]["checkout_time"], "12:00")

    def test_identity_rules(self):
        same = server._same_hotel_reservation
        self.assertFalse(same({"confirmation_number": "1", "start": "a", "end": "b"}, {"confirmation_number": "2", "start": "a", "end": "b"}))
        self.assertTrue(same({"name": "X", "start": "a", "end": "b"}, {"name": "x", "start": "a", "end": "b"}))
        self.assertFalse(same({"name": "X", "start": "a", "end": "b", "confirmation_number": "1"}, {"name": "X", "start": "a", "end": "b"}))


class InboxAndScheduleUiTests(unittest.TestCase):
    def setUp(self):
        self.index = (ROOT / "index.html").read_text(encoding="utf-8")

    def test_schedule_shows_one_row_for_several_reservations(self):
        self.assertIn("הזמנות`:''}", self.index)
        self.assertIn("hotelSeen.has(groupKey)", self.index)

    def test_notification_panel_lists_recent_emails_with_outcome(self):
        self.assertIn('id="inboxList"', self.index)
        self.assertIn("function inboxStatus(item)", self.index)
        self.assertIn("מנסה שוב", self.index)
        source = (ROOT / "telegram-bot" / "server.py").read_text(encoding="utf-8")
        self.assertIn('"ai_error", "extraction_method", "ai_retry_count")', source)


if __name__ == "__main__":
    unittest.main()
