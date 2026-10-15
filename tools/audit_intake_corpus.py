#!/usr/bin/env python3
"""Privacy-safe aggregate audit for persisted TRIPY email intake records.

The tool reads local intake artifacts but emits counts and allow-listed labels only.
It never emits message IDs, subjects, addresses, filenames, extracted values, or
record samples. ``--synthetic`` is deterministic and is used when no index exists.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from email import policy
from email.parser import BytesParser
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram-bot"))
from booking_types import infer_extended_type
from email_rules import classify_email_intent
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
    if hint and hint != "marketplace":
        return hint
    return infer_extended_type({}, source) or str(record.get("ingested_type") or "unknown") or "unknown"


def _facts(category: str, source: str, record: dict) -> dict[str, str]:
    explicit = record.get("facts_present")
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
    return history[-1] if history and isinstance(history[-1], dict) else {}


def _outcomes(record: dict, telemetry: dict, facts: dict[str, str]) -> tuple[str, str, str, str, str]:
    status = str(record.get("status") or "unknown")
    error = str(telemetry.get("validation_error_code") or "")
    persistence = str(telemetry.get("persistence_outcome") or "") or ({"processed": "created"}.get(status, "not_persisted"))
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
    stage = str(record.get("processing_stage") or "unknown")
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


def build_report(records: list[dict], source_mode: str, intake_dir: Path | None = None) -> dict:
    counts = {key: Counter() for key in ("provider", "category", "intent", "validation", "trip", "persistence", "stage", "diagnosis")}
    facts = defaultdict(Counter)
    for record in records:
        if record.get("_synthetic"):
            provider = {"provider": record["provider"], "category_hint": record["category"]}
            source = ""
        else:
            source = _body_for_record(intake_dir or ROOT, record)
            provider = identify_email_provider(str(record.get("from") or ""), str(record.get("reply_to") or ""), str(record.get("subject") or ""), source)
        category = record.get("category") or _category(record, provider, source)
        intent = record.get("intent") or classify_email_intent(str(record.get("subject") or ""), source)
        field_presence = _facts(category, source, record)
        telemetry = _last_telemetry(record)
        validation, trip, persistence, stage, diagnosis = _outcomes(record, telemetry, field_presence)
        for key, value in (("provider", provider.get("provider", "unknown")), ("category", category or "unknown"),
                           ("intent", intent or "unknown"), ("validation", validation), ("trip", trip),
                           ("persistence", persistence), ("stage", stage), ("diagnosis", diagnosis)):
            counts[key][str(value)] += 1
        for field, value in field_presence.items():
            facts[f"{category}.{field}"][value] += 1
    return {
        "schema_version": 1,
        "source_mode": source_mode,
        "records": len(records),
        "provider_counts": dict(sorted(counts["provider"].items())),
        "category_counts": dict(sorted(counts["category"].items())),
        "intent_counts": dict(sorted(counts["intent"].items())),
        "fact_presence": {key: dict(sorted(value.items())) for key, value in sorted(facts.items())},
        "validation_result_counts": dict(sorted(counts["validation"].items())),
        "trip_match_result_counts": dict(sorted(counts["trip"].items())),
        "persistence_result_counts": dict(sorted(counts["persistence"].items())),
        "failure_stage_counts": dict(sorted(counts["stage"].items())),
        "diagnosis_counts": dict(sorted(counts["diagnosis"].items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intake_dir", nargs="?", type=Path, default=ROOT / "trip-email")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--count", type=int, default=60)
    args = parser.parse_args()
    index = args.intake_dir / "index.json"
    use_synthetic = args.synthetic or not index.is_file()
    if use_synthetic:
        records, mode = _synthetic_records(max(1, args.count)), "synthetic"
    else:
        try:
            raw = json.loads(index.read_text(encoding="utf-8"))
            records, mode = (raw if isinstance(raw, list) else raw.get("records", [])), "live"
        except (OSError, ValueError, AttributeError):
            records, mode = _synthetic_records(max(1, args.count)), "synthetic_fallback"
    print(json.dumps(build_report(records, mode, args.intake_dir), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
