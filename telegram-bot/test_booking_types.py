import unittest

from booking_types import infer_extended_type, normalize_insurance


class BookingTypesTests(unittest.TestCase):
    def test_restaurant_is_detected_from_reservation_email(self):
        self.assertEqual(
            infer_extended_type({}, "restaurant reservation for dinner at 21:00"),
            "restaurant",
        )

    def test_restaurant_ai_fields_win_over_generic_booking_text(self):
        self.assertEqual(
            infer_extended_type({"type": "restaurant", "restaurant_name": "Le Kitchen"}, "booking confirmation"),
            "restaurant",
        )

    def test_insurance_summary_keeps_only_source_fields(self):
        value = normalize_insurance({
            "insurer": "TravelSafe",
            "policy_number": "P-123",
            "coverage_summary": "Medical and baggage",
            "what_to_do": ["Call assistance"],
        })
        self.assertEqual(value["insurer"], "TravelSafe")
        self.assertEqual(value["policy_number"], "P-123")
        self.assertEqual(value["what_to_do"], ["Call assistance"])
        self.assertNotIn("invented_advice", value)


if __name__ == "__main__":
    unittest.main()
