import unittest

from booking_facts import merge_normalized_booking_facts, normalize_booking_facts
from booking_extraction import extract_booking_facts
from provider_rules import identify_email_provider


class BookingFactsContractTests(unittest.TestCase):
    def test_hotel_facts_have_stable_normalized_shape(self):
        result = normalize_booking_facts(
            extract_booking_facts(
                "Booking confirmation",
                "Hotel: Harbor View\nCity: Lisbon\nCheck-in: 12 April 2027\nCheck-out: 15 April 2027",
            )
        )
        self.assertEqual(result, {
            "type": "hotel",
            "status": "confirmed",
            "provider": "unknown",
            "facts": {
                "hotel": "Harbor View",
                "city": "Lisbon",
                "check_in": "2027-04-12",
                "check_out": "2027-04-15",
            },
        })

    def test_semantic_hotel_facts_fill_fields_omitted_by_extractor(self):
        result = normalize_booking_facts(
            {"type": "hotel", "status": "confirmed"},
            subject="Booking confirmation",
            body=(
                "Your booking is confirmed at Example Resort.\n"
                "Destination: Example Bay\n"
                "Check-in date: Tuesday, May 4, 2027\n"
                "Check-out date: Friday, May 7, 2027"
            ),
        )
        self.assertEqual(result["facts"], {
            "hotel": "Example Resort",
            "city": "Example Bay",
            "check_in": "2027-05-04",
            "check_out": "2027-05-07",
        })

    def test_normalized_facts_replace_blank_model_fields(self):
        extracted = {
            "type": "hotel", "status": "confirmed",
            "hotel": "", "city": "", "check_in": "", "check_out": "",
        }
        contract = normalize_booking_facts(
            extracted,
            subject="Booking confirmation",
            body=(
                "Your booking is confirmed at Example Resort.\n"
                "Destination: Example Bay\n"
                "Check-in date: Tuesday, May 4, 2027\n"
                "Check-out date: Friday, May 7, 2027"
            ),
        )
        merged = merge_normalized_booking_facts(extracted, contract)
        self.assertEqual(
            {key: merged[key] for key in ("hotel", "city", "check_in", "check_out")},
            {"hotel": "Example Resort", "city": "Example Bay", "check_in": "2027-05-04", "check_out": "2027-05-07"},
        )

    def test_flight_facts_normalize_type_and_flight_number(self):
        result = normalize_booking_facts({
            "type": "flight",
            "status": "confirmed",
            "airline": "Example Air",
            "flight_number": " ab1234 ",
            "origin": "AAA",
            "destination": "BBB",
            "departure_date": "2027-05-06",
            "departure_time": "8:30",
        })
        self.assertEqual(result["type"], "flight")
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["facts"]["flight_number"], "AB 1234")
        self.assertEqual(result["facts"]["departure_time"], "08:30")

    def test_car_facts_normalize_rental_alias_and_dates(self):
        result = normalize_booking_facts({
            "type": "car",
            "status": "confirmed",
            "pickup_date": "1 June 2027",
            "pickup_time": "9:05",
            "pickup_location": "Lisbon Airport",
            "vehicle_type": "Compact",
            "dropoff_date": "2027-06-03",
        })
        self.assertEqual(result["type"], "car_rental")
        self.assertEqual(result["facts"]["pickup_date"], "2027-06-01")
        self.assertEqual(result["facts"]["pickup_time"], "09:05")

    def test_unknown_provider_is_metadata_and_cannot_supply_booking_type(self):
        provider = identify_email_provider(
            sender="confirmations@unlisted.example",
            subject="Your confirmation",
            body="Reservation details",
        )
        result = normalize_booking_facts(
            {"type": "other", "status": "unknown"},
            subject="Your confirmation",
            body="Reservation details",
            provider=provider,
        )
        self.assertEqual(provider["provider"], "unknown")
        self.assertEqual(provider["category_hint"], "unknown")
        self.assertEqual(result["type"], "other")
        self.assertEqual(result["provider"], "unknown")


if __name__ == "__main__":
    unittest.main()
