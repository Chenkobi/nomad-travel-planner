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
const cases = [[51, 20, false], [53, 20, false], [55, 50, false], [51, 51, true], [61, 0, true], [2, 51, true], [2, 50, false], [3, 20, false], [71, 99, false]];
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
        script = "const WEATHER_CACHE_TTL_MS=15*60*1000,weatherCache={},weatherLocationCache={},weatherRequests={}; " + weather_functions + """
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

    def test_weather_cache_expires_and_fetches_updated_forecast(self):
        start = INDEX.index("function forecastDateIsAvailable(")
        end = INDEX.index("async function updateWeather(", start)
        weather_functions = INDEX[start:end]
        script = "const WEATHER_CACHE_TTL_MS=15*60*1000,weatherCache={},weatherLocationCache={},weatherRequests={};" + weather_functions + """
const RealDate=Date;
let currentTime=RealDate.parse('2026-10-17T12:00:00Z');
global.Date=class extends RealDate{constructor(...args){super(...(args.length?args:[currentTime]))}static now(){return currentTime}};
let requestCount=0;
global.fetch=async()=>{requestCount++;const code=requestCount===1?0:3;return {ok:true,json:async()=>({daily:{weather_code:[code],temperature_2m_max:[23+requestCount],temperature_2m_min:[18],precipitation_probability_max:[5+requestCount]}})}};
(async()=>{const stop={city:'Batumi',country:'Georgia',lat:41.64077,lon:41.6306,tz:'Asia/Tbilisi'};const first=await loadWeather(stop,'2026-10-17');const cached=await loadWeather(stop,'2026-10-17');if(first.code!==0||cached.code!==0||requestCount!==1)throw new Error('fresh cache should avoid a second provider request');currentTime+=WEATHER_CACHE_TTL_MS+1;const refreshed=await loadWeather(stop,'2026-10-17');if(requestCount!==2||refreshed.code!==3||refreshed.max!==25)throw new Error('expired cache did not return the updated forecast: '+JSON.stringify({requestCount,refreshed}))})().catch(error=>{console.error(error);process.exit(1)});
"""
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
        self.assertIn("setInterval(refreshSelectedWeather,WEATHER_CACHE_TTL_MS)", INDEX)
        self.assertIn("document.addEventListener('visibilitychange'", INDEX)

    def test_trip_calendar_includes_flight_departure_and_arrival_dates(self):
        self.run_frontend_function(
            "tripCalendarRange",
            """
function localISODate(){return '2026-10-17';}
const [start,end]=tripCalendarRange({start:'2026-11-10',end:'2026-11-12',flights:[{segments:[{schedule_departure_date:'2026-11-09',schedule_arrival_date:'2026-11-13'}]}]});
if(start!=='2026-11-09'||end!=='2026-11-13')throw new Error(JSON.stringify({start,end}));
const [flightOnlyStart,flightOnlyEnd]=tripCalendarRange({flights:[{departure_date:'2026-11-09',arrival_date:'2026-11-10'}]});
if(flightOnlyStart!=='2026-11-09'||flightOnlyEnd!=='2026-11-10')throw new Error(JSON.stringify({flightOnlyStart,flightOnlyEnd}));
""",
        )
        self.assertIn("const bounds=tripCalendarRange(t)", INDEX)

    def test_flight_schedule_renders_departure_and_landing_rows(self):
        start = INDEX.index("function eventDate(")
        end = INDEX.index("function render()", start)
        schedule_functions = INDEX[start:end]
        script = "const events=[];let activeTrip={flights:[{flight_number:'SK123',airline:'Example Air',origin:'TLV',destination:'BUS',departure_date:'2026-11-09',departure_time:'06:20',arrival_date:'2026-11-10',arrival_time:'09:15'}]};" + schedule_functions + """
const rows=displayEvents().filter(event=>event[4]==='טיסה');
if(rows.length!==2)throw new Error('expected departure and landing rows: '+JSON.stringify(rows));
const departure=rows.find(event=>event[2].includes('המראה'));
const landing=rows.find(event=>event[2].includes('נחיתה'));
if(!departure||departure[0]!=='06:20'||departure[6]!=='2026-11-09'||!departure[3].includes('2026-11-09'))throw new Error('departure missing or misplaced: '+JSON.stringify(departure));
if(!landing||landing[0]!=='09:15'||landing[6]!=='2026-11-10'||!landing[3].includes('2026-11-10'))throw new Error('landing missing or misplaced: '+JSON.stringify(landing));
"""
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    def test_flight_schedule_edit_targets_departure_and_arrival_fields(self):
        start = INDEX.index("function derivedTarget(e)")
        end = INDEX.index("async function saveTypedTripChange(", start)
        target_function = INDEX[start:end]
        script = "function eventDate(event){return event?.[6]||null;}let activeTrip={flights:[{number:'SK123',departure_date:'2026-11-09',arrival_date:'2026-11-10'}]};" + target_function + """
const departure=derivedTarget(['06:20','✈️','המראה · SK123','TLV → BUS','טיסה','PDF','2026-11-09']);
const landing=derivedTarget(['09:15','🛬','נחיתה · SK123','BUS · נחיתה 09:15','טיסה','PDF','2026-11-10']);
if(!departure||departure.field!=='schedule_departure_time'||departure.dateField!=='schedule_departure_date')throw new Error('departure edit target is wrong: '+JSON.stringify(departure));
if(!landing||landing.field!=='schedule_arrival_time'||landing.dateField!=='schedule_arrival_date'||landing.hiddenField!=='schedule_arrival_hidden')throw new Error('arrival edit target is wrong: '+JSON.stringify(landing));
"""
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    def test_legacy_flight_event_is_preserved_and_deduplicated_only_when_matched(self):
        start = INDEX.index("function eventDate(")
        end = INDEX.index("function render()", start)
        schedule_functions = INDEX[start:end]
        script = "let events=[];let activeTrip={start:'2026-11-10',end:'2026-11-12',flights:[{flight_number:'SK123',airline:'Example Air',origin:'TLV',destination:'BUS',departure_date:'2026-11-09',departure_time:'06:20',arrival_date:'2026-11-10',arrival_time:'09:15'}]};" + schedule_functions + """
