import unittest

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


if __name__ == "__main__":
    unittest.main()
