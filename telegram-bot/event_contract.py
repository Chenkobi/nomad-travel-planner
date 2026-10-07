from __future__ import annotations

import hashlib
import json
from typing import Any


_KIND_NAMES = {
    "מלון": "hotel",
    "טיסה": "flight",
    "רכבת": "train",
    "אטרקציה": "attraction",
    "מסעדה": "restaurant",
    "רכב": "car_rental",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def event_kind(value: Any) -> str:
    raw = _text(value)
    return _KIND_NAMES.get(raw, raw.casefold().replace(" ", "_"))


def normalize_event(event: Any, trip_id: Any = None) -> dict[str, Any]:
    if isinstance(event, dict):
        result = dict(event)
        result.setdefault("kind", event_kind(result.get("kind") or result.get("type")))
        result.setdefault("location", _text(result.get("location") or result.get("address")))
        result.setdefault("trip_id", _text(trip_id or result.get("trip_id")))
    else:
        values = list(event or [])
        result = {
            "time": _text(values[0] if len(values) > 0 else ""),
            "icon": _text(values[1] if len(values) > 1 else ""),
            "title": _text(values[2] if len(values) > 2 else ""),
            "details": _text(values[3] if len(values) > 3 else ""),
            "kind": event_kind(values[4] if len(values) > 4 else ""),
            "source": _text(values[5] if len(values) > 5 else ""),
            "date": _text(values[6] if len(values) > 6 else ""),
            "location": _text(values[7] if len(values) > 7 else ""),
            "trip_id": _text(trip_id),
        }
    result["event_id"] = event_identity(result, result.get("trip_id"))
    return result


def event_identity(event: Any, trip_id: Any = None) -> str:
    normalized = normalize_event_without_identity(event, trip_id)
    kind = normalized["kind"] or "event"
    digest_input = {
        "trip_id": normalized["trip_id"],
        "date": normalized["date"],
        "time": normalized["time"],
        "kind": kind,
        "title": normalized["title"],
        "location": normalized["location"],
    }
    digest = hashlib.sha256(json.dumps(digest_input, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:20]
    return f"event:{kind}:{digest}"


def normalize_event_without_identity(event: Any, trip_id: Any = None) -> dict[str, str]:
    if isinstance(event, dict):
        return {
            "time": _text(event.get("time")),
            "title": _text(event.get("title")),
            "date": _text(event.get("date")),
            "kind": event_kind(event.get("kind") or event.get("type")),
            "location": _text(event.get("location") or event.get("address")),
            "trip_id": _text(trip_id or event.get("trip_id")),
        }
    values = list(event or [])
    return {
        "time": _text(values[0] if len(values) > 0 else ""),
        "title": _text(values[2] if len(values) > 2 else ""),
        "date": _text(values[6] if len(values) > 6 else ""),
        "kind": event_kind(values[4] if len(values) > 4 else ""),
        "location": _text(values[7] if len(values) > 7 else ""),
        "trip_id": _text(trip_id),
    }


def event_as_legacy_list(event: Any, trip_id: Any = None) -> list[Any]:
    normalized = normalize_event(event, trip_id)
    original_kind = event.get("kind") if isinstance(event, dict) else ((list(event or [])[4]) if len(list(event or [])) > 4 else normalized.get("kind", ""))
    return [
        normalized.get("time", ""), normalized.get("icon", ""), normalized.get("title", ""),
        normalized.get("details", ""), original_kind, normalized.get("source", ""),
        normalized.get("date", ""), normalized.get("location", ""), normalized["event_id"],
        normalized.get("trip_id", ""),
    ]
