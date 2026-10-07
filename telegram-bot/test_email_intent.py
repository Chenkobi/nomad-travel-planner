import unittest

import server


class EmailIntentTests(unittest.TestCase):
    def test_ai_status_is_authoritative_over_policy_words(self):
        self.assertEqual(
            server.intent_from_ai(
                {'type': 'hotel', 'status': 'confirmed'},
                'Reservation Confirmation',
                'Cancellation policy: free cancellation until the check-in date. Last updated yesterday.',
            ),
            'confirmed',
        )

    def test_ai_marks_real_cancellation(self):
        self.assertEqual(
            server.intent_from_ai(
                {'type': 'hotel', 'status': 'cancelled'},
                'Reservation update',
                'Your reservation has been cancelled.',
            ),
            'cancelled',
        )

    def test_ai_preserves_refunded_as_distinct_lifecycle_intent(self):
        self.assertEqual(
            server.intent_from_ai(
                {'type': 'hotel', 'status': 'refunded'},
                'Refund processed',
                'Your refund was issued.',
            ),
            'refunded',
        )

    def test_missing_ai_status_is_review_not_a_guess(self):
        self.assertEqual(
            server.intent_from_ai(
                {'type': 'hotel'},
                'Reservation confirmation',
                'Cancellation policy applies.',
            ),
            'unknown',
        )


if __name__ == '__main__':
    unittest.main()
