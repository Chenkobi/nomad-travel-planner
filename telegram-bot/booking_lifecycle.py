from datetime import datetime


COLLECTIONS = {
    "hotel": "hotels",
    "flight": "flights",
    "train": "trains",
    "attraction": "attractions",
    "car_rental": "rentals",
}


def _text(value):
    return str(value or "").strip().casefold()


def _date(value):
    try:
        return datetime.fromisoformat(str(value)).date()
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
    return record.get("vehicle_type") or record.get("name")


def _record_dates(record, kind):
    if kind == "hotel":
        return record.get("start"), record.get("end")
    if kind == "flight":
        return record.get("departure_date"), record.get("arrival_date")
    if kind == "train":
        return record.get("date"), record.get("date")
    if kind == "attraction":
        return record.get("date"), record.get("date")
    return record.get("pickup_date"), record.get("dropoff_date") or record.get("pickup_date")


def _same_dates(record, kind, facts):
    record_start, record_end = _record_dates(record, kind)
    fact_start = facts.get("start") or facts.get("date") or facts.get("pickup_date") or facts.get("departure_date")
    fact_end = facts.get("end") or facts.get("date") or facts.get("dropoff_date") or facts.get("arrival_date") or fact_start
    return bool(_date(record_start) and _date(fact_start) and _date(record_start) == _date(fact_start) and _date(record_end) == _date(fact_end))


def _same_name(record, kind, facts):
    wanted = _text(facts.get("name") or facts.get("hotel") or facts.get("attraction") or facts.get("vehicle_type") or facts.get("flight_number") or facts.get("train_number"))
    return bool(wanted and wanted == _text(_record_name(record, kind)))


def find_booking_matches(trips, facts):
    matches = []
    confirmation = _text(facts.get("confirmation_number"))
    if confirmation:
        for trip in trips:
            for kind, collection in COLLECTIONS.items():
                for record in trip.get(collection, []) or []:
                    if _text(record.get("confirmation_number")) == confirmation and record.get("status", "confirmed") not in {"cancelled", "superseded"}:
                        matches.append({"trip_id": trip.get("id"), "trip": trip, "kind": kind, "record": record, "match": "confirmation_number"})
        return matches

    supplier = _text(facts.get("supplier"))
    for trip in trips:
        for kind, collection in COLLECTIONS.items():
            for record in trip.get(collection, []) or []:
                if record.get("status", "confirmed") in {"cancelled", "superseded"}:
                    continue
                record_supplier = _text(record.get("supplier") or record.get("airline"))
                if not supplier or not record_supplier or supplier != record_supplier:
                    continue
                if _same_dates(record, kind, facts) and _same_name(record, kind, facts):
                    matches.append({"trip_id": trip.get("id"), "trip": trip, "kind": kind, "record": record, "match": "supplier_dates_name"})
    return matches if len(matches) == 1 else []


def cancel_booking(trips, match, source_email_id):
    record = match.get("record") if match else None
    if not record:
        return False
    record["status"] = "cancelled"
    record["cancelled_by_email"] = source_email_id
    return True
