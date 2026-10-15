import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")


class FrontendRegressionTests(unittest.TestCase):
    def test_trip_title_has_edit_action_and_custom_title_precedence(self):
        self.assertIn('id="editTripTitle"', INDEX)
        self.assertIn("if(t?.title_custom&&t?.title)return String(t.title)", INDEX)
        self.assertIn("title_custom:true", INDEX)
        server = (ROOT / "telegram-bot" / "server.py").read_text(encoding="utf-8")
        self.assertIn('"title_custom"', server)

    def test_share_button_is_in_a_non_overlapping_top_row(self):
        self.assertIn('class="hero-top-row"', INDEX)
        self.assertIn('.hero-share{position:static', INDEX)

    def test_booking_rows_show_source_provider_and_open_document_preview(self):
        self.assertIn("source_provider||x.supplier||x.source", INDEX)
        self.assertIn('data-open-document="${esc(x.document)}"', INDEX)
        self.assertIn("openDocumentPreview(button.dataset.openDocument)", INDEX)
        server = (ROOT / "telegram-bot" / "server.py").read_text(encoding="utf-8")
        self.assertIn('booking["source_provider"]', server)

    def test_document_preview_loads_email_body_automatically_and_embeds_pdf(self):
        self.assertIn("openDocumentSource(filename)", INDEX)
        self.assertIn("content.insertAdjacentHTML('beforeend'", INDEX)
        self.assertIn("/\\.pdf$/i.test(filename)", INDEX)
        self.assertIn("data-open-document", INDEX)

    def test_share_is_icon_only_in_hero_not_metrics(self):
        self.assertIn('class="hero-share"', INDEX)
        self.assertIn('aria-label="שיתוף הטיול"', INDEX)
        self.assertNotIn('hero-meta"><button class="primary" id="shareTripPrimary"', INDEX)

    def test_flight_delete_swipe_is_authorized_and_persistent(self):
        self.assertIn("flight-delete-affordance", INDEX)
        self.assertIn("/flights/", INDEX)
        self.assertIn("method:'DELETE'", INDEX)
        server = (ROOT / "telegram-bot" / "server.py").read_text(encoding="utf-8")
        self.assertIn("flight_not_found", server)
        self.assertIn("flights.pop(index)", server)

    def test_body_only_confirmation_has_structured_review_card(self):
        self.assertIn('id="reviewFacts"', INDEX)
        self.assertIn('id="reviewBodyPreview"', INDEX)
        self.assertIn('id="reviewRawSource"', INDEX)
        server = (ROOT / "telegram-bot" / "server.py").read_text(encoding="utf-8")
        self.assertIn('"body_preview"', server)
        self.assertIn('"raw_source"', server)

        self.assertIn("#notificationsButton", INDEX)
        self.assertIn("#notificationPanel", INDEX)
        self.assertIn("closeNotifications", INDEX)
        self.assertIn("notificationPanel.classList.add('show')", INDEX)
        self.assertIn("notificationPanel.classList.remove('show')", INDEX)
        self.assertIn("#closeNotifications", INDEX)

    def test_dark_modal_scrollbar_is_not_white(self):
        self.assertIn("scrollbar-color", INDEX)
        self.assertIn(".dark .modal", INDEX)

    def test_email_is_the_visible_intake_channel(self):
        self.assertNotIn("מתעדכן מאישורי Telegram", INDEX)
        self.assertIn("מתעדכן מאישורי מייל", INDEX)

    def test_old_day_details_section_is_removed(self):
        self.assertNotIn('<h2>פרטי היום</h2>', INDEX)
        self.assertNotIn('id="countryDetails"', INDEX)
        self.assertNotIn('id="hotelDetails"', INDEX)
        self.assertNotIn("#countryDetails", INDEX)
        self.assertNotIn("#hotelDetails", INDEX)

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

    def test_email_source_preview_is_structured_and_not_raw_only(self):
        self.assertIn("function renderEmailSource(text,filename)", INDEX)
        self.assertIn("email-source-meta", INDEX)
        self.assertIn("פתיחת המקור הגולמי", INDEX)

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

    def test_new_processed_notification_refreshes_visible_trip_data(self):
        self.assertIn("await syncRemoteTrips();await syncRemoteEvents()", INDEX)
        self.assertIn("setInterval(syncNotifications,30000)", INDEX)

    def test_server_trip_creation_endpoint_is_present(self):
        server = (ROOT / "telegram-bot" / "server.py").read_text(encoding="utf-8")
        self.assertIn('if self.path == "/api/trips":', server)
        self.assertIn('trip_title_required', server)

if __name__ == "__main__":
    unittest.main()
