import unittest

from email_rules import classify_email_intent, looks_like_travel_email


class EmailRulesTests(unittest.TestCase):
    def test_cancellation_is_classified_before_confirmation(self):
        intent = classify_email_intent(
            "Booking confirmation cancelled",
            "Your reservation has been cancelled and refunded.",
        )
        self.assertEqual(intent, "cancelled")

    def test_change_email_is_classified_as_modified(self):
        intent = classify_email_intent(
            "Your reservation has changed",
            "The dates and pickup time were updated.",
        )
        self.assertEqual(intent, "modified")

    def test_confirmation_email_is_classified_as_confirmed(self):
        intent = classify_email_intent(
            "Your hotel reservation confirmation",
            "Booking reference ABC123 and itinerary details.",
        )
        self.assertEqual(intent, "confirmed")

    def test_confirmation_with_cancellation_policy_is_not_classified_as_cancelled(self):
        intent = classify_email_intent(
            "Reservation Confirmation #70021413 for Sheraton Batumi Hotel",
            "Your reservation is confirmed. Cancellation policy: free cancellation until October 18, 2026.",
        )
        self.assertEqual(intent, "confirmed")

    def test_unrelated_message_is_not_travel_email(self):
        self.assertFalse(looks_like_travel_email("Dinner tonight", "See you at 20:00."))

    def test_travel_message_is_detected_without_supplier_name(self):
        self.assertTrue(
            looks_like_travel_email(
                "Reservation confirmation",
                "Your booking reference and check-in details are below.",
            )
        )


if __name__ == "__main__":
    unittest.main()
