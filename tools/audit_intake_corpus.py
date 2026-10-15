#!/usr/bin/env python3
"""Privacy-safe aggregate audit for persisted TRIPY email intake records.

The tool reads local intake artifacts but emits counts and allow-listed labels only.
It never emits message IDs, subjects, addresses, filenames, extracted values, or
record samples. ``--synthetic`` is deterministic and must be explicitly requested.
Live mode is the default and fails closed when its index is missing or invalid.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from email import policy
from datetime import date, datetime, timezone
from email.parser import BytesParser
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram-bot"))
from booking_types import infer_extended_type
from email_rules import _strong_confirmation_subject, classify_email_intent
from provider_rules import identify_email_provider
from server import html_to_text, normalize_car_rental_fields, normalize_flight_fields


FACT_FIELDS = {
    "flight": ("airline", "flight_number", "origin", "destination", "departure_date", "departure_time", "arrival_time"),
    "hotel": ("hotel", "city", "check_in", "check_out"),
    "car_rental": ("pickup_date", "pickup_time", "pickup_location", "vehicle_type"),
    "train": ("train_number", "origin", "destination", "date", "time"),
    "attraction": ("attraction", "date", "time", "location"),
    "restaurant": ("restaurant_name", "date", "time"),
    "insurance": ("policy_number", "insurer", "valid_from", "valid_to"),
}
ALLOWED_CATEGORIES = set(FACT_FIELDS) | {"unknown"}
ALLOWED_INTENTS = {"booking", "cancellation", "change", "receipt", "notification", "other", "confirmed", "cancelled", "modified", "unknown"}
ALLOWED_ERRORS = {"source_unavailable", "provider_unavailable", "invalid_provider_response", "unsupported_type", "unknown_status", "validation_failed", "persistence_failed", "no_unique_match", "not_travel_email", "no_source", ""}
FAILURE_STAGES = {"received", "extracting", "classifying", "validating", "matching", "persisting", "persisted", "failed", "unknown"}


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _headers(path: Path) -> dict[str, str]:
    try:
        with path.open("rb") as handle:
            message = BytesParser(policy=policy.default).parse(handle)
        return {key: "\n".join(str(value) for value in message.get_all(key.title(), []))
                for key in ("received", "from", "reply_to")}
    except (OSError, ValueError):
        return {"received": "", "from": "", "reply_to": ""}


def _body_for_record(intake_dir: Path, record: dict) -> str:
    folder = intake_dir / str(record.get("id") or "")
    parts = [_text(folder / "body.txt"), html_to_text(_text(folder / "body.html"))]
    source_file = str(record.get("source_file") or "source.eml")
    parts.append("\n".join(_headers(folder / source_file).values()))
    # Names are used only as classification signals; their values never leave this process.
    parts.append(" ".join(str(item.get("filename") or "") for item in record.get("attachments") or []))
    return "\n".join(parts)


def _category(record: dict, provider: dict, source: str) -> str:
    hint = str(provider.get("category_hint") or "")
    if hint in FACT_FIELDS:
        return hint
    declared = str(record.get("ingested_type") or "")
    if declared in FACT_FIELDS:
        return declared
    inferred = infer_extended_type({}, source)
    return inferred or "unknown"


def _facts(category: str, source: str, record: dict) -> dict[str, str]:
    explicit = record.get("facts_present")
    if not isinstance(explicit, dict):
        envelope = record.get("booking_facts") or record.get("normalized_facts") or {}
        explicit = envelope.get("facts") if isinstance(envelope, dict) else None
    if not isinstance(explicit, dict):
        explicit = record.get("facts")
    if isinstance(explicit, dict):
        return {field: "present" if bool(explicit.get(field)) else "missing" for field in FACT_FIELDS.get(category, ())}
    probe = {}
    if category == "flight":
        probe = normalize_flight_fields({}, source)
    elif category == "car_rental":
        probe = normalize_car_rental_fields({}, source)
    return {field: "present" if str(probe.get(field) or "").strip() else "missing"
            for field in FACT_FIELDS.get(category, ())}


def _last_telemetry(record: dict) -> dict:
    history = record.get("extraction_telemetry") or []
    if isinstance(history, dict):
        return history
    return history[-1] if history and isinstance(history[-1], dict) else {}


def _failure_stage(record: dict, telemetry: dict, status: str) -> str:
    stage = str(record.get("processing_stage") or "").strip().lower()
    error = str(telemetry.get("validation_error_code") or "").strip().lower()
    inferred = {
        "source_unavailable": "extracting", "provider_unavailable": "extracting",
        "invalid_provider_response": "extracting", "not_travel_email": "classifying",
        "unsupported_type": "classifying", "unknown_status": "validating",
        "validation_failed": "validating", "persistence_failed": "persisting",
        "no_unique_match": "matching",
    }.get(error)
    if inferred:
        return inferred
    if stage in FAILURE_STAGES:
        return stage
    return "persisted" if status == "processed" else "unknown"


def _outcomes(record: dict, telemetry: dict, facts: dict[str, str]) -> tuple[str, str, str, str, str]:
    status = str(record.get("status") or "unknown")
    raw_error = str(telemetry.get("validation_error_code") or "")
    error = raw_error if raw_error in ALLOWED_ERRORS else "unknown_error"
    raw_persistence = str(telemetry.get("persistence_outcome") or "")
    persistence = raw_persistence if raw_persistence in {"created", "updated", "not_persisted", "failed", "ignored"} else ({"processed": "created"}.get(status, "not_persisted"))
    validation = "passed" if not error and status == "processed" else (error or "unknown")
    trip_id = bool(str(record.get("trip_id") or "").strip())
    if trip_id:
        trip_match = "matched"
    elif status == "processed":
        trip_match = "processed_but_not_visible"
    elif status in {"needs_review", "failed"}:
        trip_match = "not_matched"
    else:
        trip_match = "unknown"
    stage = _failure_stage(record, telemetry, status)
    missing = any(value == "missing" for value in facts.values())
    if missing and status != "processed" and not error:
        diagnosis = "unknown_facts"
    elif trip_match == "processed_but_not_visible":
        diagnosis = "processed_but_not_visible"
    elif error in {"persistence_failed", "validation_failed"} or persistence == "failed":
        diagnosis = "bug_or_invariant_failure"
    elif error:
        diagnosis = "known_failure"
    elif status == "processed":
        diagnosis = "success"
    else:
        diagnosis = "unknown_state"
    return validation, trip_match, persistence, stage, diagnosis


def _synthetic_records(count: int) -> list[dict]:
    templates = [
        ("booking.com", "hotel", "confirmed", "persisted", True, "created"),
        ("wizz_air", "flight", "confirmed", "persisted", True, "created"),
        ("sixt", "car_rental", "confirmed", "persisted", True, "created"),
        ("deutsche_bahn", "train", "confirmed", "persisted", True, "created"),
        ("marriott", "hotel", "cancelled", "applying", False, "not_persisted"),
        ("unknown", "flight", "confirmed", "applying", False, "not_persisted"),
        ("avis", "car_rental", "confirmed", "failed", False, "failed"),
        ("harel", "insurance", "confirmed", "persisted", True, "updated"),
        ("trip.com", "attraction", "confirmed", "persisted", False, "created"),
        ("unknown", "hotel", "confirmed", "persisted", False, "created"),
    ]
    records = []
    for index in range(count):
        provider, category, intent, stage, visible, persistence = templates[index % len(templates)]
        fields = {field: True for field in FACT_FIELDS.get(category, ())}
        if index % len(templates) == 5:
            fields["departure_time"] = False
        if index % len(templates) == 9:
            fields["check_out"] = False
        error = ""
        if stage == "extracting": error = "provider_unavailable"
        if stage == "failed": error = "persistence_failed"
        records.append({"_synthetic": True, "provider": provider, "category": category, "intent": intent,
                        "status": "processed" if stage == "persisted" else "needs_review",
                        "processing_stage": stage, "trip_id": f"synthetic-trip-{index}" if visible else "",
                        "facts_present": fields, "extraction_telemetry": [{"validation_error_code": error,
                        "persistence_outcome": persistence}]})
    return records


def _booking_visibility(record: dict, trips: list[dict] | None) -> tuple[bool | None, bool | None]:
    if trips is None:
        return None, None
    trip_id = str(record.get("trip_id") or "").strip()
    email_id = str(record.get("id") or "").strip()
    document = str(record.get("document") or "").strip()
    trip = next((item for item in trips if isinstance(item, dict) and str(item.get("id") or "") == trip_id), None) if trip_id else None
    if not trip:
        return False, False
    collection = {
        "flight": "flights", "hotel": "hotels", "train": "trains", "car_rental": "rentals",
        "restaurant": "restaurants", "attraction": "attractions", "insurance": "insurance",
    }.get(str(record.get("ingested_type") or record.get("category") or "").casefold())
    bookings = trip.get(collection) or [] if collection else []
    if isinstance(bookings, dict):
        bookings = [bookings]
    visible = any(
        isinstance(booking, dict) and (
            (email_id and str(booking.get("source_email_id") or "") == email_id)
            or (document and str(booking.get("document") or "") == document)
            or (email_id and str(booking.get("cancelled_by_email") or "") == email_id)
            or (email_id and str(booking.get("modified_by_email") or "") == email_id)
        )
        for booking in bookings
    )
    return True, visible


def _date_value(value):
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


def _booking_for_record(record: dict, trips: list[dict] | None):
    if not trips:
        return None, None
    trip_id = str(record.get("trip_id") or "").strip()
    email_id = str(record.get("id") or "").strip()
    document = str(record.get("document") or "").strip()
    category = str(record.get("ingested_type") or record.get("category") or "").casefold()
    collection = {"flight": "flights", "hotel": "hotels", "train": "trains", "car_rental": "rentals",
                  "restaurant": "restaurants", "attraction": "attractions", "insurance": "insurance"}.get(category)
    trip = next((item for item in trips if isinstance(item, dict) and str(item.get("id") or "") == trip_id), None)
    if not trip or not collection:
        return trip, None
    bookings = trip.get(collection) or []
    if isinstance(bookings, dict):
        bookings = [bookings]
    for booking in bookings:
        if not isinstance(booking, dict):
            continue
        if (email_id and str(booking.get("source_email_id") or "") == email_id) or (document and str(booking.get("document") or "") == document):
            return trip, booking
    return trip, None


def _booking_date_range(category: str, booking: dict | None):
    if not booking:
        return None, None
    category = str(category or "").casefold()
    if category == "hotel":
        start = booking.get("start") or booking.get("check_in")
        end = booking.get("end") or booking.get("check_out") or start
    elif category == "flight":
        start = booking.get("departure_date") or booking.get("date")
        end = booking.get("arrival_date") or start
    elif category == "car_rental":
        start = booking.get("pickup_date")
        end = booking.get("dropoff_date") or start
    elif category == "insurance":
        start = booking.get("valid_from")
        end = booking.get("valid_to") or start
    else:
        start = booking.get("date") or booking.get("departure_date")
        end = start
    start_date, end_date = _date_value(start), _date_value(end)
    if not start_date or not end_date or end_date < start_date:
        return None, None
    return start_date, end_date


def _trip_interval(trip: dict):
    start, end = _date_value(trip.get("start")), _date_value(trip.get("end"))
    return (start, end) if start and end and end >= start else (None, None)


def _is_open_trip(trip: dict) -> bool:
    return not trip.get("closed") and not trip.get("archived") and str(trip.get("status") or "").strip().casefold() not in {"closed", "completed", "archived"}


def _normalized_country(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _trip_countries(trip: dict) -> set[str]:
    values = ([item.get("country") for item in trip.get("destinations", []) if isinstance(item, dict)]
              + [item.get("country") for item in (trip.get("hotels") or []) if isinstance(item, dict)])
    return {_normalized_country(value) for value in values if _normalized_country(value)}


def _interval_gap(start, end, trip_start, trip_end) -> int:
    if trip_end < start:
        return (start - trip_end).days
    if trip_start > end:
        return (trip_start - end).days
    return 0


def _timestamp(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _record_processing_time(record: dict):
    return _timestamp(record.get("processed_at") or record.get("received_at") or record.get("created_at") or record.get("timestamp"))


def _trip_creation_time(trip: dict):
    created = _timestamp(trip.get("created_at") or trip.get("created"))
    if created:
        return created
    identifier = str(trip.get("id") or "")
    if re.fullmatch(r"\d{20}", identifier):
        try:
            return datetime.strptime(identifier, "%Y%m%d%H%M%S%f").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _tripcom_window_diagnostic(record: dict, provider: str, category: str, status: str, trips: list[dict] | None, booking_visible):
    if provider != "trip.com":
        return None
    assigned_trip, booking = _booking_for_record(record, trips)
    start, end = _booking_date_range(category, booking)
    processing_time = _record_processing_time(record)
    if trips is None or not start or not end:
        return {"category": category, "status": status, "booking_visible": booking_visible,
                "booking_saved": bool(booking), "booking_dates_present": bool(start and end),
                "saved_trip_count": len(trips) if trips is not None else None,
                "processing_time_present": bool(processing_time),
                "prior_policy_candidate_count": None, "seven_day_policy_candidate_count": None,
                "country_gate_excluded_candidate_count": None,
                "candidate_created_before_processing_count": None,
                "candidate_created_after_processing_count": None,
                "candidate_creation_time_unknown_count": None}
    assigned_id = str(assigned_trip.get("id") or "") if assigned_trip else ""
    requested_country = _normalized_country(booking.get("country")) if booking else ""
    old_count = new_count = country_excluded = 0
    created_before = created_after = created_unknown = 0
    for trip in trips:
        if not isinstance(trip, dict) or not _is_open_trip(trip):
            continue
        if assigned_id and str(trip.get("id") or "") == assigned_id:
            continue
        trip_start, trip_end = _trip_interval(trip)
        if not trip_start or not trip_end:
            continue
        gap = _interval_gap(start, end, trip_start, trip_end)
        if gap <= 7:
            new_count += 1
            candidate_time = _trip_creation_time(trip)
            if not processing_time or not candidate_time:
                created_unknown += 1
            elif candidate_time <= processing_time:
                created_before += 1
            else:
                created_after += 1
        if category == "hotel":
            overlaps = start <= trip_end and end >= trip_start
            countries = _trip_countries(trip)
            if overlaps and requested_country and countries and requested_country not in countries:
                country_excluded += 1
            elif overlaps:
                old_count += 1
        elif trip_start <= start <= trip_end:
            old_count += 1
    return {"category": category, "status": status, "booking_visible": booking_visible,
            "booking_saved": bool(booking), "booking_dates_present": True,
            "saved_trip_count": len(trips),
            "processing_time_present": bool(processing_time),
            "prior_policy_candidate_count": old_count,
            "seven_day_policy_candidate_count": new_count,
            "country_gate_excluded_candidate_count": country_excluded,
            "candidate_created_before_processing_count": created_before,
            "candidate_created_after_processing_count": created_after,
            "candidate_creation_time_unknown_count": created_unknown}


def _read_trip_data(path: Path | None) -> list[dict] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("trip data file is missing or invalid")
    if not isinstance(value, list):
        raise ValueError("trip data must be a list")
    return value


def build_report(records: list[dict], source_mode: str, intake_dir: Path | None = None, trip_data: list[dict] | None = None) -> dict:
    counts = {key: Counter() for key in ("provider", "category", "intent", "status", "reason", "validation", "trip", "persistence", "stage", "diagnosis")}
    facts = defaultdict(Counter)
    evidence = defaultdict(Counter)
    provider_outcomes = Counter()
    latest_record_summary = None
    latest_order = None
    latest_tripcom_summary = None
    latest_tripcom_order = None
    for index, record in enumerate(records):
        if record.get("_synthetic"):
            provider = {"provider": record["provider"], "category_hint": record["category"]}
            source = ""
        else:
            source = _body_for_record(intake_dir or ROOT, record)
            provider = identify_email_provider(str(record.get("from") or ""), str(record.get("reply_to") or ""), str(record.get("subject") or ""), source)
        category = record.get("category") or _category(record, provider, source)
        category = category if category in ALLOWED_CATEGORIES else "unknown"
        intent = record.get("intent") or classify_email_intent(str(record.get("subject") or ""), source)
        intent = intent if intent in ALLOWED_INTENTS else "unknown"
        source_intent = classify_email_intent(str(record.get("subject") or ""), source)
        source_intent = source_intent if source_intent in ALLOWED_INTENTS else "unknown"
        field_presence = _facts(category, source, record)
        telemetry = _last_telemetry(record)
        validation, trip, persistence, stage, diagnosis = _outcomes(record, telemetry, field_presence)
        status = str(record.get("status") or "unknown")
        status = status if status in {"processed", "needs_review", "failed", "ignored", "received", "processing"} else "unknown"
        trip_id_resolves, booking_visible = _booking_visibility(record, trip_data)
        if trip_data is not None and status == "processed":
            trip = "matched_visible" if booking_visible else "processed_but_not_visible"
            if not booking_visible:
                diagnosis = "processed_but_not_visible"
        reason_code = str(telemetry.get("validation_error_code") or "").strip().lower()
        reason_code = "none" if not reason_code else reason_code if reason_code in ALLOWED_ERRORS else "other_error"
        provider_outcomes[f"{provider.get('provider', 'unknown')}|{category or 'unknown'}|{status}|{reason_code}"] += 1
        received = str(record.get("received_at") or record.get("created_at") or record.get("timestamp") or "")
        order = (bool(received), received, index)
        if latest_order is None or order > latest_order:
            latest_order = order
            latest_record_summary = {
                "provider": str(provider.get("provider") or "unknown"),
                "category": str(category or "unknown"),
                "intent": str(intent or "unknown"),
                "source_intent": str(source_intent or "unknown"),
                "source_intent_matches_record": source_intent == intent,
                "strong_confirmation_subject": bool(_strong_confirmation_subject(str(record.get("subject") or ""))),
                "status": status,
                "stage": stage,
                "diagnosis": diagnosis,
                "failure_code": reason_code,
                "trip_match": trip,
                "persistence": persistence,
                "trip_id_present": bool(str(record.get("trip_id") or "").strip()),
                "trip_id_resolves": trip_id_resolves,
                "booking_visible": booking_visible,
                "failed_field": str(telemetry.get("validation_error_field") or "") if str(telemetry.get("validation_error_field") or "") in FACT_FIELDS.get(category, ()) else "",
                "missing_fields": sorted(field for field, presence in field_presence.items() if presence == "missing"),
            }
        if str(provider.get("provider") or "").casefold() == "trip.com" and not record.get("_synthetic"):
            if latest_tripcom_order is None or order > latest_tripcom_order:
                latest_tripcom_order = order
                latest_tripcom_summary = {
                    "provider": "trip.com",
                    "intent": intent,
                    "failure_code": reason_code,
                    "trip_id_resolves": trip_id_resolves,
                    **(_tripcom_window_diagnostic(record, "trip.com", category, status, trip_data, booking_visible) or {}),
                }
        for key, value in (("provider", provider.get("provider", "unknown")), ("category", category or "unknown"),
                           ("intent", intent or "unknown"), ("status", status), ("reason", reason_code),
                           ("validation", validation), ("trip", trip), ("persistence", persistence),
                           ("stage", stage), ("diagnosis", diagnosis)):
            counts[key][str(value)] += 1
        for field, value in field_presence.items():
            facts[f"{category}.{field}"][value] += 1
            evidence[f"{category}.{field}"][value] += 1
            if str(telemetry.get("validation_error_field") or "").strip().lower() == field:
                evidence[f"{category}.{field}.validation_error"]["present"] += 1
    false_processed = counts["status"].get("processed", 0) - counts["validation"].get("passed", 0)
    processed_but_not_visible = counts["trip"].get("processed_but_not_visible", 0)
    if latest_record_summary is not None:
        latest_record_summary["latest_tripcom_booking_summary"] = latest_tripcom_summary
    return {
        "schema_version": 1,
        "source_mode": source_mode,
        "records": len(records),
        "latest_record_summary": latest_record_summary,
        "provider_counts": dict(sorted(counts["provider"].items())),
        "category_counts": dict(sorted(counts["category"].items())),
        "intent_counts": dict(sorted(counts["intent"].items())),
        "status_counts": dict(sorted(counts["status"].items())),
        "reason_counts": dict(sorted(counts["reason"].items())),
        "fact_presence": {key: dict(sorted(value.items())) for key, value in sorted(facts.items())},
        "field_evidence_counts": {key: dict(sorted(value.items())) for key, value in sorted(evidence.items())},
        "validation_result_counts": dict(sorted(counts["validation"].items())),
        "trip_match_result_counts": dict(sorted(counts["trip"].items())),
        "persistence_result_counts": dict(sorted(counts["persistence"].items())),
        "failure_stage_counts": dict(sorted(counts["stage"].items())),
        "diagnosis_counts": dict(sorted(counts["diagnosis"].items())),
        "provider_category_status_reason_counts": dict(sorted(provider_outcomes.items())),
        "release_gate": {
            "source_mode": source_mode,
            "false_processed_count": false_processed,
            "processed_but_not_visible_count": processed_but_not_visible,
            "passed": false_processed == 0 and processed_but_not_visible == 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intake_dir", nargs="?", type=Path, default=ROOT / "trip-email")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--release-gate", action="store_true",
                        help="emit only machine-readable gate results and fail on violations")
    parser.add_argument("--latest-only", action="store_true", help="emit only the privacy-safe latest-record summary")
    parser.add_argument("--wait-terminal", type=int, default=0, help="wait for the latest record to leave received/processing")
    parser.add_argument("--trip-data-file", type=Path, help="optional saved trip file for exact booking visibility read-back")
    args = parser.parse_args()
    if args.release_gate and args.latest_only:
        parser.error("--release-gate and --latest-only are mutually exclusive")
    index = args.intake_dir / "index.json"
    deadline = time.monotonic() + max(0, min(args.wait_terminal, 300))
    while True:
        if args.synthetic:
            records, mode = _synthetic_records(max(1, args.count)), "synthetic"
        elif not index.is_file():
            print("live index is missing; pass --synthetic for local synthetic data", file=sys.stderr)
            return 2
        else:
            try:
                raw = json.loads(index.read_text(encoding="utf-8"))
                records = raw if isinstance(raw, list) else raw.get("records", [])
                if not isinstance(records, list):
                    raise ValueError("records must be a list")
                mode = "live"
            except (OSError, ValueError, AttributeError, TypeError):
                print("live index is invalid; refusing to run a synthetic fallback", file=sys.stderr)
                return 2
        try:
            trip_data = _read_trip_data(args.trip_data_file)
        except ValueError:
            print("trip data file is missing or invalid", file=sys.stderr)
            return 2
        report = build_report(records, mode, args.intake_dir, trip_data)
        latest = report.get("latest_record_summary") or {}
        if time.monotonic() >= deadline or latest.get("status") not in {"received", "processing"}:
            break
        time.sleep(1)
    output = latest if args.latest_only else report["release_gate"] if args.release_gate else report
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not args.release_gate or output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
