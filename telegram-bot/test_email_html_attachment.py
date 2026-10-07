import unittest
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory

import server


class EmailAttachmentTests(unittest.TestCase):
    def test_html_attachment_becomes_ai_readable_text_source(self):
        message = EmailMessage()
        message['Subject'] = 'כרטיסים ושוברים להזמנה ארקיע'
        message.set_content('מצורף כרטיס הטיסה.')
        message.add_attachment(
            b'<html><body><h1>Arkia IZ 138</h1><p>Tel Aviv to Athens</p><p>December 10, 2026 08:30</p></body></html>',
            maintype='text', subtype='html', filename='res_doc13819584.html',
        )
        with TemporaryDirectory() as tmp:
            old_dir, old_index, old_uploads = server.EMAIL_DIR, server.EMAIL_INDEX, server.UPLOADS
            try:
                server.EMAIL_DIR = Path(tmp)
                server.EMAIL_INDEX = Path(tmp) / 'index.json'
                server.UPLOADS = Path(tmp) / 'uploads'
                record, duplicate = server.store_incoming_email(message.as_bytes())
                self.assertFalse(duplicate)
                source = server.prepare_email_source(record)
                self.assertIsNotNone(source)
                filename, path = source
                self.assertTrue(filename.endswith('.txt'))
                text = path.read_text(encoding='utf-8')
                self.assertIn('Arkia IZ 138', text)
                self.assertIn('December 10, 2026', text)
            finally:
                server.EMAIL_DIR, server.EMAIL_INDEX, server.UPLOADS = old_dir, old_index, old_uploads


if __name__ == '__main__':
    unittest.main()
