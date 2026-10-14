#!/usr/bin/env python3
"""Redacted local audit of captured intake records.

This tool intentionally prints aggregate counts and short record suffixes only.
It is for local QA; raw intake data must never be committed.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from email import policy
from email.parser import BytesParser
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram-bot"))
from email_rules import classify_email_intent
from booking_types import infer_extended_type
from provider_rules import identify_email_provider
from server import _explicit_date_tokens, _label_values, normalize_flight_fields, normalize_car_rental_fields


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_headers(path: Path) -> dict[str, list[str]]:
    try:
        with path.open("rb") as handle:
            message = BytesParser(policy=policy.default).parse(handle)
        return {
            "received": [str(value) for value in message.get_all("Received", [])],
            "from": [str(value) for value in message.get_all("From", [])],
            "reply_to": [str(value) for value in message.get_all("Reply-To", [])],
        }
    except (OSError, ValueError):
        return {"received": [], "from": [], "reply_to": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("intake_dir", type=Path)
    args = parser.parse_args()
    index_path = args.intake_dir / "index.json"
    records = json.loads(index_path.read_text(encoding="utf-8"))
    provider_counts = Counter()
    category_counts = Counter()
    intent_counts = Counter()
    unresolved_reason_counts = Counter()
    unresolved_attachment_counts = Counter()
    persisted_review_reason_counts = Counter()
    unresolved = []
    recent = []

    for record in records:
        folder = args.intake_dir / str(record["id"])
        body = read_text(folder / "body.txt")
        html = read_text(folder / "body.html")
        headers = read_headers(folder / str(record.get("source_file") or "source.eml"))
        attachment_names = " ".join(str(item.get("filename") or "") for item in record.get("attachments") or [])
        attachment_text = []
        for item in record.get("attachments") or []:
            suffix = Path(str(item.get("filename") or "")).suffix.lower()
            if item.get("content_type") == "text/html" or suffix in {".html", ".htm"}:
                attachment_text.append(read_text(folder / str(item.get("path") or "")))
            elif suffix == ".pdf":
                try:
                    result = subprocess.run(
                        ["pdftotext", str(folder / str(item.get("path") or "")), "-"],
                        capture_output=True, text=True, timeout=20,
                    )
                    if result.returncode == 0:
                        attachment_text.append(result.stdout)
                except (OSError, subprocess.SubprocessError):
                    pass
        source_text = "\n".join((
            body,
            html,
            attachment_names,
            *attachment_text,
            *headers["received"],
            *headers["from"],
            *headers["reply_to"],
        ))
        provider = identify_email_provider(
            sender=str(record.get("from") or ""),
            reply_to=str(record.get("reply_to") or ""),
            subject=str(record.get("subject") or ""),
            body=source_text,
        )
        provider_counts[provider["provider"]] += 1
        category = provider["category_hint"] if provider["category_hint"] != "marketplace" else None
        category = category or infer_extended_type({}, source_text) or "unknown"
        category_counts[category] += 1
        intent = classify_email_intent(
            str(record.get("subject") or ""),
            "\n".join((body, html, *attachment_text, *headers["received"], *headers["from"])),
        )
        intent_counts[intent] += 1
        if len(recent) < 8:
            telemetry = (record.get("extraction_telemetry") or [{}])[-1] or {}
            parser_probe = normalize_flight_fields({}, source_text) if category == "flight" else normalize_car_rental_fields({}, source_text) if category == "car_rental" else {}
            required_fields = {
                "flight": ("airline", "flight_number", "origin", "destination", "departure_date", "departure_time", "arrival_time"),
                "car_rental": ("pickup_date", "pickup_time", "pickup_location", "vehicle_type"),
            }.get(category, ())
            recent.append({
                "id_suffix": str(record.get("id") or "")[-8:],
                "received_at": str(record.get("received_at") or ""),
                "provider": provider["provider"],
                "category": category,
                "intent": intent,
                "status": str(record.get("status") or ""),
                "stage": str(record.get("processing_stage") or ""),
                "missing_deterministic_fields": [key for key in required_fields if not str(parser_probe.get(key) or "").strip()],
                "source_text_length": len(source_text),
                "signal_counts": {
                    "flight_codes": len(re.findall(r"(?<![A-Z0-9])[A-Z]{2,3}\\s?\\d{1,4}(?!\\d)", source_text, re.I)),
                    "times": len(re.findall(r"\\b(?:[01]?\\d|2[0-3]):[0-5]\\d\\b", source_text)),
                    "dates": len(_explicit_date_tokens(source_text)),
                    "route_labels": sum(len(_label_values(source_text, labels)) for labels in (("from", "origin", "departure airport"), ("to", "destination", "arrival airport"), ("departure date", "date"))),
                },
                "review_reason": str(record.get("review_reason") or "")[:120],
                "processing_error": str(record.get("processing_error") or "")[:120],
                "telemetry_error": str(telemetry.get("validation_error_code") or ""),
            })
        reasons = []
        if provider["provider"] == "unknown":
            reasons.append("provider_unknown")
        if category == "unknown":
            reasons.append("category_unknown")
        if intent == "unknown":
            reasons.append("intent_unknown")
        unresolved_reason_counts.update(reasons)
        if reasons:
            formats = tuple(sorted(
                Path(str(item.get("filename") or "")).suffix.lower()
                or str(item.get("content_type") or "unknown")
                for item in record.get("attachments") or []
            )) or ("none",)
            unresolved_attachment_counts.update(formats)
            persisted_reason = str(record.get("review_reason") or "").split(":", 1)[0].strip()
            if persisted_reason:
                persisted_review_reason_counts[persisted_reason] += 1
            unresolved.append({"id_suffix": str(record["id"])[-8:], "provider": provider["provider"], "category": category, "intent": intent})

    print(json.dumps({
        "records": len(records),
        "provider_counts": dict(provider_counts),
        "category_counts": dict(category_counts),
        "intent_counts": dict(intent_counts),
        "unresolved_reason_counts": dict(unresolved_reason_counts),
        "unresolved_attachment_counts": dict(unresolved_attachment_counts),
        "persisted_review_reason_counts": dict(persisted_review_reason_counts),
        "recent": recent,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
