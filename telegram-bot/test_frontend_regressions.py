import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")


class FrontendRegressionTests(unittest.TestCase):
    def run_frontend_function(self, name, assertions):
        function = re.search(rf"function {re.escape(name)}\([^)]*\)\{{[^{{}}]*\}}", INDEX)
        if function is None:
            self.fail(f"missing frontend helper: {name}")
            return
        subprocess.run(["node", "-e", function.group(0) + assertions], check=True, capture_output=True, text=True)

    def test_rain_mode_requires_actual_rain_or_probability_above_50(self):
        self.run_frontend_function(
            "rainModeEnabled",
            """
const cases = [[61, 0, true], [2, 51, true], [2, 50, false], [3, 20, false], [71, 99, false]];
for (const [code, probability, expected] of cases) {
  if (rainModeEnabled(code, probability) !== expected) throw new Error(JSON.stringify({code, probability, expected}));
}
""",
        )
        self.assertIn("const rainy=rainModeEnabled(code,rainProbability)", INDEX)
        self.assertIn("weatherCode===undefined&&card.dataset.weatherCode!==undefined", INDEX)
        self.assertIn("applyContextMood(stop,w.code,w.rain)", INDEX)

    def test_weather_background_is_blue_when_clear_gray_when_cloudy_and_black_at_night(self):
        self.run_frontend_function(
            "contextBackground",
            """
const cases = [[0, false, '#168bff'], [1, false, '#168bff'], [2, false, '#8a9299'], [3, false, '#8a9299'], [0, true, '#000']];
for (const [code, night, expected] of cases) {
  if (contextBackground(code, night) !== expected) throw new Error(JSON.stringify({code, night, expected}));
}
""",
        )

    def test_weather_forecast_horizon_is_limited_to_16_calendar_days(self):
        self.run_frontend_function(
            "forecastDateIsAvailable",
            """
if (!forecastDateIsAvailable('2026-11-01', '2026-10-17')) throw new Error('16th calendar day should be available');
if (forecastDateIsAvailable('2026-11-02', '2026-10-17')) throw new Error('17th calendar day should not be available');
if (forecastDateIsAvailable('2026-12-01', '2026-10-17')) throw new Error('December forecast should not be available yet');
""",
        )
        self.assertIn("תחזית עדיין לא זמינה", INDEX)
        weather_loader = INDEX.split("async function loadWeather(", 1)[1].split("async function updateWeather(", 1)[0]
        self.assertIn("const today=localDateInTimezone(stop.tz)", weather_loader)
        self.assertIn("if(date>=today&&!forecastDateIsAvailable(date,today))return {unavailable:true}", weather_loader)
        self.assertIn("&start_date=${date}&end_date=${date}", weather_loader)
        self.assertNotIn("forecast_days=", weather_loader)
        self.assertIn("if(w.unavailable)", INDEX)

    def test_load_weather_respects_forecast_horizon_before_fetching(self):
        start = INDEX.index("function forecastDateIsAvailable(")
        end = INDEX.index("async function updateWeather(", start)
        weather_functions = INDEX[start:end]
        script = "const weatherCache = {}; " + weather_functions + """
const RealDate = Date;
global.Date = class extends RealDate { constructor(...args) { super(...(args.length ? args : ['2026-10-17T12:00:00Z'])); } static now() { return RealDate.parse('2026-10-17T12:00:00Z'); } };
const requested = [];
global.fetch = async (url) => { const target=String(url); requested.push(target); return {ok:!target.includes('forecast_days='), json:async()=>({daily:{weather_code:[0],temperature_2m_max:[27],temperature_2m_min:[19],precipitation_probability_max:[15]}})}; };
(async()=>{
  const stop = {city:'Rome',tz:'Europe/Rome',lat:41.9028,lon:12.4964};
  const near = await loadWeather(stop, '2026-11-01');
  if (near.unavailable || !requested[0]?.includes('start_date=2026-11-01') || !requested[0]?.includes('end_date=2026-11-01') || requested[0]?.includes('forecast_days=')) throw new Error('single-day forecast request has incompatible parameters');
  const far = await loadWeather(stop, '2026-12-01');
  if (!far.unavailable || requested.length !== 1) throw new Error('out-of-range forecast should not call the provider');
})().catch(error=>{console.error(error);process.exit(1)});
"""
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    def test_27_degrees_is_described_as_warm_and_pleasant(self):
        self.run_frontend_function(
            "temperatureFeeling",
            "if (temperatureFeeling(27) !== 'חמים ונעים') throw new Error('27°C should feel warm and pleasant');",
        )
        self.assertIn("temperatureFeeling(w.max)", INDEX)

    def test_user_visible_dates_use_day_month_year_format(self):
        self.assertIn("function formatDisplayDate(value)", INDEX)
        self.assertIn("function formatDateText(value)", INDEX)
        self.assertIn("formatDisplayDate(t.start)", INDEX)
        self.assertIn('<bdi dir="ltr">', INDEX)
        self.assertIn("formatDateText(e[3])", INDEX)
        self.assertIn("formatDateText((x.start||'')+' – '+(x.end||''))", INDEX)
        self.assertIn("esc(formatDateText(value))", INDEX)

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
        self.assertIn('class="hero-share liquid-glass-control"', INDEX)
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

    def test_travel_mode_is_a_direct_entry_and_shows_the_next_trip_event(self):
        self.assertIn('id="openTravelMode"', INDEX)
        self.assertIn('id="travelModeModal"', INDEX)
        self.assertIn('id="travelModeNextEvent"', INDEX)
        self.assertIn('id="travelModeEventDate"', INDEX)
        self.assertIn("formatDisplayDate(next.date)", INDEX)
        self.assertIn('id="travelModeLocation"', INDEX)
        self.assertIn('id="travelModeBooking"', INDEX)
        self.assertIn('id="travelModeNavigate"', INDEX)
        self.assertIn("function renderTravelMode()", INDEX)
        self.assertIn("function openTravelMode()", INDEX)

    def test_travel_mode_upcoming_cards_render_an_icon_and_full_width_details(self):
        travel_mode = INDEX.split("function renderTravelMode(){", 1)[1].split("function closeTravelMode()", 1)[0]
        self.assertIn('class="event-icon">${esc(item.event[1]||\'📍\')}</div><div><strong>', travel_mode)
        self.assertIn('${esc(item.event[2]||\'אירוע\')}</strong><p>${esc(formatDisplayDate(item.date))}', travel_mode)

    def test_travel_mode_keeps_waze_and_offers_walking_maps(self):
        self.assertIn('data-travel-nav="google"', INDEX)
        self.assertIn('data-travel-nav="apple"', INDEX)
        self.assertIn('data-travel-nav="waze"', INDEX)
        self.assertIn("travelmode=walking", INDEX)
        self.assertIn("https://maps.apple.com/directions?destination=", INDEX)
        self.assertIn("mode=walking", INDEX)
        self.assertIn("https://waze.com/ul", INDEX)
        self.assertIn("🧭 ניווט ב-Waze", INDEX)

    def test_rain_mode_uses_real_raindrop_photo_and_liquid_glass(self):
        self.assertNotIn(".hero.weather-snow::beforecontent", INDEX)
        self.assertIn('.hero.weather-snow::before{content:"";', INDEX)
        self.assertTrue((ROOT / "assets" / "rain-window.jpg").is_file())
        self.assertIn("COPY assets/rain-window.jpg /app/assets/rain-window.jpg", (ROOT / "Dockerfile").read_text(encoding="utf-8"))
        self.assertIn("assets/rain-window.jpg", INDEX)
        self.assertIn("url('assets/rain-window.jpg')", INDEX)
        rain_css = INDEX.split("body .hero.weather-rain{", 1)[1].split("</style>", 1)[0]
        self.assertNotIn("radial-gradient", rain_css)
        self.assertIn("opacity:.7", rain_css, "the photo must be 30% transparent")
        self.assertIn("body .hero.weather-rain.context-night{background-color:#000!important", INDEX)
        self.assertIn("hero.classList.toggle('context-night',night)", INDEX)
        for marker in (".hero.weather-rain .context-card", "backdrop-filter:blur(24px)", "if(rainGlass){hero.style.removeProperty('background')", "card.style.removeProperty('background')"):
            self.assertIn(marker, INDEX, f"missing rainy Liquid Glass marker: {marker}")
        for marker in ('class="rain-glass-overlay"', 'class="pill liquid-glass-control"', "rainGlassSlide", "tripyRainDrops"):
            self.assertNotIn(marker, INDEX, f"unexpected synthetic rain marker: {marker}")

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

    def test_deploy_workflow_reads_back_travel_mode_and_liquid_glass_without_rain_overlay(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy-ugreen.yml").read_text(encoding="utf-8")
        self.assertIn("live_travel_mode_markers_verified", workflow)
        self.assertIn("unexpected synthetic rain overlay", workflow)
        for marker in ("openTravelMode", "travelModeModal", "data-travel-nav", "event-icon", "assets/rain-window.jpg", "opacity:.7", "background-color:#000!important", "hero.classList.toggle", "image/jpeg", "rain-glass-overlay", "liquid-glass-control", "backdrop-filter:blur(24px)", "rainModeEnabled(code,rainProbability)", "forecastDateIsAvailable(date,today)", "start_date=${date}&end_date=${date}", "temperatureFeeling(w.max)", "תחזית עדיין לא זמינה", "#168bff", "#8a9299"):
            self.assertTrue(marker in workflow, f"deployment read-back is missing marker: {marker}")

if __name__ == "__main__":
    unittest.main()
