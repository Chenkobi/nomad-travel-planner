import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import flight_status
import reminders
import server

ROOT = Path(__file__).resolve().parents[1]


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def api_leg(status="Expected", revised=None, gate=None, origin="TLV", belt=None):
    departure = {
        "airport": {"icao": "LLBG", "iata": origin, "name": "Tel Aviv Ben Gurion", "timeZone": "Asia/Jerusalem"},
        "scheduledTime": {"utc": "2026-11-05 08:00Z", "local": "2026-11-05 10:00+02:00"},
        "terminal": "3", "quality": ["Basic", "Live"],
    }
    if revised:
        departure["revisedTime"] = {"utc": revised[0], "local": revised[1]}
    if gate:
        departure["gate"] = gate
    arrival = {"airport": {"iata": "MXP"}, "scheduledTime": {"utc": "2026-11-05 11:55Z", "local": "2026-11-05 12:55+01:00"}}
    if belt:
        arrival["baggageBelt"] = belt
    return {"number": "LY 381", "status": status, "departure": departure, "arrival": arrival,
            "airline": {"name": "El Al", "iata": "LY"}}


def milan_trip(**flight_overrides):
    flight = {"airline": "El Al", "number": "LY381", "origin": "TLV", "destination": "MXP",
              "departure_date": "2026-11-05", "departure_time": "10:00", "arrival_time": "12:55", "status": "confirmed"}
    flight.update(flight_overrides)
    return {"id": "m1", "title": "איטליה מילאנו", "flights": [flight], "hotels": []}


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def lookup(self, number, date_local):
        self.calls.append((number, date_local))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def departure_of(flight):
    return utc("2026-11-05T10:00:00+02:00")


class ParsingTests(unittest.TestCase):
    def test_flight_numbers_are_normalized_for_lookup(self):
        self.assertEqual(flight_status.normalize_flight_number("LY 001"), "LY1")
        self.assertEqual(flight_status.normalize_flight_number("iz851"), "IZ851")
        self.assertEqual(flight_status.normalize_flight_number("W6 2345"), "W62345")

    def test_api_times_with_space_and_z_suffix(self):
        self.assertEqual(flight_status.parse_api_time("2026-11-05 08:00Z"), utc("2026-11-05T08:00:00+00:00"))
        self.assertEqual(flight_status.parse_api_time({"utc": "2026-11-05 08:40Z"}), utc("2026-11-05T08:40:00+00:00"))
        self.assertIsNone(flight_status.parse_api_time("soon"))

    def test_summary_computes_delay_gate_and_local_clock(self):
        summary = flight_status.summarize(api_leg("Delayed", ("2026-11-05 08:45Z", "2026-11-05 10:45+02:00"), gate="B7"),
                                          utc("2026-11-05T05:00:00+00:00"))
        self.assertEqual(summary["delay_minutes"], 45)
        self.assertEqual(summary["gate"], "B7")
        self.assertEqual(summary["terminal"], "3")
        self.assertIn("10:45", summary["departure_estimated_local"])
        self.assertFalse(summary["cancelled"])

    def test_leg_matching_prefers_the_booked_origin(self):
        legs = [api_leg(origin="FCO"), api_leg(origin="TLV")]
        self.assertEqual(flight_status.pick_leg(legs, "Tel Aviv (TLV)")["departure"]["airport"]["iata"], "TLV")
        self.assertIsNone(flight_status.pick_leg([], "TLV"))


class ChangeMessageTests(unittest.TestCase):
    def summary(self, **kwargs):
        base = {"status": "Expected", "cancelled": False, "delay_minutes": 0, "gate": "", "terminal": "3",
                "departure_estimated_local": "2026-11-05 10:00+02:00"}
        base.update(kwargs)
        return base

    def test_delay_alerts_once_and_again_only_when_it_moves(self):
        first = flight_status.change_messages("LY381", self.summary(), self.summary(delay_minutes=40, departure_estimated_local="2026-11-05 10:40+02:00"))
        self.assertEqual(len(first), 1)
        self.assertIn("40", first[0][0])
        self.assertIn("10:40", first[0][1])
        self.assertEqual(flight_status.change_messages("LY381", self.summary(delay_minutes=40), self.summary(delay_minutes=45)), [])
        self.assertEqual(len(flight_status.change_messages("LY381", self.summary(delay_minutes=40), self.summary(delay_minutes=70))), 1)

    def test_small_delays_are_quiet(self):
        self.assertEqual(flight_status.change_messages("LY381", self.summary(), self.summary(delay_minutes=10)), [])

    def test_gate_assignment_and_change(self):
        assigned = flight_status.change_messages("LY381", self.summary(), self.summary(gate="B7"))
        self.assertIn("שער B7", assigned[0][0])
        changed = flight_status.change_messages("LY381", self.summary(gate="B7"), self.summary(gate="C2"))
        self.assertIn("שינוי שער", changed[0][0])
        self.assertIn("B7", changed[0][1])

    def test_cancellation_is_a_single_clear_message(self):
        messages = flight_status.change_messages("LY381", self.summary(gate="B7"), self.summary(status="Canceled", cancelled=True))
        self.assertEqual(len(messages), 1)
        self.assertIn("בוטלה", messages[0][0])


