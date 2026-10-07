import unittest

from booking_lifecycle import find_booking_matches, cancel_booking


class BookingLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.trips = [{
            "id": "trip-1",
            "hotels": [{
                "name": "Hotel One",
                "start": "2026-10-10",
                "end": "2026-10-12",
                "supplier": "Example Travel",
                "confirmation_number": "ABC123",
                "status": "confirmed",
            }],
            "flights": [], "trains": [], "attractions": [], "rentals": [],
        }]

    def test_confirmation_number_is_strong_match(self):
        matches = find_booking_matches(self.trips, {"confirmation_number": "ABC123"})
        self.assertEqual([(m["trip_id"], m["kind"]) for m in matches], [("trip-1", "hotel")])

    def test_unique_supplier_and_dates_match_without_confirmation_number(self):
        matches = find_booking_matches(self.trips, {
            "supplier": "Example Travel",
            "start": "2026-10-10",
            "end": "2026-10-12",
            "name": "Hotel One",
        })
        self.assertEqual(len(matches), 1)

    def test_dates_alone_are_not_enough_to_cancel(self):
        matches = find_booking_matches(self.trips, {"start": "2026-10-10", "end": "2026-10-12"})
        self.assertEqual(matches, [])

    def test_cancel_marks_booking_without_deleting_history(self):
        match = find_booking_matches(self.trips, {"confirmation_number": "ABC123"})[0]
        self.assertTrue(cancel_booking(self.trips, match, "email-1"))
        booking = self.trips[0]["hotels"][0]
        self.assertEqual(booking["status"], "cancelled")
        self.assertEqual(booking["cancelled_by_email"], "email-1")
        self.assertEqual(booking["name"], "Hotel One")


if __name__ == "__main__":
    unittest.main()
