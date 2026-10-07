from datetime import date, datetime, timezone
import re


COLLECTIONS = {
    "hotel": "hotels",
    "flight": "flights",
    "train": "trains",
    "attraction": "attractions",
    "car_rental": "rentals",
    "restaurant": "restaurants",
}
LIFECYCLE_STATUSES = {"confirmed", "modified", "cancelled", "refunded"}


class BookingDateError(ValueError):
    """Raised when booking dates are absent, malformed, or reversed."""


def _text(value):
    return str(value or "").strip().casefold()


def validate_iso_date(value, field="date"):
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise BookingDateError(f"{field} must be an ISO date (YYYY-MM-DD)")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise BookingDateError(f"{field} is not a valid calendar date") from exc
    return text


def validate_date_range(start, end=None):
    start_text = validate_iso_date(start, "start date")
    end_text = validate_iso_date(end if end not in (None, "") else start_text, "end date")
    if end_text < start_text:
        raise BookingDateError("end date precedes start date")
    return start_text, end_text


def _date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _record_name(record, kind):
    if kind == "hotel":
        return record.get("name")
    if kind == "flight":
        return record.get("flight_number") or record.get("number")
    if kind == "train":
        return record.get("train_number") or record.get("number")
    if kind == "attraction":
        return record.get("attraction") or record.get("name")
    if kind == "restaurant":
        return record.get("name") or record.get("restaurant_name")
    return record.get("vehicle_type") or record.get("name")


def _record_dates(record, kind):
    if kind == "hotel":
        return record.get("start"), record.get("end")
    if kind == "flight":
        return record.get("departure_date"), record.get("arrival_date")
    if kind == "train":
        return record.get("date"), record.get("date")
    if kind == "attraction" or kind == "restaurant":
        return record.get("date"), record.get("date")
    return record.get("pickup_date"), record.get("dropoff_date") or record.get("pickup_date")


def booking_identity(kind, record):
    confirmation = _text(record.get("confirmation_number"))
    if confirmation:
        return kind, "confirmation_number", confirmation
    start, end = _record_dates(record, kind)
    return (
        kind,
        "facts",
        _text(record.get("supplier") or record.get("airline")),
        _text(_record_name(record, kind)),
        _text(record.get("city") or record.get("origin")),
        _text(record.get("destination")),
        str(start or ""),
        str(end or ""),
    )


def enrich_booking_lifecycle(record, source, status="confirmed", source_email_id=None, at=None):
    status = _text(status) or "confirmed"
    if status not in LIFECYCLE_STATUSES:
        raise ValueError(f"unsupported booking lifecycle status: {status}")
    record["source"] = source
    if source_email_id:
        record["source_email_id"] = source_email_id
    record["status"] = status
    record["lifecycle_status"] = status
    history = record.setdefault("lifecycle_history", [])
    history.append({"status": status, "source": source, "email_id": source_email_id, "at": at or datetime.now(timezone.utc).isoformat()})
    return record


def _same_dates(record, kind, facts):
    record_start, record_end = _record_dates(record, kind)
    fact_start = facts.get("start") or facts.get("date") or facts.get("pickup_date") or facts.get("departure_date")
    fact_end = facts.get("end") or facts.get("date") or facts.get("dropoff_date") or facts.get("arrival_date") or fact_start
    return bool(_date(record_start) and _date(fact_start) and _date(record_start) == _date(fact_start) and _date(record_end) == _date(fact_end))


def _same_name(record, kind, facts):
    wanted = _text(facts.get("name") or facts.get("hotel") or facts.get("attraction") or facts.get("vehicle_type") or facts.get("flight_number") or facts.get("train_number") or facts.get("restaurant_name"))
    return bool(wanted and wanted == _text(_record_name(record, kind)))


def find_booking_matches(trips, facts):
    matches = []
    confirmation = _text(facts.get("confirmation_number"))
    if confirmation:
        for trip in trips:
            for kind, collection in COLLECTIONS.items():
                for record in trip.get(collection, []) or []:
                    if _text(record.get("confirmation_number")) == confirmation and record.get("status", "confirmed") not in {"cancelled", "refunded", "superseded"}:
                        matches.append({"trip_id": trip.get("id"), "trip": trip, "kind": kind, "record": record, "match": "confirmation_number"})
        return matches

    supplier = _text(facts.get("supplier"))
    for trip in trips:
        for kind, collection in COLLECTIONS.items():
            for record in trip.get(collection, []) or []:
                if record.get("status", "confirmed") in {"cancelled", "refunded", "superseded"}:
                    continue
                record_supplier = _text(record.get("supplier") or record.get("airline"))
                if not supplier or not record_supplier or supplier != record_supplier:
                    continue
                if _same_dates(record, kind, facts) and _same_name(record, kind, facts):
                    matches.append({"trip_id": trip.get("id"), "trip": trip, "kind": kind, "record": record, "match": "supplier_dates_name"})
    return matches if len(matches) == 1 else []


def cancel_booking(trips, match, source_email_id, status="cancelled"):
    record = match.get("record") if match else None
    if not record:
        return False
    enrich_booking_lifecycle(record, record.get("source", "Email"), status=status, source_email_id=source_email_id)
    record["cancelled_by_email"] = source_email_id
    return True
