import unittest

from booking_types import infer_extended_type, insurance_schema_properties, normalize_insurance


class BookingTypesTests(unittest.TestCase):
    def test_train_is_detected_from_deutsche_bahn_booking_language(self):
        self.assertEqual(
            infer_extended_type({}, "Deutsche Bahn booking confirmation order 186303342535"),
            "train",
        )

    def test_auto_europe_is_detected_as_car_rental(self):
        self.assertEqual(
            infer_extended_type({}, "Autoeurope voucher for car rental booking"),
            "car_rental",
        )
    def test_avis_is_detected_as_car_rental(self):
        self.assertEqual(
            infer_extended_type({}, "Avis car rental confirmation. Pickup location and drop-off date."),
            "car_rental",
        )

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

    def test_explicit_flight_facts_are_detected_without_provider_name(self):
        self.assertEqual(
            infer_extended_type({}, "Flight number LX123. Departure airport ZRH."),
            "flight",
        )

    def test_explicit_hotel_facts_are_detected_without_provider_name(self):
        self.assertEqual(
            infer_extended_type({}, "Hotel reservation. Check-in date and room type."),
            "hotel",
        )

    def test_explicit_attraction_facts_are_detected_without_provider_name(self):
        self.assertEqual(
            infer_extended_type({}, "Museum ticket and guided tour reservation."),
            "attraction",
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

    def test_insurance_schema_covers_policy_terms_contacts_and_evidence(self):
        schema = insurance_schema_properties()
        self.assertIn("coverage_sections", schema)
        self.assertIn("extensions", schema)
        self.assertIn("claims_steps", schema)
        coverage_fields = schema["coverage_sections"]["items"]["properties"]
        self.assertTrue({"limit", "currency", "deductible", "conditions", "source_page", "source_quote"}.issubset(coverage_fields))
        contact_fields = schema["emergency_contacts"]["items"]["properties"]
        self.assertTrue({"phone_from_abroad", "email", "whatsapp", "hours", "source_page", "source_quote"}.issubset(contact_fields))

    def test_insurance_source_evidence_is_optional_when_unavailable(self):
        schema = insurance_schema_properties()
        for collection in ("coverage_sections", "extensions", "emergency_contacts", "claims_steps", "geographic_scope"):
            required = set(schema[collection]["items"]["required"])
            self.assertTrue(required)
            self.assertTrue({"source_page", "source_quote"}.isdisjoint(required))

    def test_normalize_insurance_preserves_synthetic_policy_detail_and_drops_unknown_fields(self):
        value = normalize_insurance({
            "coverage_sections": [{
                "title": "SYNTHETIC medical cover", "status": "included",
                "limit": "SYNTHETIC LIMIT", "currency": "XXX",
                "deductible": "SYNTHETIC DEDUCTIBLE", "conditions": ["SYNTHETIC CONDITION"],
                "source_page": 4, "source_quote": "SYNTHETIC POLICY QUOTE", "unapproved": "DROP ME",
            }],
            "extensions": [{"name": "SYNTHETIC extension", "status": "selected", "source_page": 5}],
            "emergency_contacts": [{
                "label": "SYNTHETIC assistance", "phone_from_abroad": "SYNTHETIC NUMBER",
                "email": "support@example.invalid", "whatsapp": "SYNTHETIC WHATSAPP",
                "source_page": 2, "source_quote": "SYNTHETIC CONTACT QUOTE",
            }],
            "claims_steps": [{"step": "SYNTHETIC claim step", "deadline": "SYNTHETIC deadline", "source_page": 6}],
            "geographic_scope": [{"area": "SYNTHETIC destination", "status": "covered", "source_page": 7, "source_quote": "SYNTHETIC TERRITORY QUOTE"}],
            "unapproved_root_field": "DROP ME",
        })
        self.assertEqual(value["coverage_sections"][0]["source_quote"], "SYNTHETIC POLICY QUOTE")
        self.assertEqual(value["coverage_sections"][0]["source_page"], 4)
        self.assertNotIn("unapproved", value["coverage_sections"][0])
        self.assertEqual(value["emergency_contacts"][0]["phone_from_abroad"], "SYNTHETIC NUMBER")
        self.assertEqual(value["claims_steps"][0]["deadline"], "SYNTHETIC deadline")
        self.assertEqual(value["geographic_scope"][0]["area"], "SYNTHETIC destination")
        self.assertNotIn("unapproved_root_field", value)
        self.assertEqual(value["review_state"], "needs_review")

    def test_flight_facts_outrank_footer_only_insurance_language(self):
        self.assertEqual(
            infer_extended_type(
                {},
                "Flight number LX123. Departure airport ZRH. "
                "Emergency assistance and insurance policy links in the footer.",
            ),
            "flight",
        )


if __name__ == "__main__":
    unittest.main()
