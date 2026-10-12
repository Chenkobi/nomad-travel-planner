import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import server


class FlightTextFallbackTests(unittest.TestCase):
    def test_arkia_html_text_fills_explicit_flight_facts_when_ai_misses_fields(self):
        text = "Arkia IZ 138\nTel Aviv to Athens\nDecember 10, 2026 08:30\nArrival 10:40"
        result = server.normalize_flight_fields({"type": "flight", "status": "confirmed"}, text)
        self.assertEqual(result["airline"], "Arkia")
        self.assertEqual(result["flight_number"], "IZ 138")
        self.assertEqual(result["origin"], "Tel Aviv")
        self.assertEqual(result["destination"], "Athens")
        self.assertEqual(result["departure_date"], "2026-12-10")
        self.assertEqual(result["departure_time"], "08:30")
        self.assertEqual(result["arrival_time"], "10:40")

    def test_ai_values_are_preserved_over_text_fallback(self):
        text = "Arkia IZ 138\nTel Aviv to Athens\nDecember 10, 2026 08:30\nArrival 10:40"
        result = server.normalize_flight_fields({
            "type": "flight",
            "status": "confirmed",
            "airline": "Arkia",
            "flight_number": "IZ 138",
            "origin": "Tel Aviv",
            "destination": "Athens",
            "departure_date": "2026-12-11",
            "departure_time": "09:00",
            "arrival_time": "11:10",
        }, text)
        self.assertEqual(result["departure_date"], "2026-12-11")
        self.assertEqual(result["departure_time"], "09:00")
        self.assertEqual(result["arrival_time"], "11:10")

    def test_wizz_compact_and_spaced_codes_are_normalized(self):
        for code in ("W6 1234", "W61234"):
            result = server.normalize_flight_fields({}, f"Wizz Air\nFlight number: {code}\nTel Aviv to Budapest")
            self.assertEqual(result["airline"], "Wizz Air")
            self.assertEqual(result["flight_number"], "W6 1234")
            self.assertEqual(result["origin"], "Tel Aviv")
            self.assertEqual(result["destination"], "Budapest")

    def test_labeled_english_and_hebrew_facts_are_used_only_when_explicit(self):
        text = """חברת תעופה: Wizz Air
מספר טיסה: W61234
מוצא: תל אביב
יעד: בודפשט
תאריך יציאה: 10 בדצמבר 2026
שעת יציאה: 08:30
שעת נחיתה: 11:30"""
        result = server.normalize_flight_fields({}, text)
        self.assertEqual(result["airline"], "Wizz Air")
        self.assertEqual(result["flight_number"], "W6 1234")
        self.assertEqual(result["origin"], "תל אביב")
        self.assertEqual(result["destination"], "בודפשט")
        self.assertEqual(result["departure_date"], "2026-12-10")
        self.assertEqual(result["departure_time"], "08:30")
        self.assertEqual(result["arrival_time"], "11:30")

    def test_hotel_context_overrides_contradictory_restaurant_label(self):
        result = server.infer_document_type(
            {"type": "restaurant"},
            "Your reservation confirmation and check-in details",
            "voco hotel reservation confirmation",
        )
        self.assertEqual(result, "hotel")

    def test_sender_context_classifies_rental_when_document_text_is_sparse(self):
        result = server.infer_document_type({}, "Pickup date and drop-off date", "Avis booking confirmation")
        self.assertEqual(result, "car_rental")

    def test_el_al_sundor_receipt_normalizes_compact_date_and_table_facts(self):
        text = """SUNDOR
EL AL GROUP
From    To    Flight    Departure    Arrival
TEL AVIV YAFO    BATUMI    LY5113    05:15    08:30
23OCT2026"""
        result = server.normalize_flight_fields({}, text)
        self.assertEqual(result["airline"], "Sundor")
        self.assertEqual(result["flight_number"], "LY5113")
        self.assertEqual(result["origin"], "TEL AVIV YAFO")
        self.assertEqual(result["destination"], "BATUMI")
        self.assertEqual(result["departure_date"], "2026-10-23")
        self.assertEqual(result["departure_time"], "05:15")
        self.assertEqual(result["arrival_time"], "08:30")

    def test_duplicate_responsive_text_does_not_create_ambiguity(self):
        text = """Flight number: W61234
Flight number: W61234
From: Tel Aviv
From: Tel Aviv
To: Budapest
To: Budapest
Departure date: 2026-12-10
Departure date: 2026-12-10
Departure time: 08:30
Departure time: 08:30
Arrival time: 11:30
Arrival time: 11:30"""
        result = server.normalize_flight_fields({}, text)
        self.assertEqual(result["flight_number"], "W6 1234")
        self.assertEqual(result["origin"], "Tel Aviv")
        self.assertEqual(result["destination"], "Budapest")
        self.assertEqual(result["departure_date"], "2026-12-10")

    def test_conflicting_explicit_routes_are_rejected_instead_of_flattened(self):
        text = "From: Tel Aviv\nTo: Budapest\nFrom: Tel Aviv\nTo: London"
        result = server.normalize_flight_fields({}, text)
        self.assertEqual(result.get("origin"), "")
        self.assertEqual(result.get("destination"), "")

    def test_explicit_segments_remain_separate(self):
        ai = {"type": "flight", "segments": [
            {"airline": "Wizz Air", "flight_number": "W61234", "origin": "Tel Aviv", "destination": "Budapest", "departure_date": "2026-12-10", "departure_time": "08:30", "arrival_time": "11:30"},
            {"airline": "Wizz Air", "flight_number": "W65678", "origin": "Budapest", "destination": "London", "departure_date": "2026-12-10", "departure_time": "13:00", "arrival_time": "15:00"},
        ]}
        result = server.normalize_flight_segments(ai, "")
        self.assertEqual([item["flight_number"] for item in result], ["W6 1234", "W6 5678"])
        self.assertEqual([(item["origin"], item["destination"]) for item in result], [("Tel Aviv", "Budapest"), ("Budapest", "London")])

    def test_single_incomplete_gemini_segment_is_completed_from_pdf_text(self):
        ai = {"type": "flight", "segments": [{"airline": "Sundor", "flight_number": "LY5113", "departure_date": "2026-10-23"}]}
        text = """SUNDOR
EL AL GROUP
From    To    Flight    Departure    Arrival
TEL AVIV YAFO    BATUMI    LY5113    05:15    08:30
23OCT2026"""
        result = server.normalize_flight_segments(ai, text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["origin"], "TEL AVIV YAFO")
        self.assertEqual(result[0]["destination"], "BATUMI")
        self.assertEqual(result[0]["departure_time"], "05:15")
        self.assertEqual(result[0]["arrival_time"], "08:30")

    def test_create_trip_completes_single_incomplete_gemini_segment_from_source_text(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = server.DATA, server.TRIPS, server.UPLOADS
            try:
                server.DATA, server.TRIPS, server.UPLOADS = root / "events.json", root / "trips.json", root / "uploads"
                server.save_events([])
                server.save_trips([{
                    "id": "georgia-trip",
                    "title": "Georgia",
                    "start": "2026-10-23",
                    "end": "2026-10-26",
                    "destinations": [],
                    "hotels": [],
                    "flights": [],
                    "trains": [],
                    "attractions": [],
                    "rentals": [],
                    "restaurants": [],
                    "documents": [],
                }])
                source = root / "elal.txt"
                source.write_text("""SUNDOR
EL AL GROUP
From    To    Flight    Departure    Arrival
TEL AVIV YAFO    BATUMI    LY5113    05:15    08:30
23OCT2026""", encoding="utf-8")
                ai = {"type": "flight", "status": "confirmed", "segments": [{"airline": "Sundor", "flight_number": "LY5113", "departure_date": "2026-10-23"}]}
                trip = server.create_trip_from_document("elal.txt", source, source="Email", ai_override=ai)
                self.assertEqual(len(trip["flights"]), 1)
                self.assertEqual(trip["flights"][0]["number"], "LY5113")
                self.assertEqual(trip["flights"][0]["origin"], "TEL AVIV YAFO")
                self.assertEqual(trip["flights"][0]["destination"], "BATUMI")
                self.assertEqual(trip["flights"][0]["departure_time"], "05:15")
                self.assertEqual(trip["flights"][0]["arrival_time"], "08:30")
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old

    def test_create_trip_persists_each_explicit_segment_as_flat_flight_record(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = server.DATA, server.TRIPS, server.UPLOADS
            try:
                server.DATA, server.TRIPS, server.UPLOADS = root / "events.json", root / "trips.json", root / "uploads"
                server.save_events([])
                server.save_trips([])
                source = root / "wizz.txt"
                source.write_text("connection", encoding="utf-8")
                ai = {"type": "flight", "status": "confirmed", "segments": [
                    {"airline": "Wizz Air", "flight_number": "W61234", "origin": "Tel Aviv", "destination": "Budapest", "departure_date": "2026-12-10", "departure_time": "08:30", "arrival_time": "11:30"},
                    {"airline": "Wizz Air", "flight_number": "W65678", "origin": "Budapest", "destination": "London", "departure_date": "2026-12-10", "departure_time": "13:00", "arrival_time": "15:00"},
                ]}
                trip = server.create_trip_from_document("wizz.txt", source, ai_override=ai)
                self.assertEqual([flight["number"] for flight in trip["flights"]], ["W6 1234", "W6 5678"])
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old


if __name__ == "__main__":
    unittest.main()
