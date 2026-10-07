from __future__ import annotations

from typing import Any


RESTAURANT_MARKERS = (
    "restaurant",
    "reservation at",
    "dinner reservation",
    "lunch reservation",
    "table reservation",
    "מסעדה",
    "הזמנת שולחן",
    "שמירת שולחן",
)

INSURANCE_MARKERS = (
    "travel insurance",
    "insurance policy",
    "policy number",
    "emergency assistance",
    "ביטוח נסיעות",
    "פוליסת ביטוח",
    "מוקד חירום",
)


def infer_extended_type(ai: dict[str, Any] | None, text: str = "") -> str | None:
    ai = ai or {}
    declared = str(ai.get("type") or ai.get("document_type") or "").strip().lower()
    if declared in {"restaurant", "מסעדה"} or ai.get("restaurant_name"):
        return "restaurant"
    if declared in {"insurance", "travel_insurance", "ביטוח"} or ai.get("policy_number") or ai.get("insurer"):
        return "insurance"
    lowered = str(text or "").lower()
    if any(marker in lowered for marker in RESTAURANT_MARKERS):
        return "restaurant"
    if any(marker in lowered for marker in INSURANCE_MARKERS):
        return "insurance"
    return None


def normalize_insurance(ai: dict[str, Any] | None) -> dict[str, Any]:
    ai = ai or {}
    allowed = (
        "insurer",
        "policy_number",
        "insured_travelers",
        "valid_from",
        "valid_to",
        "coverage_summary",
        "covered_items",
        "exclusions",
        "what_to_do",
        "emergency_contacts",
        "document",
        "source_language",
        "review_state",
    )
    result: dict[str, Any] = {}
    for key in allowed:
        value = ai.get(key)
        if value not in (None, "", [], {}):
            result[key] = value
    result.setdefault("review_state", "needs_review")
    return result
