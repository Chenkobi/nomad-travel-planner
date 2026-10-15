import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import server


class FutureBookingTests(unittest.TestCase):
    def test_city_display_name_uses_city_not_airport_rental_description(self):
        self.assertEqual(server.city_display_name("Milan Airport Malpensa T1 - Terminal 1, Car Rental Center"), "מילאנו")
        self.assertEqual(server.trip_title_from_location("Milan Airport Malpensa T1"), "איטליה מילאנו")

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
                self.assertEqual(trip["flights"][0]["arrival_date"], "2026-12-10")
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old_data, old_trips, old_uploads

    def test_one_way_flight_can_omit_arrival_time(self):
        ai = {
            'type': 'flight', 'airline': 'Arkia', 'flight_number': 'IZ414',
            'origin': 'Batumi', 'destination': 'Tel Aviv',
            'departure_date': '2026-10-26', 'departure_time': '14:15',
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_data, old_trips, old_uploads = server.DATA, server.TRIPS, server.UPLOADS
            try:
                server.DATA = root / 'events.json'; server.TRIPS = root / 'trips.json'; server.UPLOADS = root / 'uploads'
                server.save_trips([]); server.save_events([])
                source = root / 'arkia.html'; source.write_text('<html>Arkia one-way ticket</html>', encoding='utf-8')
                trip = server.create_trip_from_document('arkia.html', source, 'Email', ai_override=ai, mime_type='text/html')
                self.assertEqual(trip['_ingested_type'], 'flight')
                self.assertEqual(trip['flights'][0]['departure_time'], '14:15')
                self.assertEqual(trip['flights'][0]['arrival_time'], '')
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old_data, old_trips, old_uploads

    def test_car_rental_month_name_dates_are_normalized_before_routing(self):
        with TemporaryDirectory() as tmp:
            old_data, old_trips, old_uploads = server.DATA, server.TRIPS, server.UPLOADS
            try:
                root = Path(tmp)
                server.DATA = root / "events.json"
                server.TRIPS = root / "trips.json"
                server.UPLOADS = root / "uploads"
                server.save_trips([])
                path = root / "sixt.txt"
                path.write_text("Sixt Milan Airport Malpensa T1", encoding="utf-8")
                trip = server.create_trip_from_document(
                    "sixt.txt", path, source="Email", ai_override={
                        "type": "car_rental", "status": "confirmed",
                        "pickup_date": "October 24, 2026", "pickup_time": "10:00",
                        "pickup_location": "Milan Airport Malpensa T1", "vehicle_type": "compact",
                        "dropoff_date": "October 27, 2026", "dropoff_time": "10:00",
                        "dropoff_location": "Milan Airport Malpensa T1",
                    })
                self.assertEqual(trip["rentals"][0]["pickup_date"], "2026-10-24")
                self.assertEqual(trip["rentals"][0]["dropoff_date"], "2026-10-27")
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old_data, old_trips, old_uploads

    def test_auto_europe_text_fallback_fills_rental_fields(self):
        text = """Auto Europe voucher
Pick-up date: October 24, 2026
Pick-up time: 10:00
Pick-up location: Milan Airport Malpensa T1
Vehicle type: Compact
Drop-off date: October 27, 2026
Drop-off time: 10:00
Drop-off location: Milan Airport Malpensa T1"""
        result = server.normalize_car_rental_fields({}, text)
        self.assertEqual(result["pickup_date"], "2026-10-24")
        self.assertEqual(result["pickup_time"], "10:00")
        self.assertEqual(result["pickup_location"], "Milan Airport Malpensa T1")
        self.assertEqual(result["vehicle_type"], "Compact")
        self.assertEqual(result["dropoff_date"], "2026-10-27")
        self.assertEqual(result["dropoff_location"], "Milan Airport Malpensa T1")
    def test_create_car_rental_uses_source_fallback_when_ai_fields_are_missing(self):
        with TemporaryDirectory() as tmp:
            old_data, old_trips, old_uploads = server.DATA, server.TRIPS, server.UPLOADS
            try:
                root = Path(tmp)
                server.DATA = root / "events.json"
                server.TRIPS = root / "trips.json"
                server.UPLOADS = root / "uploads"
                server.save_trips([])
                path = root / "autoeurope.txt"
                path.write_text("""Auto Europe voucher
Pick-up date: October 24, 2026
Pick-up time: 10:00
Pick-up location: Milan Airport Malpensa T1
Vehicle type: Compact
Drop-off date: October 27, 2026
Drop-off time: 10:00
Drop-off location: Milan Airport Malpensa T1""", encoding="utf-8")
                trip = server.create_trip_from_document(
                    "autoeurope.txt", path, source="Email", ai_override={
                        "type": "car_rental", "status": "confirmed",
                    })
                self.assertEqual(trip["rentals"][0]["pickup_date"], "2026-10-24")
                self.assertEqual(trip["rentals"][0]["pickup_location"], "Milan Airport Malpensa T1")
                self.assertEqual(trip["rentals"][0]["vehicle_type"], "Compact")
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old_data, old_trips, old_uploads
    def test_train_month_name_dates_are_normalized_before_persistence(self):
        with TemporaryDirectory() as tmp:
            old_data, old_trips, old_uploads = server.DATA, server.TRIPS, server.UPLOADS
            try:
                root = Path(tmp)
                server.DATA = root / "events.json"
                server.TRIPS = root / "trips.json"
                server.UPLOADS = root / "uploads"
                server.save_trips([])
                server.save_events([])
                path = root / "bahn.txt"
                path.write_text("Deutsche Bahn Berlin to Munich", encoding="utf-8")
                trip = server.create_trip_from_document(
                    "bahn.txt", path, source="Email", ai_override={
                        "type": "train", "status": "confirmed", "train_number": "ICE 701",
                        "origin": "Berlin", "destination": "Munich",
                        "departure_date": "October 24, 2026", "departure_time": "08:10",
                    })
                self.assertEqual(trip["trains"][0]["date"], "2026-10-24")
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old_data, old_trips, old_uploads
    def test_insurance_month_name_dates_are_normalized_before_routing(self):
        with TemporaryDirectory() as tmp:
            old_data, old_trips, old_uploads = server.DATA, server.TRIPS, server.UPLOADS
            try:
                root = Path(tmp)
                server.DATA = root / "events.json"
                server.TRIPS = root / "trips.json"
                server.UPLOADS = root / "uploads"
                server.save_trips([])
                path = root / "insurance.pdf"
                path.write_bytes(b"%PDF fake")
                trip = server.create_trip_from_document(
                    "insurance.pdf", path, source="Email", ai_override={
                        "type": "insurance", "status": "confirmed",
                        "valid_from": "October 24, 2026", "valid_to": "October 31, 2026",
                        "insurer": "TravelSafe", "policy_number": "P-123",
                    })
                self.assertEqual(trip["start"], "2026-10-24")
                self.assertEqual(trip["end"], "2026-10-31")
                self.assertEqual(trip["insurance"]["insurer"], "TravelSafe")
                self.assertEqual(trip.get("events", []), [])
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old_data, old_trips, old_uploads
    def test_reversed_dated_shell_is_rejected(self):
        with self.assertRaises(ValueError):
            server.create_dated_trip_shell([], '2026-12-10', '2026-12-09', 'Email', 'bad.txt')

    def test_trip_com_hotel_is_attached_to_existing_dated_trip(self):
        """Marketplace email facts route into the matching trip, not a duplicate."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_data, old_trips, old_uploads = server.DATA, server.TRIPS, server.UPLOADS
            try:
                server.DATA = root / 'events.json'; server.TRIPS = root / 'trips.json'; server.UPLOADS = root / 'uploads'
                existing = {
                    'id': 'trip-existing', 'title': 'Synthetic trip',
                    'start': '2026-10-20', 'end': '2026-10-23',
                    'days': 4, 'source': 'Manual', 'documents': ['manual.txt'],
                    'document_titles': {'manual.txt': 'Synthetic itinerary'},
                    'destinations': [{'city': 'Batumi', 'country': 'Georgia'}],
                    'hotels': [], 'flights': [], 'trains': [], 'attractions': [],
                    'rentals': [], 'restaurants': [],
                }
                server.save_trips([existing]); server.save_events([])
                path = root / 'trip-com.txt'
                path.write_text(
                    'Trip.com reservation confirmation\\n'
                    'Check-in: October 21, 2026\\n'
                    'Check-out: October 22, 2026\\n'
                    'Property: Synthetic Harbor Hotel\\n',
                    encoding='utf-8',
                )
                provider = server.identify_email_provider(
                    sender='booking@trip.com',
                    subject='Your Trip.com hotel reservation is confirmed',
                    body='Synthetic Harbor Hotel',
                )
                self.assertEqual(provider['provider'], 'trip.com')
                trip = server.create_trip_from_document(
                    'trip-com.txt', path, source='Email',
                    ai_override={
                        'type': 'hotel', 'status': 'confirmed',
                        'hotel': 'Synthetic Harbor Hotel', 'city': 'Batumi',
                        'country': 'Georgia',
                        'check_in': 'October 21, 2026',
                        'check_out': 'October 22, 2026',
                    },
                )
                saved = json.loads(server.TRIPS.read_text())
                self.assertEqual(len(saved), 1)
                self.assertEqual(trip['id'], 'trip-existing')
                self.assertEqual(saved[0]['hotels'][0]['name'], 'Synthetic Harbor Hotel')
                self.assertEqual(saved[0]['hotels'][0]['start'], '2026-10-21')
                self.assertIn('trip-com.txt', saved[0]['documents'])
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old_data, old_trips, old_uploads

    def test_hotel_whose_dates_overlap_existing_trip_is_attached_and_trip_window_expands(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_data, old_trips, old_uploads = server.DATA, server.TRIPS, server.UPLOADS
            try:
                server.DATA = root / 'events.json'; server.TRIPS = root / 'trips.json'; server.UPLOADS = root / 'uploads'
                existing = {
                    'id': 'italy-trip', 'title': 'איטליה · Courmayeur',
                    'start': '2026-12-11', 'end': '2026-12-12', 'days': 2,
                    'source': 'Email', 'documents': ['courmayeur.pdf'],
                    'document_titles': {'courmayeur.pdf': 'אישור מלון'},
                    'destinations': [{'city': 'Courmayeur', 'country': 'Italy'}],
                    'hotels': [{'name': 'Grand Hotel Courmayeur', 'start': '2026-12-11', 'end': '2026-12-12'}],
                    'flights': [], 'trains': [], 'attractions': [], 'rentals': [], 'restaurants': [],
                }
                server.save_trips([existing]); server.save_events([])
                path = root / 'milan-hotel.txt'
                path.write_text('NH Collection Milano. Check-in: December 10, 2026. Check-out: December 13, 2026.', encoding='utf-8')
                result = server.create_trip_from_document(
                    'milan-hotel.txt', path, source='Email',
                    ai_override={'type': 'hotel', 'status': 'confirmed', 'hotel': 'NH Collection Milano', 'city': 'Milan', 'country': 'Italy', 'check_in': '2026-12-10', 'check_out': '2026-12-13'},
                )
                saved = json.loads(server.TRIPS.read_text())
                self.assertEqual(len(saved), 1)
                self.assertEqual(result['id'], 'italy-trip')
                self.assertEqual((saved[0]['start'], saved[0]['end']), ('2026-12-10', '2026-12-13'))
                self.assertIn('NH Collection Milano', [hotel['name'] for hotel in saved[0]['hotels']])
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old_data, old_trips, old_uploads

    def test_hotel_overlapping_trip_in_another_country_creates_separate_trip(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_data, old_trips, old_uploads = server.DATA, server.TRIPS, server.UPLOADS
            try:
                server.DATA = root / 'events.json'; server.TRIPS = root / 'trips.json'; server.UPLOADS = root / 'uploads'
                existing = {'id': 'other-trip', 'title': 'גיאורגיה · באטומי', 'start': '2026-12-11', 'end': '2026-12-12', 'days': 2, 'documents': [], 'destinations': [{'city': 'Batumi', 'country': 'Georgia'}], 'hotels': [], 'flights': [], 'trains': [], 'attractions': [], 'rentals': [], 'restaurants': []}
                server.save_trips([existing]); server.save_events([])
                path = root / 'milan-hotel.txt'
                path.write_text('NH Collection Milano. Check-in: December 10, 2026. Check-out: December 13, 2026.', encoding='utf-8')
                result = server.create_trip_from_document('milan-hotel.txt', path, source='Email', ai_override={'type': 'hotel', 'status': 'confirmed', 'hotel': 'NH Collection Milano', 'city': 'Milan', 'country': 'Italy', 'check_in': '2026-12-10', 'check_out': '2026-12-13'})
                self.assertNotEqual(result['id'], 'other-trip')
                self.assertEqual(len(json.loads(server.TRIPS.read_text())), 2)
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old_data, old_trips, old_uploads

    def test_hotel_documents_within_fourteen_days_are_not_broadly_merged(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_data, old_trips, old_uploads = server.DATA, server.TRIPS, server.UPLOADS
            try:
                server.DATA = root / 'events.json'; server.TRIPS = root / 'trips.json'; server.UPLOADS = root / 'uploads'
                server.save_trips([]); server.save_events([])
                first = root / 'one.txt'; first.write_text('Hotel One 2026-10-10 2026-10-12 Zurich')
                second = root / 'two.txt'; second.write_text('Hotel Two 2026-10-20 2026-10-22 Munich')
                first_ai = {'type': 'hotel', 'hotel': 'Hotel One', 'city': 'Zurich', 'country': 'Switzerland', 'check_in': '2026-10-10', 'check_out': '2026-10-12'}
                second_ai = {'type': 'hotel', 'hotel': 'Hotel Two', 'city': 'Munich', 'country': 'Germany', 'check_in': '2026-10-20', 'check_out': '2026-10-22'}
                server.create_trip_from_document('one.txt', first, 'Email', ai_override=first_ai)
                server.create_trip_from_document('two.txt', second, 'Email', ai_override=second_ai)
                self.assertEqual(len(json.loads(server.TRIPS.read_text())), 2)
            finally:
                server.DATA, server.TRIPS, server.UPLOADS = old_data, old_trips, old_uploads


if __name__ == '__main__':
    unittest.main()
