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


if __name__ == "__main__":
    unittest.main()