const matching=['06:20','✈️','טיסה Example Air SK123','TLV → BUS','טיסה','PDF','2026-11-09'];
const unmatched=['07:00','✈️','טיסה Other Air OA321','ATH → BUS','טיסה','PDF','2026-11-10'];
events=[matching,unmatched];
const rows=displayEvents().filter(event=>event[4]==='טיסה');
if(rows.length!==3||rows.filter(event=>event[2].includes('המראה')).length!==1)throw new Error('expected departure/landing rows plus one unrelated legacy flight: '+JSON.stringify(rows));
if(!rows.some(event=>event[2]==='טיסה Other Air OA321'&&event[6]==='2026-11-10'))throw new Error('unmatched flight event was dropped: '+JSON.stringify(rows));
if(rows.some(event=>event[2]==='טיסה Example Air SK123'))throw new Error('duplicate base flight was not removed: '+JSON.stringify(rows));
"""
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    def test_batumi_uses_its_own_weather_location(self):
        self.run_frontend_function(
            "weatherLocationForCity",
            """
const english=weatherLocationForCity('Batumi');
const hebrew=weatherLocationForCity('באטומי');
for(const location of [english,hebrew])if(!location||location[0]!==41.64077||location[1]!==41.6306||location[2]!=='Asia/Tbilisi')throw new Error(JSON.stringify(location));
""",
        )

    def test_unknown_destination_is_geocoded_before_weather_fetch(self):
        start = INDEX.index("function forecastDateIsAvailable(")
        end = INDEX.index("async function updateWeather(", start)
        weather_functions = INDEX[start:end]
        script = "const WEATHER_CACHE_TTL_MS=15*60*1000,weatherCache={},weatherLocationCache={},weatherRequests={}; " + weather_functions + """
const RealDate=Date;
global.Date=class extends RealDate{constructor(...args){super(...(args.length?args:['2026-10-17T12:00:00Z']))}static now(){return RealDate.parse('2026-10-17T12:00:00Z')}};
const requested=[];
global.fetch=async url=>{const target=String(url);requested.push(target);if(target.includes('geocoding-api.open-meteo.com'))return {ok:true,json:async()=>({results:[{name:'Batumi',country:'Georgia',latitude:41.64077,longitude:41.6306,timezone:'Asia/Tbilisi'}]})};return {ok:true,json:async()=>({daily:{weather_code:[2],temperature_2m_max:[27],temperature_2m_min:[19],precipitation_probability_max:[15]}})}};
(async()=>{const stop={city:'Batumi',country:'Georgia',lat:0,lon:0,tz:'Europe/Berlin'};const result=await loadWeather(stop,'2026-11-01');if(result.max!==27)throw new Error('forecast missing');if(stop.lat!==41.64077||stop.lon!==41.6306||stop.tz!=='Asia/Tbilisi')throw new Error('resolved location not applied: '+JSON.stringify(stop));if(requested.length!==2||!requested[0].includes('geocoding-api.open-meteo.com')||!requested[1].includes('latitude=41.64077')||!requested[1].includes('longitude=41.6306')||!requested[1].includes('timezone=Asia%2FTbilisi'))throw new Error('forecast did not use geocoded coordinates: '+JSON.stringify(requested))})().catch(error=>{console.error(error);process.exit(1)});
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
        self.assertIn("f.schedule_arrival_date||f.arrival_date||departureDate", INDEX)
        self.assertIn("flightScheduleEvents(segment)", INDEX)
        self.assertIn("tripCalendarRange(t)", INDEX)
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
        self.assertIn("container_status={{.State.Status}} exit_code={{.State.ExitCode}}", workflow)
        self.assertNotIn("docker logs --tail", workflow)
        for marker in ("openTravelMode", "travelModeModal", "data-travel-nav", "event-icon", "assets/rain-window.jpg", "opacity:.7", "background-color:#000!important", "hero.classList.toggle", "image/jpeg", "rain-glass-overlay", "liquid-glass-control", "backdrop-filter:blur(24px)", "rainModeEnabled(code,rainProbability)", "raining=[61,63,65,66,67,80,81,82,95,96,99]", "forecastDateIsAvailable(date,today)", "start_date=${date}&end_date=${date}", "temperatureFeeling(w.max)", "תחזית עדיין לא זמינה", "function tripCalendarRange(trip)", "tripCalendarRange(t)", "function flightScheduleEvents(f)", "flightScheduleEvents(segment)", "function flightRecordMatchesEvent(f,event)", "schedule_arrival_date", "schedule_arrival_time", "schedule_arrival_hidden", "המראה", "נחיתה", "function weatherLocationForCity(city)", "41.64077,41.6306", "Asia/Tbilisi", "async function resolveWeatherLocation(stop)", "geocoding-api.open-meteo.com", "weatherLocationCache", "WEATHER_CACHE_TTL_MS=15*60*1000", "cached.fetchedAt<WEATHER_CACHE_TTL_MS", "setInterval(refreshSelectedWeather,WEATHER_CACHE_TTL_MS)", "document.addEventListener('visibilitychange'", "#168bff", "#8a9299"):
            self.assertTrue(marker in workflow, f"deployment read-back is missing marker: {marker}")

if __name__ == "__main__":
    unittest.main()
