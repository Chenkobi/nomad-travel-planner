import unittest

import server
from provider_rules import apply_provider_hint, enrich_ai_with_provider, identify_email_provider


class EmailIntentTests(unittest.TestCase):
    def test_provider_identity_prefers_sender_domain_over_generic_booking_words(self):
        result = identify_email_provider(
            sender="confirmation@booking.com",
            subject="Your reservation confirmation",
            body="Hotel reservation confirmation",
        )
        self.assertEqual(result["provider"], "booking.com")
        self.assertEqual(result["category_hint"], "marketplace")
        self.assertGreaterEqual(result["confidence"], 0.9)

    def test_provider_identity_detects_sixt_and_car_rental(self):
        result = identify_email_provider(
            sender="noreply@sixt.com",
            subject="Your SIXT reservation",
            body="Pickup location and vehicle category",
        )
        self.assertEqual(result["provider"], "sixt")
        self.assertEqual(result["category_hint"], "car_rental")

    def test_provider_identity_detects_airline_from_forwarded_body(self):
        result = identify_email_provider(
            sender="forwarder@kobichen.com",
            subject="Fwd: Flight confirmation",
            body="From: noreply@wizzair.com\nWizz Air flight confirmation",
        )
        self.assertEqual(result["provider"], "wizz_air")
        self.assertEqual(result["category_hint"], "flight")

    def test_provider_identity_detects_klm_and_hertz(self):
        airline = identify_email_provider(
            sender="booking@klm.com",
            subject="KLM itinerary confirmation",
        )
        rental = identify_email_provider(
            sender="reservations@hertz.com",
            subject="Hertz rental reservation",
        )
        self.assertEqual(airline["provider"], "klm")
        self.assertEqual(airline["category_hint"], "flight")
        self.assertEqual(rental["provider"], "hertz")
        self.assertEqual(rental["category_hint"], "car_rental")

    def test_specific_hotel_brand_beats_generic_booking_confirmation(self):
        result = identify_email_provider(
            sender="forwarder@kobichen.com",
            subject="Reservation Confirmation",
            body="Marriott Bonvoy booking confirmation and check-in details",
        )
        self.assertEqual(result["provider"], "marriott")

    def test_provider_hint_fills_missing_type_without_overwriting_extracted_type(self):
        self.assertEqual(
            apply_provider_hint({}, {"category_hint": "car_rental"})["type"],
            "car_rental",
        )
        self.assertEqual(
            apply_provider_hint({"type": "hotel"}, {"category_hint": "car_rental"})["type"],
            "hotel",
        )

    def test_provider_enrichment_adds_supplier_only_for_non_marketplaces(self):
        result = enrich_ai_with_provider(
            {}, {"provider": "sixt", "category_hint": "car_rental"}
        )
        self.assertEqual(result["supplier"], "sixt")
        self.assertNotIn("supplier", enrich_ai_with_provider(
            {}, {"provider": "booking.com", "category_hint": "marketplace"}
        ))

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

    def test_missing_ai_status_uses_clear_email_confirmation(self):
        self.assertEqual(
            server.intent_from_ai(
                {'type': 'hotel'},
                'Reservation confirmation',
                'Your reservation is confirmed. Check-in is on 12 June 2026.',
            ),
            'confirmed',
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
