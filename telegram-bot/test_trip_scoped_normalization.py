import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import server


class TripScopedNormalizationTests(unittest.TestCase):
    def test_same_hotel_event_in_two_trips_is_not_collapsed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_data, old_trips = server.DATA, server.TRIPS
            try:
                server.DATA = root / "events.json"
                server.TRIPS = root / "trips.json"
                server.save_events([])
                server.save_trips([
                    {"id": "trip-1", "start": "2026-10-10", "end": "2026-10-12", "title": "A", "destinations": [], "hotels": [{"name": "Same Hotel", "start": "2026-10-10", "end": "2026-10-12", "address": "1 Main St"}]},
                    {"id": "trip-2", "start": "2026-10-10", "end": "2026-10-12", "title": "B", "destinations": [], "hotels": [{"name": "Same Hotel", "start": "2026-10-10", "end": "2026-10-12", "address": "1 Main St"}]},
                ])
                events = server.ensure_hotel_events()
                checkins = [event for event in events if "צ׳ק-אין" in str(event[2])]
                self.assertEqual(len(checkins), 2)
                self.assertEqual({event[9] for event in checkins}, {"trip-1", "trip-2"})
            finally:
                server.DATA, server.TRIPS = old_data, old_trips


if __name__ == "__main__":
    unittest.main()
