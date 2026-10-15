import unittest

import server
from booking_types import infer_extended_type
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

    def test_current_subject_beats_stale_forwarded_provider_markers(self):
        result = identify_email_provider(
            sender="forwarder@kobichen.com",
            subject="SIXT rental reservation",
            body="Previous KLM itinerary confirmation and a forwarded Sheraton thread",
        )
        self.assertEqual(result["provider"], "sixt")

    def test_forwarded_header_domain_is_used_when_outer_sender_is_user(self):
        result = identify_email_provider(
            sender="Kobi Chen <kobi@kobichen.com>",
            subject="Fwd: Reservation confirmation",
            body="From: reservations@marriott.com\nSubject: Your hotel reservation",
        )
        self.assertEqual(result["provider"], "marriott")

    def test_sheraton_is_classified_as_marriott_family_hotel(self):
        result = identify_email_provider(
            sender="forwarder@kobichen.com",
            subject="Sheraton reservation confirmation",
        )
        self.assertEqual(result["provider"], "marriott")
        self.assertEqual(result["category_hint"], "hotel")

    def test_booking_hotel_template_is_classified_from_lodging_fields(self):
        text = """Booking.com reservation confirmation\nProperty: Batumi Hotel\nCheck-in: Tue, 20 Oct 2026\nCheck-out: Thu, 22 Oct 2026\nRoom: Double room"""
        self.assertEqual(infer_extended_type({}, text), "hotel")

    def test_booking_lodging_fields_override_false_restaurant_type(self):
        text = """Booking.com reservation confirmation\nProperty: Batumi Hotel\nCheck-in: Tue, 20 Oct 2026\nCheck-out: Thu, 22 Oct 2026\nRoom: Double room\nRestaurant recommendations"""
        self.assertEqual(server.infer_document_type({"type": "restaurant"}, text), "hotel")

    def test_email_source_combines_plain_and_html_parts_for_extraction(self):
        combined = server.combine_email_body_parts(
            "Booking confirmation\nRestaurant recommendations",
            "<html><body><b>Property:</b> Batumi Hotel<br>Check-in: 20 Oct 2026<br>Check-out: 22 Oct 2026<br>Room: Double</body></html>",
        )
        self.assertIn("Property:", combined)
        self.assertIn("Restaurant recommendations", combined)

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

    def test_provider_identity_detects_insurance_and_rail_suppliers(self):
        insurance = identify_email_provider(
            sender="policy@harel-ins.co.il",
            subject="Travel insurance policy",
            body="Harel travel insurance policy details",
        )
        rail = identify_email_provider(
            sender="booking@bahn.de",
            subject="DB Bahn ticket",
            body="Deutsche Bahn train ticket",
        )
        self.assertEqual(insurance["provider"], "harel")
        self.assertEqual(insurance["category_hint"], "insurance")
        self.assertEqual(rail["provider"], "deutsche_bahn")
        self.assertEqual(rail["category_hint"], "train")

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
    def test_explicit_change_overrides_contradictory_ai_confirmation(self):
        self.assertEqual(
            server.intent_from_ai(
                {'type': 'flight', 'status': 'confirmed'},
                'Your flight update',
                'Your reservation has been changed and rescheduled.',
            ),
            'modified',
        )

    def test_provider_confirmation_subject_overrides_cancellation_policy_footer(self):
        for subject in (
            "Your booking at Milan Airport Malpensa T1 is confirmed: #9740201569",
            "Your travel itinerary: MW77WZ",
        ):
            self.assertEqual(
                server.intent_from_ai(
                    {'type': 'car_rental', 'status': 'cancelled'},
                    subject,
                    'Cancellation policy: free cancellation until pickup. You may cancel online.',
                ),
                'confirmed',
            )

    def test_real_cancellation_requires_an_event_sentence(self):
        self.assertEqual(
            server.intent_from_ai(
                {'type': 'hotel', 'status': 'confirmed'},
                'Reservation update',
                'Your reservation has been cancelled by the provider.',
            ),
            'cancelled',
        )
    def test_unique_provider_fallback_matches_sixt_cancellation(self):
        trips = [{"id": "trip-1", "rentals": [{"supplier": "sixt", "status": "confirmed", "vehicle_type": "compact"}]}]
        matches = server.find_single_provider_booking(trips, {"type": "car_rental", "supplier": "sixt"})
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["kind"], "car_rental")


if __name__ == '__main__':
    unittest.main()
