import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import server


class FutureBookingTests(unittest.TestCase):
    def test_future_flight_creates_trip_when_no_trip_exists(self):
        ai = {
            'type': 'flight', 'airline': 'Arkia', 'flight_number': 'IZ123',
            'origin': 'Tel Aviv', 'destination': 'Athens',
            'departure_date': '2026-12-10', 'departure_time': '08:30',
            'arrival_date': '2026-12-10', 'arrival_time': '11:00',
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_data, old_trips, old_uploads = server.DATA, server.TRIPS, server.UPLOADS
            try:
                server.DATA = root / 'events.json'
                server.TRIPS = root / 'trips.json'
                server.UPLOADS = root / 'uploads'
                server.save_trips([])
                server.save_events([])
                source = root / 'arkia.pdf'
                source.write_bytes(b'%PDF fake')
                trip = server.create_trip_from_document('arkia.pdf', source, 'Telegram', ai_override=ai)
                self.assertEqual(trip.get('_ingested_type'), 'flight')
                saved = json.loads(server.TRIPS.read_text())
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0]['start'], '2026-12-10')
                self.assertEqual(saved[0]['flights'][0]['airline'], 'Arkia')
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old_data, old_trips, old_uploads

    def test_same_day_flight_can_omit_arrival_date(self):
        ai = {
            'type': 'flight', 'airline': 'Arkia', 'flight_number': 'IZ124',
            'origin': 'Tel Aviv', 'destination': 'Athens',
            'departure_date': '2026-12-10', 'departure_time': '08:30',
            'arrival_time': '11:00',
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_data, old_trips, old_uploads = server.DATA, server.TRIPS, server.UPLOADS
            try:
                server.DATA = root / 'events.json'; server.TRIPS = root / 'trips.json'; server.UPLOADS = root / 'uploads'
                server.save_trips([]); server.save_events([])
                source = root / 'arkia.pdf'; source.write_bytes(b'%PDF fake')
                trip = server.create_trip_from_document('arkia.pdf', source, 'Telegram', ai_override=ai)
                self.assertEqual(trip['flights'][0]['arrival_date'], '2026-12-10')
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old_data, old_trips, old_uploads

    def test_reversed_dated_shell_is_rejected(self):
        with self.assertRaises(ValueError):
            server.create_dated_trip_shell([], '2026-12-10', '2026-12-09', 'Email', 'bad.txt')


if __name__ == '__main__':
    unittest.main()
