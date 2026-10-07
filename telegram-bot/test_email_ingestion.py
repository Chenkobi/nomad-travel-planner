import unittest
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory

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


if __name__ == '__main__':
    unittest.main()
