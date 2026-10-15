"""Provider-agnostic extraction of booking facts from bounded email text.

This layer deliberately relies on labelled facts and booking language, not a
supplier catalogue. Provider identity is optional metadata and never selects a
category when explicit facts disagree.
"""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any


_DATE_FORMATS = (
    "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
    "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d",
)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _label(text: str, *labels: str) -> str:
    names = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?im)^\s*(?:{names})\s*[:：-]\s*(.+?)\s*$", text)
    return _clean(match.group(1)) if match else ""


def _date(value: str) -> str:
    value = _clean(value).replace(",", "")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value) else ""


def _time(value: str) -> str:
    value = _clean(value)
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", value)
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else ""


def _has_booking_language(subject: str, body: str) -> bool:
    text = f"{subject} {body}".casefold()
    if re.search(r"\b(?:newsletter|guide|tips|offers?)\b", text) and not re.search(
        r"\b(?:confirmed|reservation|booking|itinerary|e-ticket)\b", text
    ):
        return False
    return bool(re.search(r"\b(?:confirmed|confirmation|reservation|booking|itinerary|e-ticket|voucher)\b", text))


def extract_booking_facts(subject: str = "", body: str = "", provider_hint: str | None = None) -> dict[str, Any]:
    """Return a stable type/status/facts envelope from email text.

    Only unambiguous labelled values are returned. ``provider_hint`` is kept as
    optional evidence and is never used to manufacture a booking type.
    """
    subject, body = _clean(subject), str(body or "")
    facts: dict[str, str] = {}
    booking = _has_booking_language(subject, body)

    hotel = _label(body, "hotel", "property", "accommodation")
    city = _label(body, "city", "destination")
    check_in = _date(_label(body, "check-in", "check in", "check-in date"))
    check_out = _date(_label(body, "check-out", "check out", "check-out date"))
    if hotel and check_in and check_out:
        facts.update(hotel=hotel, check_in=check_in, check_out=check_out)
        if city:
            facts["city"] = city
        doc_type = "hotel"
    else:
        flight = _label(body, "flight number", "flight no")
        origin = _label(body, "departure airport", "origin", "from")
        destination = _label(body, "arrival airport", "destination", "to")
        departure_date = _date(_label(body, "departure date"))
        departure_time = _time(_label(body, "departure time"))
        arrival_time = _time(_label(body, "arrival time"))
        if flight and origin and destination and departure_date:
            facts.update(
                flight_number=re.sub(r"^([A-Z]{2,3})\s*(\d{1,4})$", r"\1 \2", re.sub(r"\s+", "", flight.upper())),
                origin=origin,
                destination=destination,
                departure_date=departure_date,
            )
            if departure_time:
                facts["departure_time"] = departure_time
            if arrival_time:
                facts["arrival_time"] = arrival_time
            doc_type = "flight"
        else:
            pickup_date = _date(_label(body, "pickup date", "pick-up date"))
            pickup_time = _time(_label(body, "pickup time", "pick-up time"))
            pickup_location = _label(body, "pickup location", "pick-up location")
            vehicle = _label(body, "vehicle type", "car type")
            if pickup_date and pickup_time and pickup_location and vehicle:
                facts.update(
                    pickup_date=pickup_date,
                    pickup_time=pickup_time,
                    pickup_location=pickup_location,
                    vehicle_type=vehicle,
                )
                dropoff_date = _date(_label(body, "drop-off date", "dropoff date", "return date"))
                dropoff_location = _label(body, "drop-off location", "dropoff location", "return location")
                if dropoff_date:
                    facts["dropoff_date"] = dropoff_date
                if dropoff_location:
                    facts["dropoff_location"] = dropoff_location
                doc_type = "car_rental"
            else:
                doc_type = "other"

    provider = _clean(provider_hint)
    if not provider and doc_type == "hotel" and re.search(r"\b(?:property|accommodation)\b", body, re.I):
        provider = "marketplace"
    status = "confirmed" if doc_type != "other" and booking else "unknown"
    return {"type": doc_type, "status": status, "provider": provider, "facts": facts}
