import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import server


class DocumentBoundaryTests(unittest.TestCase):
    def test_safe_document_filename_strips_path_and_unsafe_characters(self):
        self.assertEqual(server.safe_document_filename("../../boarding pass?.pdf"), "boarding_pass_.pdf")

    def test_safe_document_filename_uses_fallback_for_empty_or_dot_name(self):
        self.assertEqual(server.safe_document_filename("../../", "document.bin"), "document.bin")
        self.assertEqual(server.safe_document_filename("...", "document.bin"), "document.bin")

    def test_resolve_document_path_rejects_traversal_and_external_symlinks(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "uploads"
            root.mkdir()
            outside = Path(tmp) / "secret.txt"
            outside.write_text("secret", encoding="utf-8")
            (root / "inside.txt").write_text("inside", encoding="utf-8")
            (root / "link.txt").symlink_to(outside)

            self.assertEqual(server.resolve_document_path(root, "inside.txt"), (root / "inside.txt").resolve())
            self.assertIsNone(server.resolve_document_path(root, "../secret.txt"))
            self.assertIsNone(server.resolve_document_path(root, "link.txt"))

    def test_bounded_document_bytes_rejects_oversized_content(self):
        with self.assertRaisesRegex(ValueError, "document_too_large"):
            server.bounded_document_bytes(b"12345", max_size=4)

    def test_bounded_document_bytes_preserves_content_at_limit(self):
        self.assertEqual(server.bounded_document_bytes(b"1234", max_size=4), b"1234")

    def test_upload_suffixes_are_explicitly_allowlisted(self):
        self.assertTrue(server.is_supported_upload_filename("ticket.PDF"))
        self.assertTrue(server.is_supported_upload_filename("photo.webp"))
        self.assertFalse(server.is_supported_upload_filename("booking.html"))
        self.assertFalse(server.is_supported_upload_filename("ticket.pdf.exe"))

    def test_bounded_stream_rejects_oversized_download_without_unbounded_read(self):
        with self.assertRaisesRegex(ValueError, "document_too_large"):
            server.read_bounded_stream(io.BytesIO(b"12345"), max_size=4)

    def test_bounded_stream_returns_download_at_limit(self):
        self.assertEqual(server.read_bounded_stream(io.BytesIO(b"1234"), max_size=4), b"1234")


if __name__ == "__main__":
    unittest.main()
