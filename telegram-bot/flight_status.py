"""Live flight status (delays, cancellations, gates) from AeroDataBox via RapidAPI.

The poller only spends API calls around each flight: every few hours the day
before, more often in the hours before departure, and until it lands. Each
change worth knowing about becomes one push message. Without an API key the
feature is silently disabled and the rest of TRIPY behaves as before.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

API_HOST = "aerodatabox.p.rapidapi.com"
INACTIVE_BOOKINGS = {"cancelled", "refunded", "superseded"}
FINAL_STATUSES = {"arrived", "canceled", "cancelled", "diverted"}
DELAY_ALERT_MINUTES = 15
DELAY_STEP_MINUTES = 15  # re-alert only when the delay moves by at least this much


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_flight_number(value: Any) -> str:
    """'LY 001' / 'ly1' -> 'LY1'; AeroDataBox accepts the compact IATA form."""
    match = re.fullmatch(r"([A-Z0-9]{2}[A-Z]?)\s*0*(\d{1,4})([A-Z]?)", _text(value).upper().replace("-", " "))
    if not match:
        return re.sub(r"\s+", "", _text(value).upper())
    return f"{match.group(1)}{int(match.group(2))}{match.group(3)}"


def flight_key(flight: dict) -> str:
    return f"{normalize_flight_number(flight.get('number') or flight.get('flight_number'))}:{_text(flight.get('departure_date'))[:10]}"


def parse_api_time(value: Any) -> datetime | None:
    """AeroDataBox times look like '2026-10-27 04:40Z' or '2026-10-27 06:40+02:00'
    (sometimes wrapped as {'utc': ..., 'local': ...})."""
    if isinstance(value, dict):
        value = value.get("utc") or value.get("local")
    raw = _text(value)
    if not raw:
        return None
    raw = raw.replace(" ", "T", 1)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str:
    return value.astimezone(timezone.utc).isoformat() if value else ""


def _movement(raw: dict, side: str) -> dict:
    value = raw.get(side) if isinstance(raw, dict) else None
    return value if isinstance(value, dict) else {}


def _best_time(movement: dict) -> datetime | None:
    for field in ("runwayTime", "revisedTime", "predictedTime", "scheduledTime"):
        moment = parse_api_time(movement.get(field))
        if moment:
            return moment
    return None


def pick_leg(legs: Any, origin: Any = "") -> dict | None:
    if isinstance(legs, dict):
        legs = [legs]
    if not isinstance(legs, list):
        return None
    legs = [leg for leg in legs if isinstance(leg, dict)]
    origin_text = _text(origin).upper()
    for leg in legs:
        airport = _movement(leg, "departure").get("airport") or {}
        codes = [_text(airport.get("iata")).upper(), _text(airport.get("icao")).upper()]
        if any(code and re.search(rf"\b{re.escape(code)}\b", origin_text) for code in codes):
            return leg
    return legs[0] if legs else None


def _local_text(movement: dict) -> str:
    for field in ("runwayTime", "revisedTime", "predictedTime", "scheduledTime"):
        value = movement.get(field)
        if isinstance(value, dict) and _text(value.get("local")):
            return _text(value.get("local"))
    return ""


def summarize(raw: dict, checked_at: datetime) -> dict:
    dep, arr = _movement(raw, "departure"), _movement(raw, "arrival")
    scheduled = parse_api_time(dep.get("scheduledTime"))
    best_dep = _best_time(dep)
    delay = int(round((best_dep - scheduled).total_seconds() / 60)) if scheduled and best_dep else 0
    status = _text(raw.get("status"))
    return {
        "status": status,
        "cancelled": status.casefold() in {"canceled", "cancelled"},
        "departure_scheduled_utc": _iso(scheduled),
        "departure_estimated_utc": _iso(best_dep),
        "departure_estimated_local": _local_text(dep),
        "delay_minutes": max(delay, 0),
        "terminal": _text(dep.get("terminal")),
        "gate": _text(dep.get("gate")),
        "check_in_desk": _text(dep.get("checkInDesk")),
        "arrival_estimated_utc": _iso(_best_time(arr)),
        "arrival_terminal": _text(arr.get("terminal")),
        "baggage_belt": _text(arr.get("baggageBelt")),
        "checked_at": _iso(checked_at),
    }


def _local_clock(summary: dict) -> str:
    local = _text(summary.get("departure_estimated_local"))
    match = re.search(r"(\d{2}:\d{2})", local)
    return match.group(1) if match else ""


def change_messages(number: str, previous: dict | None, current: dict) -> list[tuple[str, str]]:
    """Push messages (title, body) for what changed since the last check."""
    previous = previous or {}
    messages: list[tuple[str, str]] = []
    if current.get("cancelled") and not previous.get("cancelled"):
        return [(f"❌ הטיסה {number} בוטלה", "כדאי לבדוק מול חברת התעופה או באפליקציה שלה")]
    if _text(current.get("status")).casefold() == "diverted" and _text(previous.get("status")).casefold() != "diverted":
        messages.append((f"⚠️ הטיסה {number} הוסטה", "הטיסה נוחתת בשדה אחר מהמתוכנן"))
    delay, before = int(current.get("delay_minutes") or 0), int(previous.get("delay_minutes") or 0)
    clock = _local_clock(current)
    if delay >= DELAY_ALERT_MINUTES and abs(delay - before) >= DELAY_STEP_MINUTES:
        when = f" · המראה צפויה ב-{clock}" if clock else ""
        messages.append((f"⏱️ {number} מתעכבת {delay} דק׳", f"עודכן מול לוח הטיסות{when}"))
    elif before >= DELAY_ALERT_MINUTES and delay < DELAY_ALERT_MINUTES:
        messages.append((f"✅ {number} חזרה לזמן", f"המראה לפי הלו״ז{f' ב-{clock}' if clock else ''}"))
    gate, old_gate = _text(current.get("gate")), _text(previous.get("gate"))
    if gate and gate != old_gate:
        terminal = f"טרמינל {current['terminal']} · " if current.get("terminal") else ""
        title = f"🚪 שער {gate} לטיסה {number}" if not old_gate else f"🚪 שינוי שער: {number} עברה לשער {gate}"
        messages.append((title, f"{terminal}{'היה שער ' + old_gate if old_gate else 'השער נקבע'}"))
    belt, old_belt = _text(current.get("baggage_belt")), _text(previous.get("baggage_belt"))
    if belt and belt != old_belt and _text(current.get("status")).casefold() == "arrived":
        messages.append((f"🧳 מזוודות מ-{number}", f"מסוע {belt}"))
    return messages


def next_check_due(departure_utc: datetime, now: datetime, last_checked: datetime | None, landed: bool) -> bool:
    """Polling schedule tuned to stay inside a small monthly quota."""
    if landed:
        return False
    until = departure_utc - now
    if until > timedelta(hours=26) or now - departure_utc > timedelta(hours=14):
        return False
    if until > timedelta(hours=6):
        interval = timedelta(hours=4)
    elif until > timedelta(hours=3):
        interval = timedelta(hours=1)
    elif until > -timedelta(minutes=30):
        interval = timedelta(minutes=20)
    else:
        interval = timedelta(minutes=45)  # airborne: watch for landing / belt
    return last_checked is None or now - last_checked >= interval


class AeroDataBoxClient:
    def __init__(self, api_key: str, opener: Callable[..., Any] | None = None):
        self.api_key = api_key
        self.opener = opener or urllib.request.urlopen

    def lookup(self, number: str, date_local: str) -> list:
        path = f"/flights/number/{urllib.parse.quote(number)}/{urllib.parse.quote(date_local)}"
        query = urllib.parse.urlencode({"withAircraftImage": "false", "withLocation": "false", "dateLocalRole": "Departure"})
        request = urllib.request.Request(f"https://{API_HOST}{path}?{query}", headers={
            "X-RapidAPI-Key": self.api_key, "X-RapidAPI-Host": API_HOST, "Accept": "application/json", "User-Agent": "TRIPY/1.0"})
        with self.opener(request, timeout=15) as response:
            if getattr(response, "status", 200) == 204:
                return []
            body = response.read()
        value = json.loads(body) if body else []
        return value if isinstance(value, list) else [value]


def tracked_flights(trips: Any) -> list[dict]:
    flights = []
    for trip in trips or []:
        if not isinstance(trip, dict):
            continue
        for flight in trip.get("flights") or []:
            if not isinstance(flight, dict) or _text(flight.get("status") or "confirmed").casefold() in INACTIVE_BOOKINGS:
                continue
            if normalize_flight_number(flight.get("number") or flight.get("flight_number")) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", _text(flight.get("departure_date"))[:10]):
                flights.append(flight)
    return flights


def run_flight_status_cycle(trips: Any, state: dict, now: datetime, client: Any, departure_utc: Callable[[dict], datetime | None],
                            notify: Callable[[str, str, str], Any], monthly_limit: int) -> dict:
    """Poll due flights, record summaries and push changes. Returns the new state."""
    month = now.strftime("%Y-%m")
    if state.get("month") != month:
        state = {**state, "month": month, "calls": 0}
    records = dict(state.get("flights") or {})
    calls = int(state.get("calls") or 0)
    for flight in tracked_flights(trips):
        key = flight_key(flight)
        record = dict(records.get(key) or {})
        summary = record.get("summary") or {}
        departure = parse_api_time(summary.get("departure_estimated_utc")) or departure_utc(flight)
        if departure is None:
            continue
        landed = _text(summary.get("status")).casefold() in FINAL_STATUSES
        if not next_check_due(departure, now, parse_api_time(record.get("last_checked")), landed):
            continue
        if calls >= monthly_limit:
            break
        calls += 1
        record["last_checked"] = _iso(now)
        number = normalize_flight_number(flight.get("number") or flight.get("flight_number"))
        try:
            leg = pick_leg(client.lookup(number, _text(flight.get("departure_date"))[:10]), flight.get("origin"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            record["last_error"] = type(exc).__name__
            records[key] = record
            continue
        record.pop("last_error", None)
        if leg is None:
            record["not_found"] = True
            records[key] = record
            continue
        current = summarize(leg, now)
        for title, body in change_messages(_text(flight.get("number") or number), summary, current):
            notify(title, body, f"tripy-flight-{re.sub(r'[^A-Za-z0-9-]', '-', key)}")
        record["summary"] = current
        records[key] = record
    cutoff = now - timedelta(days=10)
    records = {k: v for k, v in records.items() if (parse_api_time((v or {}).get("last_checked")) or now) > cutoff}
    return {**state, "month": month, "calls": calls, "flights": records}


def live_status_for(state: dict, flight: dict) -> dict | None:
    record = (state.get("flights") or {}).get(flight_key(flight)) or {}
    return record.get("summary") or None
