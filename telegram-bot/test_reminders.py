import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import reminders
import server


def utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def batumi_trip(**overrides):
    trip = {
        "id": "t1", "title": "גיאורגיה באטומי", "start": "2026-10-27", "end": "2026-11-02",
        "destinations": [{"city": "באטומי", "country": "גיאורגיה"}],
        "flights": [
            {"airline": "Arkia", "number": "IZ851", "origin": "TLV", "destination": "BUS",
             "departure_date": "2026-10-27", "departure_time": "06:40", "arrival_time": "09:55", "status": "confirmed"},
            {"airline": "Arkia", "number": "IZ852", "origin": "BUS", "destination": "TLV",
             "departure_date": "2026-11-02", "departure_time": "13:00", "arrival_time": "14:20", "status": "confirmed",
             "confirmation_number": "K7XQ2P"},
        ],
        "hotels": [{"name": "Hilton Batumi", "city": "Batumi", "country": "Georgia", "start": "2026-10-27",
                    "end": "2026-11-02", "checkin_time": "15:00", "checkout_time": "12:00", "status": "confirmed"}],
        "rentals": [{"pickup_date": "2026-10-28", "pickup_time": "10:00", "pickup_location": "Batumi Airport (BUS)",
                     "vehicle_type": "Toyota RAV4", "dropoff_date": "2026-11-01", "dropoff_time": "18:00",
                     "dropoff_location": "Batumi Airport (BUS)", "status": "confirmed"}],
        "restaurants": [{"name": "Retro", "date": "2026-10-28", "time": "20:00", "location": "Batumi"}],
    }
    trip.update(overrides)
    return trip


class TimezoneResolutionTests(unittest.TestCase):
    def test_airport_codes_cities_and_countries_resolve_without_network(self):
        resolver = reminders.TimezoneResolver(geocode=None)
        self.assertEqual(resolver.airport("Tel Aviv (TLV)"), "Asia/Jerusalem")
        self.assertEqual(resolver.airport("BUS"), "Asia/Tbilisi")
        self.assertEqual(resolver.place("40 Rustaveli Ave, Batumi"), "Asia/Tbilisi")
        self.assertEqual(resolver.place("", "", "איטליה"), "Europe/Rome")
        self.assertIsNone(resolver.place("Somewhere Unknown"))

    def test_unknown_places_are_geocoded_once_and_cached(self):
        calls = []

        def geocode(name):
            calls.append(name)
            return "Europe/Ljubljana"

        with TemporaryDirectory() as directory:
            cache = Path(directory) / "tz.json"
            resolver = reminders.TimezoneResolver(cache, geocode=geocode)
            self.assertEqual(resolver.place("Hotel Park, Bled, Slovenia Region"), "Europe/Ljubljana")
            self.assertEqual(resolver.place("Hotel Park, Bled, Slovenia Region"), "Europe/Ljubljana")
            self.assertEqual(calls, ["Bled"])
            reloaded = reminders.TimezoneResolver(cache, geocode=lambda name: self.fail("cache was not reused"))
            self.assertEqual(reloaded.place("Hotel Park, Bled, Slovenia Region"), "Europe/Ljubljana")

    def test_geocoding_failures_are_not_cached(self):
        def broken(name):
            raise OSError("offline")

        resolver = reminders.TimezoneResolver(geocode=broken)
        self.assertIsNone(resolver.place("Bled"))
        resolver.geocode = lambda name: "Europe/Ljubljana"
        self.assertEqual(resolver.place("Bled"), "Europe/Ljubljana")


