"""Internal normalized booking-facts contract between extraction and storage."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Mapping

from booking_extraction import extract_booking_facts


_TYPE_ALIASES = {
    "car": "car_rental",
    "car rental": "car_rental",
    "rental": "car_rental",
    "lodging": "hotel",
}
_STATUS_VALUES = {"confirmed", "pending", "cancelled", "refunded", "unknown"}
_FACT_KEYS = (
    "hotel", "city", "check_in", "check_out",
    "airline", "flight_number", "origin", "destination",
    "departure_date", "departure_time", "arrival_date", "arrival_time",
    "pickup_date", "pickup_time", "pickup_location", "vehicle_type",
    "dropoff_date", "dropoff_time", "dropoff_location",
)
_DATE_FORMATS = ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%d/%m/%Y", "%m/%d/%Y")


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _type(value: Any) -> str:
    value = _text(value).casefold()
    return _TYPE_ALIASES.get(value, value or "other")


def _date(value: Any) -> str:
    value = _text(value).replace(",", "")
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
        return value
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def _time(value: Any) -> str:
    value = _text(value)
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", value)
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else value


def _provider(value: Any) -> str:
    if isinstance(value, Mapping):
        return _text(value.get("provider")) or "unknown"
    return _text(value) or "unknown"


def normalize_booking_facts(
    extracted: Mapping[str, Any] | None = None,
    *,
    subject: str = "",
    body: str = "",
    provider: Mapping[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Return a stable, provider-independent booking facts envelope.

    Provider data is retained as metadata only. It never supplies or overrides
    the booking type; explicit facts or the extractor's type decide it.
    """
    extracted = dict(extracted or {})
    text_facts = extract_booking_facts(subject, body)
    declared_type = _type(extracted.get("type"))
    fact_type = _type(text_facts.get("type"))
    normalized_type = fact_type if declared_type == "other" and fact_type != "other" else declared_type
    facts: dict[str, Any] = {}
    source_facts = dict(text_facts.get("facts") or {})
    source_facts.update(extracted.get("facts") or {})
    source_facts.update({key: extracted.get(key) for key in _FACT_KEYS if extracted.get(key) not in (None, "", [], {})})
    for key in _FACT_KEYS:
        value = source_facts.get(key)
        if value in (None, "", [], {}):
            continue
        if key.endswith("_date") or key in {"check_in", "check_out"}:
            value = _date(value)
        elif key.endswith("_time"):
            value = _time(value)
        elif key == "flight_number":
            value = re.sub(r"^([A-Z]{2,3})\s*(\d{1,4})$", r"\1 \2", re.sub(r"\s+", "", _text(value).upper()))
        else:
            value = _text(value)
        if value:
            facts[key] = value
    status = _text(extracted.get("status")).casefold()
    if status not in _STATUS_VALUES:
        status = text_facts.get("status", "unknown")
    return {"type": normalized_type, "status": status, "provider": _provider(provider or extracted.get("provider")), "facts": facts}


def merge_normalized_booking_facts(extracted: Mapping[str, Any] | None, contract: Mapping[str, Any]) -> dict[str, Any]:
    """Overlay normalized missing values without replacing model evidence."""
    result = dict(extracted or {})
    if _type(result.get("type")) == "other" and contract.get("type") != "other":
        result["type"] = contract["type"]
    if not _text(result.get("status")) or _text(result.get("status")).casefold() == "unknown":
        result["status"] = contract.get("status", "unknown")
    for key, value in (contract.get("facts") or {}).items():
        result.setdefault(key, value)
    return result
