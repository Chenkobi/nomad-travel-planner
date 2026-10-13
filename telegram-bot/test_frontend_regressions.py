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
        self.assertIn("/api/push/test", INDEX)
        self.assertIn("באייפון צריך להוסיף את TRIPY למסך הבית", INDEX)

    def test_review_center_allows_approval_and_edits(self):
        self.assertIn('id="reviewModal"', INDEX)
        self.assertIn('id="approveReview"', INDEX)
        self.assertIn("/api/intake/review", INDEX)
        self.assertIn("מרכז אישורים", INDEX)

    def test_booking_events_use_schedule_overrides_not_source_deletion(self):
        self.assertIn("schedule_checkin_time", INDEX)
        self.assertIn("schedule_checkin_hidden", INDEX)
        self.assertIn("saveTypedTripChange(target,{[target.hiddenField]:true})", INDEX)

    def test_currency_editor_is_inside_converter_and_selects_input(self):
        self.assertIn('id="currencyEditor"', INDEX)
        self.assertIn('id="currencyEditorSelect"', INDEX)
        self.assertIn("input.addEventListener('focus',()=>input.select())", INDEX)
        self.assertIn("data-currency-up", INDEX)

    def test_flight_date_fallbacks_are_rendered(self):
        self.assertIn("f.schedule_departure_date||f.departure_date||f.date||f.start", INDEX)
        self.assertIn("f.flight_number", INDEX)

    def test_remote_trip_sync_does_not_overwrite_local_trips_when_server_is_empty(self):
        self.assertIn("data.trips.length===0&&localSnapshot.length", INDEX)
        self.assertIn("/api/trips',{method:'POST'", INDEX)

    def test_server_trip_creation_endpoint_is_present(self):
        server = (ROOT / "telegram-bot" / "server.py").read_text(encoding="utf-8")
        self.assertIn('if self.path == "/api/trips":', server)
        self.assertIn('trip_title_required', server)

if __name__ == "__main__":
    unittest.main()
