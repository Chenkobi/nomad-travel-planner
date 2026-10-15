import unittest
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import server


class ReleaseGateMatrixTests(unittest.TestCase):
    """End-to-end email-intake matrix for the architectural reset."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = (server.EMAIL_DIR, server.EMAIL_INDEX, server.TRIPS, server.UPLOADS)
        server.EMAIL_DIR = root / "email"
        server.EMAIL_INDEX = root / "email-index.json"
        server.TRIPS = root / "trips.json"
        server.UPLOADS = root / "uploads"

    def tearDown(self):
        server.EMAIL_DIR, server.EMAIL_INDEX, server.TRIPS, server.UPLOADS = self.old
        self.tmp.cleanup()

    def _email(self, sender, subject, body):
        message = EmailMessage()
        message["From"] = sender
        message["Subject"] = subject
        message.set_content(body)
        record, duplicate = server.store_incoming_email(message.as_bytes())
        self.assertFalse(duplicate)
        return record

    def _process(self, sender, subject, body, ai):
        record = self._email(sender, subject, body)
        with patch.object(server, "gemini_extract", return_value=ai), patch.object(server, "send"), patch.object(server, "send_push_notification"):
            server.process_incoming_email(record)
        return server.load_email_index()[0]

    def _hotel_ai(self, **overrides):
        return {
            "type": "hotel", "status": "confirmed", "hotel": "Example Hotel",
            "city": "Paris", "country": "France", "check_in": "2026-10-20",
            "check_out": "2026-10-23", "confirmation_number": "HOTEL-123",
            **overrides,
        }

    def test_direct_hotel_is_processed_and_persisted(self):
        record = self._process(
            "reservations@marriott.com", "Marriott reservation confirmation",
            "Hotel reservation confirmation Check-in 2026-10-20 Check-out 2026-10-23",
            self._hotel_ai(hotel="Marriott Example"),
        )
        self.assertEqual(record["status"], "processed")
        self.assertEqual(record["provider"], "marriott")
        self.assertEqual(server.load_trips()[0]["hotels"][0]["name"], "Marriott Example")

    def test_booking_confirmation_with_omitted_ai_fields_persists_complete_hotel(self):
        record = self._process(
            "confirmation@booking.com",
            "Booking confirmation",
            "Your booking is confirmed at Example Resort.\n"
            "Destination: Example Bay\n"
            "Check-in date: Tuesday, May 4, 2027\n"
            "Check-out date: Friday, May 7, 2027",
            {"type": "hotel", "status": "confirmed", "hotel": "", "city": "", "check_in": "", "check_out": ""},
        )
        self.assertEqual(record["status"], "processed")
        self.assertEqual(record["ingested_type"], "hotel")
        trip = next(item for item in server.load_trips() if item["id"] == record["trip_id"])
        self.assertEqual(trip["hotel"], "Example Resort")
        self.assertEqual((trip["start"], trip["end"]), ("2027-05-04", "2027-05-07"))
        self.assertEqual(trip["destinations"][0]["city"], "Example Bay")

    def test_successful_reprocessing_clears_stale_review_reason(self):
        record = self._email(
            "confirmation@booking.com", "Hotel booking confirmation",
            "Booking confirmed. Hotel: Example Hotel. Check-in: 2027-06-10. Check-out: 2027-06-12.",
        )
        server.update_email_record(record["id"], status="received", review_reason="obsolete_missing_fields")
        with patch.object(server, "gemini_extract", return_value=self._hotel_ai()), patch.object(server, "send"), patch.object(server, "send_push_notification"):
            server.process_incoming_email(record)
        saved = next(item for item in server.load_email_index() if item["id"] == record["id"])
        self.assertEqual(saved["status"], "processed")
        self.assertEqual(saved.get("review_reason", ""), "")

    def test_booking_and_trip_com_marketplace_hotels_are_processed(self):
        for sender, provider, dates in (
            ("confirmation@booking.com", "booking.com", ("2026-10-20", "2026-10-23")),
            ("noreply@trip.com", "trip.com", ("2026-11-20", "2026-11-23")),
        ):
            with self.subTest(provider=provider):
                record = self._process(
                    sender, "Hotel booking confirmation",
                    f"{provider} reservation Property Example Hotel Check-in {dates[0]} Check-out {dates[1]}",
                    self._hotel_ai(check_in=dates[0], check_out=dates[1], confirmation_number=f"{provider}-123"),
                )
                self.assertEqual(record["status"], "processed")
                self.assertEqual(record["provider"], provider)
                self.assertEqual(record["provider_category_hint"], "marketplace")
        self.assertEqual(len(server.load_trips()), 2)

    def test_airline_and_car_rental_provider_hints_reach_persistence(self):
        flight = self._process(
            "confirmation@wizzair.com", "Wizz Air flight confirmation",
            "Wizz Air flight confirmation",
            {"type": "other", "status": "confirmed", "airline": "Wizz Air", "flight_number": "W6 123", "origin": "TLV", "destination": "BUD", "departure_date": "2026-10-20", "departure_time": "10:00", "arrival_date": "2026-10-20", "arrival_time": "13:00"},
        )
        self.assertEqual(flight["status"], "processed")
        self.assertEqual(flight["ingested_type"], "flight")
        rental = self._process(
            "reservations@sixt.com", "Sixt rental confirmation",
            "Sixt car rental confirmation",
            {"type": "other", "status": "confirmed", "pickup_date": "2026-10-20", "pickup_time": "09:00", "pickup_location": "Paris Airport", "vehicle_type": "Compact", "dropoff_date": "2026-10-23", "dropoff_time": "09:00", "dropoff_location": "Paris Airport"},
        )
        self.assertEqual(rental["status"], "processed")
        self.assertEqual(rental["ingested_type"], "car_rental")

    def test_unknown_provider_is_reviewed_only_when_facts_are_insufficient(self):
        record = self._process(
            "travel@example.test", "Reservation confirmation", "A reservation confirmation",
            {"type": "other", "status": "confirmed"},
        )
        self.assertEqual(record["provider"], "unknown")
        self.assertEqual(record["status"], "needs_review")
        self.assertFalse(server.load_trips())

    def test_cancellation_updates_unique_booking_without_deleting_history(self):
        first = self._process(
            "confirmation@booking.com", "Reservation confirmation", "Booking reservation",
            self._hotel_ai(),
        )
        cancel = self._process(
            "confirmation@booking.com", "Reservation cancelled", "Your reservation was cancelled",
            self._hotel_ai(status="cancelled"),
        )
        self.assertEqual(first["status"], "processed")
        self.assertEqual(cancel["status"], "processed")
        hotel = server.load_trips()[0]["hotels"][0]
        self.assertEqual(hotel["status"], "cancelled")
        self.assertEqual(hotel["name"], "Example Hotel")

    def test_modification_is_reviewed_before_mutating_existing_booking(self):
        self._process("confirmation@booking.com", "Reservation confirmation", "Booking reservation", self._hotel_ai())
        changed = self._process(
            "confirmation@booking.com", "Reservation changed", "Your reservation dates changed",
            self._hotel_ai(status="modified", check_in="2026-10-21", check_out="2026-10-24"),
        )
        self.assertEqual(changed["status"], "needs_review")
        self.assertEqual(changed["review_reason"], "replacement_requires_review")
        self.assertEqual(server.load_trips()[0]["hotels"][0]["start"], "2026-10-20")

    def test_missing_or_ambiguous_dates_are_reviewed_and_not_invented(self):
        record = self._process(
            "noreply@wizzair.com", "Wizz flight confirmation", "Flight confirmation",
            {"type": "flight", "status": "confirmed", "airline": "Wizz Air", "flight_number": "W6 123", "origin": "TLV", "destination": "BUD", "departure_time": "10:00"},
        )
        self.assertEqual(record["status"], "needs_review")
        self.assertIn("missing unambiguous flight facts", record["review_reason"])
        self.assertFalse(server.load_trips())

    def test_processed_email_is_visible_via_trip_document_and_notification(self):
        record = self._process(
            "reservations@marriott.com", "Hotel reservation confirmation", "Hotel reservation",
            self._hotel_ai(),
        )
        self.assertEqual(record["status"], "processed")
        self.assertTrue(record["trip_id"])
        trip = next(trip for trip in server.load_trips() if trip["id"] == record["trip_id"])
        self.assertIn(record["document"], trip["documents"])
        visible = {item["id"]: item for item in server.load_notifications()}
        self.assertIn(record["id"], visible)
        self.assertEqual(visible[record["id"]]["status"], "processed")

    def test_unknown_direct_hotel_with_multilingual_free_form_facts_is_processed(self):
        record = self._process(
            "reservas@cedarlane.example", "אישור שהייה במלון",
            "הזמנה מאושרת עבור מלון Cedar Lane Inn בפורטו.",
            self._hotel_ai(hotel="Cedar Lane Inn", city="Porto", country="Portugal"),
        )
        self.assertEqual(record["provider"], "unknown")
        self.assertEqual(record["status"], "processed")
        hotel = server.load_trips()[0]["hotels"][0]
        self.assertEqual(hotel["name"], "Cedar Lane Inn")
        self.assertEqual(hotel["city"], "Porto")

    def test_html_only_email_preserves_rendered_facts_through_intake(self):
        message = EmailMessage()
        message["From"] = "bookings@unlisted.example"
        message["Subject"] = "Booking confirmed"
        message.add_alternative(
            "<html><body><h1>Booking confirmed</h1><p>Hotel: Orchard House</p>"
            "<p>Check-in: 2027-08-02</p><p>Check-out: 2027-08-05</p></body></html>",
            subtype="html",
        )
        record, duplicate = server.store_incoming_email(message.as_bytes())
        self.assertFalse(duplicate)
        self.assertIn("Orchard House", server.email_body_text(record))
        with patch.object(server, "gemini_extract", return_value=self._hotel_ai(
            hotel="Orchard House", check_in="2027-08-02", check_out="2027-08-05",
        )), patch.object(server, "send"), patch.object(server, "send_push_notification"):
            server.process_incoming_email(record)
        stored = server.load_email_index()[0]
        self.assertEqual(stored["status"], "processed")
        self.assertEqual(server.load_trips()[0]["hotels"][0]["name"], "Orchard House")

    def test_ambiguous_hotel_dates_are_reviewed_without_inventing_dates(self):
        record = self._process(
            "bookings@unlisted.example", "Hotel booking confirmation",
            "Hotel booking confirmation. Stay dates: 04/05/2027 to 07/05/2027.",
            self._hotel_ai(check_in="", check_out=""),
        )
        self.assertEqual(record["status"], "needs_review")
        self.assertEqual(record["review_reason"], "ValueError: hotel document missing unambiguous lodging facts")
        self.assertFalse(server.load_trips())

    def test_duplicate_confirmation_is_processed_once_and_not_repeated_in_trip(self):
        first = self._process(
            "bookings@unlisted.example", "Hotel booking confirmation", "Hotel reservation",
            self._hotel_ai(confirmation_number="DUP-123"),
        )
        duplicate_message = EmailMessage()
        duplicate_message["From"] = "bookings@unlisted.example"
        duplicate_message["Subject"] = "Hotel booking confirmation"
        duplicate_message.set_content("Hotel reservation")
        second, duplicate = server.store_incoming_email(duplicate_message.as_bytes())
        self.assertTrue(duplicate)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(first["status"], "processed")
        hotels = [hotel for trip in server.load_trips() for hotel in trip.get("hotels", [])]
        self.assertEqual(len(hotels), 1)
        self.assertEqual(hotels[0]["confirmation_number"], "DUP-123")

    def test_cancellation_without_unique_match_is_reviewed_and_keeps_trip_unchanged(self):
        record = self._process(
            "bookings@unlisted.example", "Reservation cancelled", "Your reservation was cancelled",
            self._hotel_ai(status="cancelled", confirmation_number="NOT-FOUND"),
        )
        self.assertEqual(record["status"], "needs_review")
        self.assertEqual(record["review_reason"], "no_unique_match")
        self.assertFalse(server.load_trips())


if __name__ == "__main__":
    unittest.main()
