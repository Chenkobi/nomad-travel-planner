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

CAR_RENTAL_PROVIDER_MARKERS = (
    "avis",
    "sixt",
    "hertz",
    "enterprise",
    "budget rent a car",
    "europcar",
    "alamo",
    "autoeurope",
    "auto europe",
    "כרטיס השכרת רכב",
)

TRAIN_MARKERS = (
    "deutsche bahn",
    "db bahn",
    "train booking",
    "train ticket",
    "rail ticket",
    "רכבת",
)

CAR_RENTAL_FACT_MARKERS = (
    "car rental",
    "rental agreement",
    "pickup location",
    "pick-up location",
    "drop-off",
    "vehicle type",
    "השכרת רכב",
    "איסוף רכב",
    "החזרת רכב",
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

FLIGHT_FACT_MARKERS = (
    "flight number",
    "departure airport",
    "arrival airport",
    "boarding pass",
    "flight itinerary",
    "טיסת",
    "מספר טיסה",
)

HOTEL_FACT_MARKERS = (
    "hotel reservation",
    "hotel booking",
    "check-in date",
    "check-out date",
    "room type",
    "guest name",
    "accommodation details",
    "הזמנת מלון",
)

ATTRACTION_MARKERS = (
    "museum ticket",
    "admission ticket",
    "guided tour",
    "attraction reservation",
    "single ticket",
    "כרטיס כניסה",
    "סיור מודרך",
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
    if any(marker in lowered for marker in FLIGHT_FACT_MARKERS):
        return "flight"
    if any(marker in lowered for marker in HOTEL_FACT_MARKERS):
        return "hotel"
    if any(marker in lowered for marker in ATTRACTION_MARKERS):
        return "attraction"
    if any(marker in lowered for marker in TRAIN_MARKERS):
        return "train"
    if any(provider in lowered for provider in CAR_RENTAL_PROVIDER_MARKERS) and any(marker in lowered for marker in CAR_RENTAL_FACT_MARKERS):
        return "car_rental"
    if any(marker in lowered for marker in CAR_RENTAL_FACT_MARKERS):
        return "car_rental"
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
