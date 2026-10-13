import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")


class FrontendRegressionTests(unittest.TestCase):
    def test_notification_bell_has_visible_panel_toggle_handler(self):
        self.assertIn("#notificationsButton", INDEX)
        self.assertIn("#notificationPanel", INDEX)
        self.assertTrue("classList.toggle('show')" in INDEX or 'classList.toggle("show")' in INDEX)

    def test_dark_modal_scrollbar_is_not_white(self):
        self.assertIn("scrollbar-color", INDEX)
        self.assertIn(".dark .modal", INDEX)

    def test_email_is_the_visible_intake_channel(self):
        self.assertNotIn("מתעדכן מאישורי Telegram", INDEX)
        self.assertIn("מתעדכן מאישורי מייל", INDEX)


if __name__ == "__main__":
    unittest.main()
