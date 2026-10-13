#!/usr/bin/env python3
"""Redacted local audit of captured intake records.

This tool intentionally prints aggregate counts and short record suffixes only.
It is for local QA; raw intake data must never be committed.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram-bot"))
from email_rules import classify_email_intent
from booking_types import infer_extended_type
from provider_rules import identify_email_provider


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("intake_dir", type=Path)
    args = parser.parse_args()
    index_path = args.intake_dir / "index.json"
    records = json.loads(index_path.read_text(encoding="utf-8"))
    provider_counts = Counter()
    category_counts = Counter()
    intent_counts = Counter()
    unresolved = []

    for record in records:
        folder = args.intake_dir / str(record["id"])
        body = read_text(folder / "body.txt")
        html = read_text(folder / "body.html")
        attachment_names = " ".join(str(item.get("filename") or "") for item in record.get("attachments") or [])
        attachment_text = []
        for item in record.get("attachments") or []:
            suffix = Path(str(item.get("filename") or "")).suffix.lower()
            if item.get("content_type") == "text/html" or suffix in {".html", ".htm"}:
                attachment_text.append(read_text(folder / str(item.get("path") or "")))
        source_text = "\n".join((body, html, attachment_names, *attachment_text))
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
        intent = classify_email_intent(str(record.get("subject") or ""), body + "\n" + html)
        intent_counts[intent] += 1
        if provider["provider"] == "unknown" or category == "unknown" or intent == "unknown":
            unresolved.append({"id_suffix": str(record["id"])[-8:], "provider": provider["provider"], "category": category, "intent": intent})

    print(json.dumps({
        "records": len(records),
        "provider_counts": dict(provider_counts),
        "category_counts": dict(category_counts),
        "intent_counts": dict(intent_counts),
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