class ScheduleTests(unittest.TestCase):
    def test_polling_only_around_the_flight(self):
        dep = utc("2026-11-05T08:00:00+00:00")
        self.assertFalse(flight_status.next_check_due(dep, dep - timedelta(hours=30), None, False))
        self.assertTrue(flight_status.next_check_due(dep, dep - timedelta(hours=20), None, False))
        self.assertFalse(flight_status.next_check_due(dep, dep - timedelta(hours=20), dep - timedelta(hours=21), False))
        self.assertTrue(flight_status.next_check_due(dep, dep - timedelta(hours=1), dep - timedelta(minutes=80), False))
        self.assertFalse(flight_status.next_check_due(dep, dep - timedelta(hours=1), dep - timedelta(minutes=70), False))
        self.assertFalse(flight_status.next_check_due(dep, dep + timedelta(hours=2), None, True))
        self.assertFalse(flight_status.next_check_due(dep, dep + timedelta(hours=15), None, False))


class CycleTests(unittest.TestCase):
    def test_cycle_pushes_changes_respects_quota_and_survives_errors(self):
        pushes = []
        notify = lambda title, body, tag: pushes.append((title, body, tag))
        client = FakeClient([
            [api_leg()],
            OSError("network"),
            [api_leg("Delayed", ("2026-11-05 08:50Z", "2026-11-05 10:50+02:00"), gate="B7")],
        ])
        trips = [milan_trip()]
        state = flight_status.run_flight_status_cycle(trips, {}, utc("2026-11-04T12:00:00+02:00"), client, departure_of, notify, 250)
        self.assertEqual(pushes, [])
        self.assertEqual(state["calls"], 1)
        self.assertEqual(client.calls, [("LY381", "2026-11-05")])
        # not due again an hour later (4h interval the day before)
        state = flight_status.run_flight_status_cycle(trips, state, utc("2026-11-04T13:00:00+02:00"), client, departure_of, notify, 250)
        self.assertEqual(state["calls"], 1)
        state = flight_status.run_flight_status_cycle(trips, state, utc("2026-11-05T08:00:00+02:00"), client, departure_of, notify, 250)
        self.assertEqual(state["flights"]["LY381:2026-11-05"]["last_error"], "OSError")
        state = flight_status.run_flight_status_cycle(trips, state, utc("2026-11-05T08:30:00+02:00"), client, departure_of, notify, 250)
        self.assertEqual([p[0] for p in pushes], ["⏱️ LY381 מתעכבת 50 דק׳", "🚪 שער B7 לטיסה LY381"])
        self.assertTrue(pushes[0][2].startswith("tripy-flight-"))
        live = flight_status.live_status_for(state, trips[0]["flights"][0])
        self.assertEqual(live["delay_minutes"], 50)

    def test_monthly_quota_stops_calls(self):
        client = FakeClient([[api_leg()]])
        state = flight_status.run_flight_status_cycle([milan_trip()], {"month": "2026-11", "calls": 5}, utc("2026-11-05T05:00:00+02:00"),
                                                      client, departure_of, lambda *a: None, 5)
        self.assertEqual(client.calls, [])
        self.assertEqual(state["calls"], 5)

    def test_cancelled_bookings_are_not_polled(self):
        client = FakeClient([])
        flight_status.run_flight_status_cycle([milan_trip(status="cancelled")], {}, utc("2026-11-05T05:00:00+02:00"),
                                              client, departure_of, lambda *a: None, 250)
        self.assertEqual(client.calls, [])


