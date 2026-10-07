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


if __name__ == '__main__':
    unittest.main()