class BuildReminderTests(unittest.TestCase):
    def setUp(self):
        self.resolver = reminders.TimezoneResolver(geocode=None)

    def plan(self, trip):
        return {item["id"]: item for item in reminders.build_reminders([trip], self.resolver)}

    def find(self, plan, suffix):
        matches = [item for key, item in plan.items() if key.endswith(suffix)]
        self.assertEqual(len(matches), 1, f"expected one reminder ending with {suffix}: {list(plan)}")
        return matches[0]

    def test_flight_reminders_use_the_origin_airport_time_zone(self):
        plan = self.plan(batumi_trip())
        outbound = self.find(plan, "2026-10-27T06:40:00+02:00:checkin")
        self.assertEqual(outbound["fire_at"], utc("2026-10-26T06:40:00+02:00"))
        homebound = self.find(plan, "2026-11-02T13:00:00+04:00:checkin")
        self.assertEqual(homebound["fire_at"], utc("2026-11-01T13:00:00+04:00"))
        self.assertIn("K7XQ2P", homebound["body"])
        leave = self.find(plan, "2026-11-02T13:00:00+04:00:leave")
        self.assertEqual(leave["fire_at"], utc("2026-11-02T09:00:00+04:00"))

    def test_relative_reminders_in_quiet_hours_are_dropped(self):
        plan = self.plan(batumi_trip())
        self.assertFalse([key for key in plan if key.endswith("2026-10-27T06:40:00+02:00:leave")])

    def test_rental_return_gets_evening_and_fuel_reminders(self):
        plan = self.plan(batumi_trip())
        eve = self.find(plan, ":dropoff-eve")
        self.assertEqual(eve["fire_at"], utc("2026-10-31T19:00:00+04:00"))
        self.assertIn("עד 18:00", eve["title"])
        fuel = self.find(plan, ":fuel")
        self.assertEqual(fuel["fire_at"], utc("2026-11-01T15:00:00+04:00"))
        pickup = self.find(plan, ":pickup")
        self.assertEqual(pickup["fire_at"], utc("2026-10-28T08:00:00+04:00"))

    def test_hotel_checkout_morning_reminder(self):
        checkout = self.find(self.plan(batumi_trip()), ":checkout")
        self.assertEqual(checkout["fire_at"], utc("2026-11-02T08:00:00+04:00"))
        self.assertIn("12:00", checkout["title"])

    def test_cancelled_and_superseded_bookings_produce_no_reminders(self):
        trip = batumi_trip()
        for flight in trip["flights"]:
            flight["status"] = "cancelled"
        trip["rentals"][0]["status"] = "superseded"
        plan = self.plan(trip)
        self.assertFalse([key for key in plan if key.startswith(("flight:", "rental:"))])

    def test_modified_departure_time_creates_a_new_reminder_id(self):
        before = set(self.plan(batumi_trip()))
        trip = batumi_trip()
        trip["flights"][1]["departure_time"] = "15:30"
        after = set(self.plan(trip))
        self.assertTrue([key for key in after - before if key.endswith(":checkin")])

    def test_daily_brief_morning_or_evening_before_early_days(self):
        plan = self.plan(batumi_trip())
        busy_day = plan["brief:t1:2026-10-28"]
        self.assertEqual(busy_day["fire_at"], utc("2026-10-28T07:30:00+04:00"))
        self.assertIn("10:00 איסוף רכב", busy_day["body"])
        self.assertIn("20:00 Retro", busy_day["body"])
        early_day = plan["brief:t1:2026-10-27"]
        self.assertEqual(early_day["fire_at"], utc("2026-10-26T21:00:00+02:00"))
        self.assertTrue(early_day["title"].endswith("מחר"))

    def test_bad_dates_and_times_are_skipped(self):
        trip = batumi_trip(flights=[{"number": "X1", "origin": "TLV", "departure_date": "soon", "departure_time": "25:99"}])
        self.assertFalse([key for key in self.plan(trip) if key.startswith("flight:")])


class SchedulingTests(unittest.TestCase):
    def test_due_window_and_sent_dedupe(self):
        fire = utc("2026-10-28T08:00:00+04:00")
        items = [{"id": "a", "fire_at": fire}, {"id": "b", "fire_at": fire}]
        self.assertEqual(reminders.due_reminders(items, fire - timedelta(minutes=1), {}), [])
        self.assertEqual([r["id"] for r in reminders.due_reminders(items, fire + timedelta(minutes=5), {"a": "x"})], ["b"])
        self.assertEqual(reminders.due_reminders(items, fire + timedelta(hours=2), {}), [])

    def test_prune_drops_old_and_invalid_entries(self):
        now = utc("2026-10-28T12:00:00+00:00")
        kept = reminders.prune_sent({"new": now.isoformat(), "old": (now - timedelta(days=30)).isoformat(), "bad": "?"}, now)
        self.assertEqual(list(kept), ["new"])


class ServerReminderCycleTests(unittest.TestCase):
    def test_cycle_sends_each_due_reminder_once_and_retries_failures(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            trips_file = root / "trips.json"
            trips_file.write_text(json.dumps([batumi_trip()], ensure_ascii=False), encoding="utf-8")
            sent_messages = []

            def sender(title, body, tag=""):
                sent_messages.append((title, body, tag))
                return 1

            resolver = reminders.TimezoneResolver(geocode=None)
            now = utc("2026-10-28T07:35:00+04:00")
            with patch.object(server, "TRIPS", trips_file), patch.object(server, "REMINDER_STATE", root / "state.json"):
                failed = server.run_reminder_cycle(now=now, resolver=resolver, sender=lambda *a, **k: 0)
                self.assertEqual(failed, [])
                delivered = server.run_reminder_cycle(now=now, resolver=resolver, sender=sender)
                self.assertEqual(delivered, ["brief:t1:2026-10-28"])
                self.assertEqual(sent_messages[0][0], "☀️ מה מחכה היום")
                self.assertTrue(sent_messages[0][2].startswith("tripy-reminder-"))
                again = server.run_reminder_cycle(now=now + timedelta(minutes=2), resolver=resolver, sender=sender)
                self.assertEqual(again, [])
                state = json.loads((root / "state.json").read_text(encoding="utf-8"))
                self.assertIn("brief:t1:2026-10-28", state["sent"])
                upcoming = server.upcoming_trip_reminders(now=now, resolver=resolver)
                self.assertEqual(upcoming[0]["title"], "🚗 איסוף רכב בעוד שעתיים")
                json.dumps(upcoming)  # API payload is serialisable


if __name__ == "__main__":
    unittest.main()
