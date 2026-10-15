import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import server


class EmailIngestionTests(unittest.TestCase):
    def _configure_temp_email_dir(self, tmp):
        old_dir, old_index = server.EMAIL_DIR, server.EMAIL_INDEX
        server.EMAIL_DIR = Path(tmp)
        server.EMAIL_INDEX = Path(tmp) / 'index.json'
        return old_dir, old_index

    def test_incoming_email_persists_provider_identity(self):
        message = EmailMessage()
        message['From'] = 'confirmation@booking.com'
        message['Subject'] = 'Your reservation confirmation'
        message.set_content('Hotel reservation confirmation')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            try:
                record, duplicate = server.store_incoming_email(message.as_bytes())
                self.assertFalse(duplicate)
                self.assertEqual(record['provider'], 'booking.com')
                self.assertEqual(record['provider_category_hint'], 'marketplace')
                self.assertGreaterEqual(record['provider_confidence'], 0.9)
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_incoming_email_uses_attachment_filename_for_provider_identity(self):
        message = EmailMessage()
        message['From'] = 'forwarder@kobichen.com'
        message['Received'] = 'from mail.sixt.com by intake.example'
        message['Subject'] = 'Booking details'
        message.set_content('See attached confirmation.')
        message.add_attachment(b'%PDF-1.4 test', maintype='application', subtype='pdf', filename='sixt-reservation.pdf')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            try:
                record, duplicate = server.store_incoming_email(message.as_bytes())
                self.assertFalse(duplicate)
                self.assertEqual(record['provider'], 'sixt')
                self.assertEqual(record['provider_category_hint'], 'car_rental')
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_incoming_email_uses_html_attachment_for_provider_identity(self):
        message = EmailMessage()
        message['From'] = 'forwarder@kobichen.com'
        message['Subject'] = 'Reservation details'
        message.set_content('See attached document.')
        message.add_attachment(
            b'<html><body>Marriott Bonvoy Reservation Confirmation</body></html>',
            maintype='text', subtype='html', filename='reservation.html'
        )
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            try:
                record, duplicate = server.store_incoming_email(message.as_bytes())
                self.assertFalse(duplicate)
                self.assertEqual(record['provider'], 'marriott')
                self.assertEqual(record['provider_category_hint'], 'hotel')
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_html_email_is_converted_to_source_text(self):
        message = EmailMessage()
        message['Subject'] = 'Reservation confirmation'
        message.set_content('<html><body><h1>Sheraton Batumi Hotel</h1><p>Check-in: October 20, 2026</p><p>Check-out: October 23, 2026</p></body></html>', subtype='html')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            try:
                record, duplicate = server.store_incoming_email(message.as_bytes())
                self.assertFalse(duplicate)
                source = server.prepare_email_source(record)
                self.assertIsNotNone(source)
                filename, path = source
                self.assertTrue(filename.endswith('.txt'))
                text = path.read_text(encoding='utf-8')
                self.assertIn('Sheraton Batumi Hotel', text)
                self.assertIn('October 20, 2026', text)
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_pdf_attachment_is_supported_by_filename_when_mime_is_generic(self):
        message = EmailMessage()
        message['Subject'] = 'Hotel reservation confirmation'
        message.set_content('See attached confirmation.')
        message.add_attachment(b'%PDF-1.4 test', maintype='application', subtype='octet-stream', filename='confirmation.pdf')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            try:
                record, duplicate = server.store_incoming_email(message.as_bytes())
                self.assertFalse(duplicate)
                source = server.prepare_email_source(record)
                self.assertIsNotNone(source)
                self.assertTrue(source[0].endswith('confirmation.pdf'))
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_attachment_context_aggregates_additional_html_sources(self):
        message = EmailMessage()
        message['Subject'] = 'Train booking confirmation'
        message.set_content('See attached documents.')
        message.add_attachment(b'%PDF-1.4 primary', maintype='application', subtype='pdf', filename='ticket-confirmation.pdf')
        html = '<html><body><p>Passenger train ICE 701</p><p>Berlin to Munich</p></body></html>'
        message.add_attachment(html.encode(), maintype='text', subtype='html', filename='booking-details.html')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            try:
                record, duplicate = server.store_incoming_email(message.as_bytes())
                self.assertFalse(duplicate)
                candidates = server.email_attachment_candidates(record)
                context = server.email_attachment_text_context(record, exclude_path=candidates[0][0])
                self.assertIn('Passenger train ICE 701', context)
                self.assertIn('Berlin to Munich', context)
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index
    def test_relevant_attachment_is_selected_over_logo_when_multiple_exist(self):
        message = EmailMessage()
        message['Subject'] = 'Car rental confirmation'
        message.set_content('See attached booking.')
        message.add_attachment(b'logo', maintype='image', subtype='png', filename='company-logo.png')
        message.add_attachment(b'%PDF-1.4 rental', maintype='application', subtype='pdf', filename='rental-confirmation.pdf')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            old_uploads = server.UPLOADS
            try:
                server.UPLOADS = Path(tmp) / 'uploads'
                record, duplicate = server.store_incoming_email(message.as_bytes())
                self.assertFalse(duplicate)
                source = server.prepare_email_source(record)
                self.assertIsNotNone(source)
                self.assertTrue(source[0].endswith('rental-confirmation.pdf'))
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX, server.UPLOADS = old_dir, old_index, old_uploads
    def test_processing_claim_is_atomic_for_concurrent_delivery(self):
        message = EmailMessage()
        message['Message-ID'] = '<concurrent@example.test>'
        message['Subject'] = 'Hotel reservation confirmation'
        message.set_content('Reservation confirmation')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            try:
                record, duplicate = server.store_incoming_email(message.as_bytes())
                self.assertFalse(duplicate)
                barrier = threading.Barrier(2)
                claims = []

                def claim():
                    barrier.wait()
                    claims.append(server.claim_email_for_processing(record['id']))

                threads = [threading.Thread(target=claim) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

                self.assertEqual(sum(claim is not None for claim in claims), 1)
                self.assertEqual(server.load_email_index()[0]['status'], 'processing')
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_stale_processing_claim_can_be_recovered_after_crash(self):
        message = EmailMessage()
        message['Subject'] = 'Hotel reservation confirmation'
        message.set_content('Reservation confirmation')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            try:
                record, _ = server.store_incoming_email(message.as_bytes())
                first_now = datetime(2026, 10, 7, tzinfo=timezone.utc)
                self.assertIsNotNone(server.claim_email_for_processing(record['id'], now=first_now, lease_seconds=60))
                self.assertIsNone(server.claim_email_for_processing(record['id'], now=first_now + timedelta(seconds=30), lease_seconds=60))
                recovered = server.claim_email_for_processing(record['id'], now=first_now + timedelta(seconds=61), lease_seconds=60)
                self.assertIsNotNone(recovered)
                self.assertEqual(recovered['processing_attempts'], 2)
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_stage_metadata_is_bounded_and_redacts_sensitive_error_text(self):
        message = EmailMessage()
        message['Subject'] = 'Hotel reservation confirmation'
        message.set_content('Reservation confirmation')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            try:
                record, _ = server.store_incoming_email(message.as_bytes())
                server.claim_email_for_processing(record['id'])
                updated = server.update_email_stage(
                    record['id'], 'extracting',
                    error='token=secret-value password:super-secret ' + ('x' * 1000),
                )
                self.assertEqual(updated['processing_stage'], 'extracting')
                self.assertLessEqual(len(updated['processing_error']), server.MAX_PROCESSING_ERROR)
                self.assertNotIn('secret-value', updated['processing_error'])
                self.assertNotIn('super-secret', updated['processing_error'])
                self.assertLessEqual(len(updated['processing_stage_history']), server.MAX_STAGE_HISTORY)
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_extraction_telemetry_is_bounded_and_secret_safe(self):
        message = EmailMessage()
        message['Subject'] = 'Hotel reservation confirmation'
        message.set_content('Reservation for Passenger Name, phone +1 555 123 4567')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            try:
                record, _ = server.store_incoming_email(message.as_bytes())
                updated = server.record_extraction_attempt(
                    record['id'],
                    source_filename='confirmation-passenger-name.pdf',
                    source_kind='attachment',
                    mime_type='application/pdf',
                    byte_size=1234,
                    extracted_text_length=456,
                    provider='gemini',
                    model='gemini-test',
                    outcome='success',
                    status='processed',
                    retry_count=2,
                    normalized_type='hotel',
                    normalized_status='confirmed',
                    validation_error_code='',
                    validation_error_field='',
                    persistence_outcome='created',
                )
                telemetry = updated['extraction_telemetry']
                self.assertEqual(len(telemetry), 1)
                self.assertEqual(telemetry[0]['source_filename'], 'attachment.pdf')
                self.assertEqual(telemetry[0]['source_kind'], 'attachment')
                self.assertEqual(telemetry[0]['byte_size'], 1234)
                self.assertEqual(telemetry[0]['extracted_text_length'], 456)
                self.assertEqual(telemetry[0]['normalized_type'], 'hotel')
                self.assertEqual(telemetry[0]['persistence_outcome'], 'created')
                serialized = json.dumps(telemetry)
                self.assertNotIn('Passenger Name', serialized)
                self.assertNotIn('+1 555 123 4567', serialized)
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_extraction_telemetry_history_is_capped(self):
        message = EmailMessage()
        message['Subject'] = 'Reservation confirmation'
        message.set_content('Reservation confirmation')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            try:
                record, _ = server.store_incoming_email(message.as_bytes())
                for attempt in range(server.MAX_EXTRACTION_ATTEMPTS + 3):
                    server.record_extraction_attempt(
                        record['id'], source_filename='source.pdf', source_kind='attachment',
                        mime_type='application/pdf', byte_size=attempt,
                        extracted_text_length=0, provider='gemini', model='test',
                        outcome='failure', status='needs_review', retry_count=attempt,
                        normalized_type='other', normalized_status='unknown',
                        validation_error_code='provider_unavailable',
                        validation_error_field='', persistence_outcome='not_persisted',
                    )
                saved = server.load_email_index()[0]['extraction_telemetry']
                self.assertEqual(len(saved), server.MAX_EXTRACTION_ATTEMPTS)
                self.assertEqual(saved[0]['byte_size'], 3)
                self.assertEqual(saved[-1]['byte_size'], server.MAX_EXTRACTION_ATTEMPTS + 2)
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_process_email_persists_normalized_extraction_telemetry(self):
        message = EmailMessage()
        message['Subject'] = 'Hotel reservation confirmation'
        message.set_content('Reservation confirmation')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            old_trips, old_uploads = server.TRIPS, server.UPLOADS
            try:
                server.TRIPS = Path(tmp) / 'trips.json'
                server.UPLOADS = Path(tmp) / 'uploads'
                record, _ = server.store_incoming_email(message.as_bytes())
                ai = {'type': 'hotel', 'status': 'confirmed', 'hotel': 'Test Hotel', 'city': 'Paris', 'country': 'France', 'check_in': '2026-10-20', 'check_out': '2026-10-23'}
                with patch.object(server, 'gemini_extract', return_value=ai), patch.object(server, 'send'):
                    server.process_incoming_email(record)
                telemetry = server.load_email_index()[0]['extraction_telemetry'][-1]
                self.assertEqual(telemetry['provider'], 'gemini')
                self.assertEqual(telemetry['model'], server.GEMINI_MODEL)
                self.assertEqual(telemetry['outcome'], 'success')
                self.assertEqual(telemetry['normalized_type'], 'hotel')
                self.assertEqual(telemetry['normalized_status'], 'confirmed')
                self.assertEqual(telemetry['persistence_outcome'], 'created')
                self.assertGreaterEqual(telemetry['byte_size'], 0)
                self.assertGreaterEqual(telemetry['extracted_text_length'], 0)
            finally:
                server.TRIPS, server.UPLOADS = old_trips, old_uploads
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_concurrent_processing_delivers_one_mutation(self):
        message = EmailMessage()
        message['Message-ID'] = '<process-concurrent@example.test>'
        message['Subject'] = 'Hotel reservation confirmation'
        message.set_content('Reservation confirmation')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            old_trips, old_uploads = server.TRIPS, server.UPLOADS
            try:
                server.TRIPS = Path(tmp) / 'trips.json'
                server.UPLOADS = Path(tmp) / 'uploads'
                record, _ = server.store_incoming_email(message.as_bytes())
                ai = {'type': 'hotel', 'status': 'confirmed', 'hotel': 'Test Hotel', 'city': 'Paris', 'country': 'France', 'check_in': '2026-10-20', 'check_out': '2026-10-23'}
                with patch.object(server, 'gemini_extract', return_value=ai), patch.object(server, 'send'), patch.object(server, 'create_trip_from_document', wraps=server.create_trip_from_document) as create:
                    threads = [threading.Thread(target=server.process_incoming_email, args=(record,)) for _ in range(2)]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join()
                self.assertEqual(create.call_count, 1)
                self.assertEqual(len(server.load_trips()), 1)
                self.assertEqual(server.load_email_index()[0]['status'], 'processed')
            finally:
                server.TRIPS, server.UPLOADS = old_trips, old_uploads
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_replaying_processed_email_does_not_create_second_mutation(self):
        message = EmailMessage()
        message['Subject'] = 'Hotel reservation confirmation'
        message.set_content('Reservation confirmation')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            old_trips, old_uploads = server.TRIPS, server.UPLOADS
            try:
                server.TRIPS = Path(tmp) / 'trips.json'
                server.UPLOADS = Path(tmp) / 'uploads'
                record, _ = server.store_incoming_email(message.as_bytes())
                ai = {'type': 'hotel', 'status': 'confirmed', 'hotel': 'Test Hotel', 'city': 'Paris', 'country': 'France', 'check_in': '2026-10-20', 'check_out': '2026-10-23'}
                with patch.object(server, 'gemini_extract', return_value=ai), patch.object(server, 'send'):
                    server.process_incoming_email(record)
                    first_trips = server.load_trips()
                    server.process_incoming_email(server.load_email_index()[0])
                self.assertEqual(len(server.load_trips()), len(first_trips))
                self.assertEqual(server.load_email_index()[0]['status'], 'processed')
            finally:
                server.TRIPS, server.UPLOADS = old_trips, old_uploads
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_approved_modification_replaces_unique_original_booking(self):
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            old_trips = server.TRIPS
            try:
                server.TRIPS = Path(tmp) / 'trips.json'
                server.save_email_index([{
                    'id': 'email-mod-1', 'status': 'needs_review', 'trip_id': 'trip-1',
                    'document': 'modified-hotel.pdf', 'ingested_type': 'hotel',
                }])
                server.save_trips([{
                    'id': 'trip-1', 'hotels': [{
                        'name': 'Hotel One', 'start': '2026-10-10', 'end': '2026-10-12',
                        'supplier': 'Example Travel', 'confirmation_number': 'ABC123',
                        'document': 'modified-hotel.pdf', 'status': 'confirmed',
                    }], 'flights': [], 'trains': [], 'attractions': [], 'rentals': [],
                }])
                updated = server.apply_review_decision('email-mod-1', 'approve', {
                    'type': 'hotel', 'date': '2026-10-11', 'supplier': 'Example Travel',
                })
                hotels = server.load_trips()[0]['hotels']
                self.assertEqual(updated['status'], 'processed')
                self.assertEqual(hotels[0]['status'], 'superseded')
                self.assertEqual(hotels[1]['start'], '2026-10-11')
                self.assertEqual(hotels[1]['status'], 'confirmed')
            finally:
                server.TRIPS = old_trips
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_email_body_is_supplemental_context_when_pdf_is_primary_source(self):
        message = EmailMessage()
        message['From'] = 'confirmation@booking.com'
        message['Subject'] = 'Reservation confirmation'
        message.set_content('Booking.com reservation confirmation\\nProperty: Test Hotel\\nCheck-in: 2026-10-20\\nCheck-out: 2026-10-23')
        message.add_attachment(b'%PDF-1.4 test', maintype='application', subtype='pdf', filename='confirmation.pdf')
        with TemporaryDirectory() as tmp:
            old_dir, old_index = self._configure_temp_email_dir(tmp)
            old_trips, old_uploads = server.TRIPS, server.UPLOADS
            try:
                server.TRIPS = Path(tmp) / 'trips.json'
                server.UPLOADS = Path(tmp) / 'uploads'
                record, _ = server.store_incoming_email(message.as_bytes())
                captured = {}
                ai = {'type': 'hotel', 'status': 'confirmed', 'hotel': 'Test Hotel', 'city': 'Paris', 'country': 'France', 'check_in': '2026-10-20', 'check_out': '2026-10-23'}
                def extract(path, mime_type=None, supplemental_text=''):
                    captured['supplemental_text'] = supplemental_text
                    return ai
                with patch.object(server, 'gemini_extract', side_effect=extract), patch.object(server, 'send'):
                    server.process_incoming_email(record)
                self.assertIn('Property: Test Hotel', captured['supplemental_text'])
                self.assertIn('Check-out: 2026-10-23', captured['supplemental_text'])
            finally:
                server.TRIPS, server.UPLOADS = old_trips, old_uploads
                server.EMAIL_DIR, server.EMAIL_INDEX = old_dir, old_index

    def test_provider_type_contradiction_cannot_be_persisted_as_processed(self):
        self.assertFalse(server.ingested_type_matches_booking('flight', {'_ingested_type': 'hotel'}))
        self.assertTrue(server.ingested_type_matches_booking('hotel', {'_ingested_type': 'hotel'}))


if __name__ == '__main__':
    unittest.main()
