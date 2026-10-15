import unittest
from pathlib import Path

import server
from booking_types import infer_extended_type
from email_rules import classify_email_intent
from provider_rules import identify_email_provider


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")


class IdentificationQATests(unittest.TestCase):
    """Redacted boundary fixtures for provider/type/intent safety."""

    def test_booking_hotel_beats_restaurant_noise(self):
        text = (
            "Booking.com reservation confirmation\n"
            "Property Example Hotel\nCheck-in 20 Oct 2026\n"
            "Check-out 23 Oct 2026\nRoom Double room\n"
            "Restaurant recommendations"
        )
        provider = identify_email_provider(
            sender="confirmation@booking.com",
            subject="Your reservation confirmation",
            body=text,
        )
        self.assertEqual(provider["provider"], "booking.com")
        self.assertEqual(provider["category_hint"], "marketplace")
        self.assertEqual(server.infer_document_type({"type": "restaurant"}, text), "hotel")
        self.assertEqual(classify_email_intent("Your reservation confirmation", text), "confirmed")

    def test_booking_true_restaurant_requires_restaurant_facts(self):
        text = "Booking.com table reservation\nRestaurant name Example Bistro\nParty size 2\nReservation time 19:30"
        self.assertEqual(infer_extended_type({"type": "restaurant"}, text), "restaurant")
        self.assertEqual(server.infer_document_type({"type": "restaurant"}, text), "restaurant")

    def test_trip_com_marketplace_resolves_hotel_and_attraction_from_facts(self):
        hotel = "Trip.com hotel booking confirmation\nHotel Example Hotel\nCheck-in 20 Oct 2026\nCheck-out 23 Oct 2026"
        attraction = "Trip.com ticket confirmation\nMuseum admission\nVisit 21 Oct 2026\nTicket"
        provider = identify_email_provider(sender="noreply@trip.com", subject="Booking confirmation", body=hotel)
        self.assertEqual(provider["provider"], "trip.com")
        self.assertEqual(provider["category_hint"], "marketplace")
        self.assertEqual(infer_extended_type({}, hotel), "hotel")
        self.assertEqual(server.infer_document_type({}, attraction), "attraction")

    def test_known_good_provider_categories(self):
        cases = (
            ("noreply@wizzair.com", "Wizz Air flight confirmation", "wizz_air", "flight"),
            ("booking@arkia.co.il", "Arkia flight confirmation", "arkia", "flight"),
            ("reservations@marriott.com", "Marriott hotel reservation", "marriott", "hotel"),
        )
        for sender, subject, expected_provider, expected_category in cases:
            with self.subTest(sender=sender):
                result = identify_email_provider(sender=sender, subject=subject)
                self.assertEqual(result["provider"], expected_provider)
                self.assertEqual(result["category_hint"], expected_category)

    def test_missing_and_ambiguous_dates_are_not_invented(self):
        missing = {"type": "flight", "status": "confirmed", "airline": "Wizz Air", "flight_number": "W6 123", "origin": "AAA", "destination": "BBB", "departure_time": "10:00"}
        ambiguous = {**missing, "departure_date": "", "arrival_date": ""}
        with self.assertRaisesRegex(ValueError, "missing unambiguous flight facts"):
            server.create_trip_from_document("confirmation.pdf", Path("/dev/null"), ai_override=missing)
        with self.assertRaisesRegex(ValueError, "missing unambiguous flight facts"):
            server.create_trip_from_document("confirmation.pdf", Path("/dev/null"), ai_override=ambiguous)

    def test_persisted_document_has_a_visible_attachment_boundary(self):
        endpoint = (ROOT / "telegram-bot" / "server.py").read_text(encoding="utf-8")
        self.assertIn('"attachments"', endpoint)
        self.assertIn("documents", INDEX)
        self.assertIn("documentBooking(filename)", INDEX)
        self.assertIn("/api/documents/", INDEX)


if __name__ == "__main__":
    unittest.main()
