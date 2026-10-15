import unittest
from pathlib import Path

import server
from booking_extraction import extract_booking_facts


FIXTURES = Path(__file__).with_name("fixtures")


class ProviderAgnosticExtractionTests(unittest.TestCase):
    def _extract(self, name):
        message = (FIXTURES / name).read_text(encoding="utf-8")
        headers, body = message.split("\n\n", 1)
        subject = next(line.split(":", 1)[1].strip() for line in headers.splitlines() if line.lower().startswith("subject:"))
        return extract_booking_facts(subject, body)

    def test_direct_hotel_email_extracts_lodging_facts_without_supplier_rule(self):
        result = self._extract("direct-hotel.eml")
        self.assertEqual(result["type"], "hotel")
        self.assertEqual(result["facts"]["hotel"], "Harbor View Hotel")
        self.assertEqual(result["facts"]["check_in"], "2027-04-12")
        self.assertEqual(result["facts"]["check_out"], "2027-04-15")

    def test_free_form_direct_hotel_email_extracts_narrative_facts(self):
        result = self._extract("direct-hotel-freeform.eml")
        self.assertEqual(result["type"], "hotel")
        self.assertEqual(result["facts"]["hotel"], "Cedar Lane Inn")
        self.assertEqual(result["facts"]["city"], "Porto")
        self.assertEqual(result["facts"]["check_in"], "2027-09-07")
        self.assertEqual(result["facts"]["check_out"], "2027-09-10")
        self.assertEqual(result["facts"]["check_in_time"], "15:30")

    def test_hebrew_hotel_labels_are_supported_without_translating_values(self):
        result = self._extract("hebrew-hotel.eml")
        self.assertEqual(result["type"], "hotel")
        self.assertEqual(result["facts"]["hotel"], "מלון שקיעה")
        self.assertEqual(result["facts"]["city"], "אילת")
        self.assertEqual(result["facts"]["check_in"], "2027-04-12")
        self.assertEqual(result["facts"]["check_out"], "2027-04-15")

    def test_html_and_ocr_noise_are_normalized(self):
        result = self._extract("html-ocr-hotel.eml")
        self.assertEqual(result["type"], "hotel")
        self.assertEqual(result["facts"]["hotel"], "Orchard House")
        self.assertEqual(result["facts"]["check_in"], "2027-08-02")
        self.assertEqual(result["facts"]["check_out"], "2027-08-05")
        self.assertEqual(result["facts"]["check_in_time"], "09:05")

    def test_ota_hotel_email_uses_facts_not_marketplace_identity(self):
        result = self._extract("ota-hotel.eml")
        self.assertEqual(result["type"], "hotel")
        self.assertEqual(result["provider"], "marketplace")
        self.assertEqual(result["facts"]["city"], "Lisbon")

    def test_airline_confirmation_extracts_normalized_flight_facts(self):
        result = self._extract("airline-confirmation.eml")
        self.assertEqual(result["type"], "flight")
        self.assertEqual(result["facts"]["flight_number"], "AB 1234")
        self.assertEqual(result["facts"]["departure_date"], "2027-05-06")
        self.assertEqual(result["facts"]["departure_time"], "08:30")

    def test_car_rental_extracts_pickup_and_vehicle_facts(self):
        result = self._extract("car-rental.eml")
        self.assertEqual(result["type"], "car_rental")
        self.assertEqual(result["facts"]["pickup_date"], "2027-06-01")
        self.assertEqual(result["facts"]["pickup_location"], "Lisbon Airport")
        self.assertEqual(result["facts"]["vehicle_type"], "Compact")

    def test_ambiguous_non_booking_email_is_not_promoted_to_booking(self):
        result = self._extract("ambiguous-non-booking.eml")
        self.assertEqual(result["type"], "other")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["facts"], {})

    def test_provider_hint_is_optional_and_cannot_override_conflicting_facts(self):
        result = extract_booking_facts(
            "Travel update",
            "Flight number: AB 1234\nDeparture airport: AAA\nArrival airport: BBB\nDeparture date: 2027-05-06",
            provider_hint="hotel",
        )
        self.assertEqual(result["type"], "flight")
        self.assertEqual(result["provider"], "hotel")

    def test_server_document_type_uses_shared_fact_boundary_without_provider_name(self):
        self.assertEqual(
            server.infer_document_type(
                {},
                "Flight number: AB1234\nDeparture airport: AAA\nArrival airport: BBB\nDeparture date: 06 May 2027",
            ),
            "flight",
        )


if __name__ == "__main__":
    unittest.main()
