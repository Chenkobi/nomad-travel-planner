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

    def test_trip_centered_navigation_has_tools_sheet_and_current_day_logic(self):
        self.assertIn('id="openTripTools"', INDEX)
        self.assertIn('id="tripToolsModal"', INDEX)
        self.assertIn("function initialDayIndex()", INDEX)
        self.assertIn("if(today>last)return 0", INDEX)
        self.assertIn("if(today===last)return days.length-1", INDEX)

    def test_currency_is_full_screen_and_direct_input(self):
        self.assertIn('class="panel hidden currency-screen"', INDEX)
        self.assertIn('id="closeCurrency"', INDEX)
        self.assertIn('הקלידו ישירות בכל שורה', INDEX)
        self.assertIn("function updateFromMoneyInput(input)", INDEX)
        self.assertIn('value="PLN"', INDEX)
        self.assertIn('value="GEL"', INDEX)

    def test_push_flow_handles_existing_subscription_and_ios(self):
        self.assertIn("navigator.serviceWorker.ready", INDEX)
        self.assertIn("getSubscription()", INDEX)
        self.assertIn("באייפון צריך להוסיף את TRIPY למסך הבית", INDEX)

if __name__ == "__main__":
    unittest.main()