class ClientTests(unittest.TestCase):
    def test_request_uses_rapidapi_headers_and_departure_date(self):
        seen = {}

        class Response(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["headers"] = dict(request.header_items())
            return Response(json.dumps([api_leg()]).encode())

        legs = flight_status.AeroDataBoxClient("secret-key", opener=opener).lookup("LY381", "2026-11-05")
        self.assertEqual(len(legs), 1)
        self.assertIn("aerodatabox.p.rapidapi.com/flights/number/LY381/2026-11-05", seen["url"])
        self.assertIn("dateLocalRole=Departure", seen["url"])
        self.assertEqual(seen["headers"]["X-rapidapi-key"], "secret-key")


class ReminderIntegrationTests(unittest.TestCase):
    def test_delay_moves_leave_reminder_and_cancellation_removes_reminders(self):
        resolver = reminders.TimezoneResolver(geocode=None)
        trip = milan_trip()
        delayed = {"delay_minutes": 90, "departure_estimated_utc": "2026-11-05T09:30:00+00:00"}
        plan = {r["id"]: r for r in reminders.build_reminders([trip], resolver, lambda f: delayed)}
        leave = [r for k, r in plan.items() if ":leave" in k]
        self.assertEqual(len(leave), 1)
        self.assertEqual(leave[0]["fire_at"], utc("2026-11-05T07:30:00+02:00"))
        self.assertIn("11:30", leave[0]["title"])
        self.assertIn("במקור 10:00", leave[0]["body"])
        cancelled = reminders.build_reminders([trip], resolver, lambda f: {"cancelled": True})
        self.assertFalse([r for r in cancelled if r["id"].startswith("flight:")])


class ServerIntegrationTests(unittest.TestCase):
    def test_server_cycle_persists_state_and_trips_api_carries_live_status(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            trips_file = root / "trips.json"
            trips_file.write_text(json.dumps([milan_trip()]), encoding="utf-8")
            client = FakeClient([[api_leg("Boarding", gate="B7")]])
            sent = []
            with patch.object(server, "TRIPS", trips_file), patch.object(server, "FLIGHT_STATUS_STATE", root / "fs.json"):
                state = server.run_flight_status_cycle(now=utc("2026-11-05T09:00:00+02:00"), client=client,
                                                       resolver=reminders.TimezoneResolver(geocode=None),
                                                       sender=lambda title, body, tag="": sent.append(title) or 1)
                self.assertEqual(state["calls"], 1)
                self.assertEqual(sent, ["🚪 שער B7 לטיסה LY381"])
                trips = server.attach_live_flight_status(server.load_trips())
                self.assertEqual(trips[0]["flights"][0]["live_status"]["gate"], "B7")

    def test_without_api_key_nothing_runs(self):
        with patch.object(server, "AERODATABOX_API_KEY", ""):
            self.assertIsNone(server.run_flight_status_cycle())


class DeploymentTests(unittest.TestCase):
    def test_key_comes_from_github_secret_and_module_is_in_image(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy-ugreen.yml").read_text(encoding="utf-8")
        self.assertIn('AERODATABOX_RAPIDAPI_KEY="${{ secrets.AERODATABOX_RAPIDAPI_KEY }}"', workflow)
        self.assertIn("COPY telegram-bot/flight_status.py", (ROOT / "Dockerfile").read_text(encoding="utf-8"))

    def test_schedule_shows_live_status(self):
        index = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("function flightLiveText(s)", index)
        self.assertIn("${flightLiveText(f.live_status)}", index)


if __name__ == "__main__":
    unittest.main()


class ScheduleUiTests(unittest.TestCase):
    def setUp(self):
        self.index = (ROOT / "index.html").read_text(encoding="utf-8")

    def test_edit_modal_closes_from_button_backdrop_and_escape(self):
        self.assertIn('getElementById("closeDetailModal")?.addEventListener("click"', self.index)
        self.assertIn('classList.contains("modal-back")&&back.classList.contains("show")', self.index)
        self.assertIn('e.key==="Escape"', self.index)
        self.assertIn('m.id!=="authOverlay"', self.index)

    def test_departure_row_has_time_terminal_and_airport_navigation(self):
        self.assertIn("function flightAirportPlace(place,terminal)", self.index)
        self.assertIn("TLV:'נמל התעופה בן גוריון'", self.index)
        self.assertIn("'המראה '+departureTime", self.index)
        self.assertIn("🧭 ניווט לשדה התעופה", self.index)
        self.assertIn("departureDate=f.schedule_departure_date||f.departure_date||f.date||f.start||f.schedule_arrival_date||f.arrival_date", self.index)

    def test_extraction_keeps_stated_terminals(self):
        source = (ROOT / "telegram-bot" / "server.py").read_text(encoding="utf-8")
        self.assertIn('"departure_terminal":str(segment.get("departure_terminal") or "")', source)
        self.assertIn('"departure_terminal":{"type":"string"}', source)
