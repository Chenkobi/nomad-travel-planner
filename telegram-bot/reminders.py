"""Time-based trip reminders delivered through the existing Web Push channel.

Booking times are stored as destination-local wall-clock values (a flight's
departure_time is local to its origin airport, a hotel's check-out time is local
to the hotel). Every reminder is therefore computed in the booking location's
time zone and compared in UTC.

Reminders are derived from the trips on every cycle, so a modified booking
naturally produces new reminder ids and a cancelled booking produces none.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

INACTIVE_STATUSES = {"cancelled", "refunded", "superseded"}
DEFAULT_TIMEZONE = "Asia/Jerusalem"
SEND_GRACE = timedelta(minutes=45)
SENT_RETENTION = timedelta(days=21)
MORNING_BRIEF = time(7, 30)
EVENING_BRIEF = time(21, 0)
EARLY_START = time(9, 30)       # days starting before this get the brief the evening before
QUIET_START = time(23, 0)       # relative reminders that land in quiet hours are dropped
QUIET_END = time(6, 30)

AIRPORT_TIMEZONES = {
    "tlv": "Asia/Jerusalem", "etm": "Asia/Jerusalem", "hfa": "Asia/Jerusalem",
    "mxp": "Europe/Rome", "lin": "Europe/Rome", "bgy": "Europe/Rome", "fco": "Europe/Rome",
    "cia": "Europe/Rome", "vce": "Europe/Rome", "nap": "Europe/Rome", "blq": "Europe/Rome",
    "flr": "Europe/Rome", "psa": "Europe/Rome", "cta": "Europe/Rome", "pmo": "Europe/Rome",
    "cdg": "Europe/Paris", "ory": "Europe/Paris", "nce": "Europe/Paris",
    "lhr": "Europe/London", "lgw": "Europe/London", "stn": "Europe/London", "ltn": "Europe/London",
    "muc": "Europe/Berlin", "fra": "Europe/Berlin", "ber": "Europe/Berlin", "dus": "Europe/Berlin",
    "ham": "Europe/Berlin", "bud": "Europe/Budapest", "bus": "Asia/Tbilisi", "tbs": "Asia/Tbilisi",
    "kut": "Asia/Tbilisi", "zrh": "Europe/Zurich", "gva": "Europe/Zurich", "bsl": "Europe/Zurich",
    "vie": "Europe/Vienna", "prg": "Europe/Prague", "ath": "Europe/Athens", "skg": "Europe/Athens",
    "her": "Europe/Athens", "rho": "Europe/Athens", "lca": "Asia/Nicosia", "pfo": "Asia/Nicosia",
    "ist": "Europe/Istanbul", "saw": "Europe/Istanbul", "bcn": "Europe/Madrid", "mad": "Europe/Madrid",
    "lis": "Europe/Lisbon", "ams": "Europe/Amsterdam", "bru": "Europe/Brussels", "waw": "Europe/Warsaw",
    "krk": "Europe/Warsaw", "otp": "Europe/Bucharest", "sof": "Europe/Sofia", "cph": "Europe/Copenhagen",
    "arn": "Europe/Stockholm", "osl": "Europe/Oslo", "dxb": "Asia/Dubai", "auh": "Asia/Dubai",
    "jfk": "America/New_York", "ewr": "America/New_York", "lax": "America/Los_Angeles",
    "bkk": "Asia/Bangkok", "evn": "Asia/Yerevan", "gyd": "Asia/Baku", "lju": "Europe/Ljubljana",
    "zag": "Europe/Zagreb", "spu": "Europe/Zagreb", "dbv": "Europe/Zagreb", "tia": "Europe/Tirane",
    "tgd": "Europe/Podgorica", "beg": "Europe/Belgrade", "mla": "Europe/Malta",
}

CITY_TIMEZONES = {
    "tel aviv": "Asia/Jerusalem", "תל אביב": "Asia/Jerusalem", "jerusalem": "Asia/Jerusalem",
    "ben gurion": "Asia/Jerusalem", "נתב״ג": "Asia/Jerusalem", "נתב\"ג": "Asia/Jerusalem",
    "milan": "Europe/Rome", "milano": "Europe/Rome", "מילאנו": "Europe/Rome", "rome": "Europe/Rome",
    "roma": "Europe/Rome", "רומא": "Europe/Rome", "venice": "Europe/Rome", "venezia": "Europe/Rome",
    "florence": "Europe/Rome", "firenze": "Europe/Rome", "como": "Europe/Rome", "garda": "Europe/Rome",
    "paris": "Europe/Paris", "פריז": "Europe/Paris", "london": "Europe/London", "לונדון": "Europe/London",
    "munich": "Europe/Berlin", "münchen": "Europe/Berlin", "מינכן": "Europe/Berlin", "berlin": "Europe/Berlin",
    "frankfurt": "Europe/Berlin", "פרנקפורט": "Europe/Berlin",
    "budapest": "Europe/Budapest", "בודפשט": "Europe/Budapest", "batumi": "Asia/Tbilisi",
    "באטומי": "Asia/Tbilisi", "tbilisi": "Asia/Tbilisi", "טביליסי": "Asia/Tbilisi",
    "zurich": "Europe/Zurich", "zürich": "Europe/Zurich", "ציריך": "Europe/Zurich", "geneva": "Europe/Zurich",
    "lucerne": "Europe/Zurich", "luzern": "Europe/Zurich", "interlaken": "Europe/Zurich",
    "vienna": "Europe/Vienna", "wien": "Europe/Vienna", "prague": "Europe/Prague", "praha": "Europe/Prague",
    "athens": "Europe/Athens", "barcelona": "Europe/Madrid", "madrid": "Europe/Madrid",
    "lisbon": "Europe/Lisbon", "lisboa": "Europe/Lisbon", "amsterdam": "Europe/Amsterdam",
}

COUNTRY_TIMEZONES = {
    "israel": "Asia/Jerusalem", "ישראל": "Asia/Jerusalem", "italy": "Europe/Rome", "italia": "Europe/Rome",
    "איטליה": "Europe/Rome", "france": "Europe/Paris", "צרפת": "Europe/Paris", "germany": "Europe/Berlin",
    "deutschland": "Europe/Berlin", "גרמניה": "Europe/Berlin", "united kingdom": "Europe/London",
    "uk": "Europe/London", "england": "Europe/London", "בריטניה": "Europe/London", "אנגליה": "Europe/London",
    "hungary": "Europe/Budapest", "הונגריה": "Europe/Budapest", "georgia": "Asia/Tbilisi",
    "גיאורגיה": "Asia/Tbilisi", "גאורגיה": "Asia/Tbilisi", "switzerland": "Europe/Zurich",
    "schweiz": "Europe/Zurich", "שווייץ": "Europe/Zurich", "שוויץ": "Europe/Zurich", "austria": "Europe/Vienna",
    "אוסטריה": "Europe/Vienna", "czech": "Europe/Prague", "צ׳כיה": "Europe/Prague", "greece": "Europe/Athens",
    "יוון": "Europe/Athens", "spain": "Europe/Madrid", "ספרד": "Europe/Madrid", "portugal": "Europe/Lisbon",
    "פורטוגל": "Europe/Lisbon", "netherlands": "Europe/Amsterdam", "הולנד": "Europe/Amsterdam",
    "cyprus": "Asia/Nicosia", "קפריסין": "Asia/Nicosia",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _valid_zone(name: Any) -> str | None:
    name = _text(name)
    if not name:
        return None
    try:
        ZoneInfo(name)
        return name
    except (ZoneInfoNotFoundError, ValueError):
        return None


class TimezoneResolver:
    """Resolve free-text locations to IANA zones: airport codes, known cities and
    countries, then an optional cached geocoding lookup, then the default."""

    def __init__(self, cache_path: Path | None = None, geocode: Callable[[str], str | None] | None = None,
                 default: str = DEFAULT_TIMEZONE):
        self.cache_path = cache_path
        self.geocode = geocode
        self.default = _valid_zone(default) or DEFAULT_TIMEZONE
        self._cache: dict[str, str | None] = {}
        if cache_path and cache_path.exists():
            try:
                value = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    self._cache = {str(k): _valid_zone(v) for k, v in value.items()}
            except (OSError, ValueError):
                self._cache = {}

    def airport(self, value: Any) -> str | None:
        for code in re.findall(r"\b[a-z]{3}\b", _text(value).casefold()):
            if code in AIRPORT_TIMEZONES:
                return AIRPORT_TIMEZONES[code]
        return None

    def place(self, *values: Any) -> str | None:
        texts = [_text(v).casefold() for v in values if _text(v)]
        for lowered in texts:
            for city, zone in CITY_TIMEZONES.items():
                if city in lowered:
                    return zone
        for lowered in texts:
            for country, zone in COUNTRY_TIMEZONES.items():
                if re.search(rf"(?<!\w){re.escape(country)}(?!\w)", lowered):
                    return zone
        for value in values:
            zone = self._geocoded(_text(value))
            if zone:
                return zone
        return None

    def _geocoded(self, value: str) -> str | None:
        if not value or not self.geocode:
            return None
        # "Street 12, City, Country" -> geocode the city part.
        parts = [p.strip() for p in re.split(r"[,·|]", value) if p.strip() and not re.search(r"\d", p)]
        query = (parts[-2] if len(parts) >= 3 else parts[0] if parts else value)[:80]
        key = query.casefold()
        if key in self._cache:
            return self._cache[key]
        try:
            zone = _valid_zone(self.geocode(query))
        except Exception:
            return None  # network trouble: not cached, retried next cycle
        self._cache[key] = zone
        if self.cache_path:
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        return zone


def open_meteo_geocode(name: str) -> str | None:
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": name, "count": 1})
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "TRIPY/1.0"})
    with urllib.request.urlopen(request, timeout=6) as response:
        results = json.loads(response.read()).get("results") or []
    return _text(results[0].get("timezone")) or None if results else None


def local_moment(date_value: Any, time_value: Any, zone: str) -> datetime | None:
    try:
        day = date.fromisoformat(_text(date_value)[:10])
    except ValueError:
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", _text(time_value))
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    return datetime.combine(day, time(int(match.group(1)), int(match.group(2))), tzinfo=ZoneInfo(zone))


def _at(day_value: Any, clock: time, zone: str) -> datetime | None:
    return local_moment(day_value, clock.strftime("%H:%M"), zone)


def _quiet(moment: datetime) -> bool:
    clock = moment.timetz().replace(tzinfo=None)
    return clock >= QUIET_START or clock < QUIET_END


def _active(booking: Any) -> bool:
    return isinstance(booking, dict) and _text(booking.get("status") or "confirmed").casefold() not in INACTIVE_STATUSES


def _join(*parts: Any) -> str:
    return " · ".join(p for p in (_text(x) for x in parts) if p)


def _conf(booking: dict) -> str:
    number = _text(booking.get("confirmation_number"))
    return f"אישור {number}" if number else ""


def _reminder(key: str, fire_at: datetime | None, title: str, body: str, *, relative: bool = False) -> dict | None:
    """relative=True marks 'in N hours' reminders, which are dropped in quiet hours
    (the evening brief or an earlier reminder already covers them)."""
    if fire_at is None or (relative and _quiet(fire_at)):
        return None
    return {"id": key, "fire_at": fire_at.astimezone(timezone.utc), "title": title[:80], "body": body[:240],
            "tag": "tripy-reminder-" + re.sub(r"[^\w-]", "-", key, flags=re.ASCII)[:60]}


def _trip_zone(trip: dict, resolver: TimezoneResolver) -> str:
    for hotel in trip.get("hotels") or []:
        if _active(hotel):
            zone = resolver.place(hotel.get("address"), hotel.get("city"), hotel.get("country"))
            if zone:
                return zone
    places = [d.get("city") if isinstance(d, dict) else d for d in trip.get("destinations") or []]
    return resolver.place(*places, trip.get("title")) or resolver.default


def build_reminders(trips: Iterable[dict], resolver: TimezoneResolver) -> list[dict]:
    """Every reminder implied by the active bookings, past and future."""
    reminders: list[dict | None] = []
    for trip in trips or []:
        if not isinstance(trip, dict):
            continue
        trip_id = _text(trip.get("id"))
        trip_zone = _trip_zone(trip, resolver)
        agenda: list[tuple[datetime, str]] = []

        for flight in trip.get("flights") or []:
            if not _active(flight):
                continue
            origin = _text(flight.get("origin"))
            zone = resolver.airport(origin) or resolver.place(origin) or resolver.default
            dep = local_moment(flight.get("departure_date"), flight.get("departure_time"), zone)
            if dep is None:
                continue
            number = _text(flight.get("number") or flight.get("flight_number"))
            route = f"{origin} → {_text(flight.get('destination'))}"
            hhmm = dep.strftime("%H:%M")
            base = f"flight:{trip_id}:{number}:{dep.isoformat()}"
            reminders.append(_reminder(base + ":checkin", dep - timedelta(hours=24), "✈️ טיסה מחר · אפשר לעשות צ׳ק-אין",
                                       _join(f"{_text(flight.get('airline'))} {number}".strip(), route, f"המראה ב-{hhmm}", _conf(flight))))
            reminders.append(_reminder(base + ":leave", dep - timedelta(hours=4), f"✈️ טיסה ב-{hhmm} · זמן לצאת לשדה",
                                       _join(number, route, "כדאי להיות בשדה כשלוש שעות לפני ההמראה"), relative=True))
            agenda.append((dep, f"{hhmm} טיסה {number}"))

        for hotel in trip.get("hotels") or []:
            if not _active(hotel):
                continue
            zone = resolver.place(hotel.get("address"), hotel.get("city"), hotel.get("country")) or trip_zone
            name = _text(hotel.get("name")) or "המלון"
            checkin = _text(hotel.get("checkin_time") or trip.get("checkin_time") or "15:00")
            checkout = _text(hotel.get("checkout_time") or trip.get("checkout_time") or "11:00")
            base = f"hotel:{trip_id}:{name}:{_text(hotel.get('start'))}:{_text(hotel.get('end'))}"
            reminders.append(_reminder(base + ":checkout", _at(hotel.get("end"), time(8, 0), zone),
                                       f"🏨 צ׳ק-אאוט היום עד {checkout}", _join(name, "לא לשכוח דרכונים ומה שבכספת")))
            arrival = local_moment(hotel.get("start"), checkin, zone)
            if arrival:
                agenda.append((arrival, f"{checkin} צ׳ק-אין {name}"))
            departure = local_moment(hotel.get("end"), checkout, zone)
            if departure:
                agenda.append((departure, f"{checkout} צ׳ק-אאוט {name}"))

        for rental in trip.get("rentals") or []:
            if not _active(rental):
                continue
            pickup_place = _text(rental.get("pickup_location"))
            dropoff_place = _text(rental.get("dropoff_location")) or pickup_place
            pickup_zone = resolver.airport(pickup_place) or resolver.place(pickup_place) or trip_zone
            dropoff_zone = resolver.airport(dropoff_place) or resolver.place(dropoff_place) or pickup_zone
            base = f"rental:{trip_id}:{_text(rental.get('pickup_date'))}:{_text(rental.get('dropoff_date'))}"
            pickup = local_moment(rental.get("pickup_date"), rental.get("pickup_time"), pickup_zone)
            if pickup:
                reminders.append(_reminder(base + ":pickup", pickup - timedelta(hours=2), "🚗 איסוף רכב בעוד שעתיים",
                                           _join(f"{pickup.strftime('%H:%M')} · {pickup_place}", rental.get("vehicle_type"),
                                                 rental.get("supplier"), _conf(rental)), relative=True))
                agenda.append((pickup, f"{pickup.strftime('%H:%M')} איסוף רכב"))
            dropoff_day = _text(rental.get("dropoff_date"))
            dropoff_time = _text(rental.get("dropoff_time"))
            if dropoff_day:
                eve = _at(dropoff_day, time(19, 0), dropoff_zone)
                until = f" עד {dropoff_time}" if dropoff_time else ""
                reminders.append(_reminder(base + ":dropoff-eve", eve - timedelta(days=1) if eve else None,
                                           f"🚗 מחר מחזירים את הרכב{until}", _join(dropoff_place, "כדאי לתדלק כבר הערב")))
                dropoff = local_moment(dropoff_day, dropoff_time, dropoff_zone)
                if dropoff:
                    reminders.append(_reminder(base + ":fuel", dropoff - timedelta(hours=3), "⛽ החזרת רכב בעוד 3 שעות",
                                               _join(f"{dropoff_time} · {dropoff_place}", "זמן לתדלק ולצאת לדרך"), relative=True))
                    agenda.append((dropoff, f"{dropoff_time} החזרת רכב"))

        for collection, icon, label, lead in (("trains", "🚆", "רכבת", 90), ("attractions", "🎟️", "אטרקציה", 120),
                                               ("restaurants", "🍽️", "מסעדה", 90)):
            for item in trip.get(collection) or []:
                if not _active(item):
                    continue
                zone = resolver.place(item.get("location"), item.get("address")) or trip_zone
                moment = local_moment(item.get("date"), item.get("time"), zone)
                if moment is None:
                    continue
                name = _text(item.get("name")) or label
                when = "שעה וחצי" if lead == 90 else "שעתיים"
                reminders.append(_reminder(f"{collection}:{trip_id}:{name}:{moment.isoformat()}", moment - timedelta(minutes=lead),
                                           f"{icon} בעוד {when}: {name}",
                                           _join(moment.strftime("%H:%M"), item.get("location") or item.get("address"), _conf(item)),
                                           relative=True))
                agenda.append((moment, f"{moment.strftime('%H:%M')} {name}"))

        reminders.extend(_briefs(trip_id, agenda))
    return [r for r in reminders if r]


def _briefs(trip_id: str, agenda: list[tuple[datetime, str]]) -> list[dict | None]:
    """One summary per day with at least two items: at 07:30 local, or at 21:00 the
    evening before when the day starts early."""
    days: dict[date, list[tuple[datetime, str]]] = {}
    for moment, line in agenda:
        days.setdefault(moment.date(), []).append((moment, line))
    briefs = []
    for day, items in sorted(days.items()):
        if len(items) < 2:
            continue
        items.sort(key=lambda entry: entry[0].astimezone(timezone.utc))
        first = items[0][0]
        body = " · ".join(line for _, line in items)
        if first.timetz().replace(tzinfo=None) < EARLY_START:
            fire = datetime.combine(day - timedelta(days=1), EVENING_BRIEF, tzinfo=first.tzinfo)
            briefs.append(_reminder(f"brief:{trip_id}:{day.isoformat()}", fire, "🌙 מה מחכה מחר", body))
        else:
            fire = datetime.combine(day, MORNING_BRIEF, tzinfo=first.tzinfo)
            briefs.append(_reminder(f"brief:{trip_id}:{day.isoformat()}", fire, "☀️ מה מחכה היום", body))
    return briefs


def due_reminders(reminders: Iterable[dict], now: datetime, sent: dict, grace: timedelta = SEND_GRACE) -> list[dict]:
    due = [r for r in reminders if r["id"] not in sent and r["fire_at"] <= now < r["fire_at"] + grace]
    return sorted(due, key=lambda r: r["fire_at"])


def upcoming_reminders(reminders: Iterable[dict], now: datetime, limit: int = 30) -> list[dict]:
    future = sorted((r for r in reminders if r["fire_at"] > now), key=lambda r: r["fire_at"])[:limit]
    return [{**r, "fire_at": r["fire_at"].isoformat()} for r in future]


def prune_sent(sent: Any, now: datetime) -> dict:
    kept = {}
    for key, stamp in (sent if isinstance(sent, dict) else {}).items():
        try:
            when = datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if now - when < SENT_RETENTION:
            kept[str(key)] = when.isoformat()
    return kept
