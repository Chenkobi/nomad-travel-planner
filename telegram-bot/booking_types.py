from __future__ import annotations

from typing import Any
import re


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
    # Footer links often contain generic insurance language unrelated to the
    # booking. Only inspect the substantive document text for text inference.
    lowered = re.split(r"\b(?:footer|unsubscribe|manage your preferences)\b", lowered, maxsplit=1)[0]
    if any(marker in lowered for marker in RESTAURANT_MARKERS):
        return "restaurant"
    insurance_context = any(marker in lowered for marker in ("travel insurance", "insurance policy", "policy number", "insured traveler", "coverage summary"))
    if insurance_context and ("policy number" in lowered or "coverage" in lowered or "insured" in lowered or "valid from" in lowered or "valid to" in lowered):
        return "insurance"
    if any(marker in lowered for marker in FLIGHT_FACT_MARKERS):
        return "flight"
    if any(marker in lowered for marker in HOTEL_FACT_MARKERS):
        return "hotel"
    # Booking.com uses short labelled fields rather than the longer phrases
    # above. Require two lodging labels so a generic "check-in" mention alone
    # cannot turn a non-hotel travel message into a hotel booking.
    booking_lodging_labels = ("property", "check-in", "check-out", "room", "accommodation")
    if sum(label in lowered for label in booking_lodging_labels) >= 2:
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


_POLICY_STATUS = ["included", "selected", "optional", "not_selected", "excluded", "unknown"]


def _schema_string(description: str = "") -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    if description:
        schema["description"] = description
    return schema


def _evidence_properties() -> dict[str, Any]:
    return {
        "source_page": {"type": "integer", "description": "1-based PDF page where this exact item appears; omit if unavailable."},
        "source_quote": _schema_string("Short exact quote from the source that supports this item; preserve original language."),
    }


def _policy_item_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    properties = {**properties, **_evidence_properties()}
    return {"type": "object", "properties": properties, "required": required}


def insurance_schema_properties() -> dict[str, Any]:
    """Gemini output fields for a source-grounded travel-insurance policy."""
    status = {"type": "string", "enum": _POLICY_STATUS}
    string_list = {"type": "array", "items": _schema_string()}
    coverage = _policy_item_schema({
        "title": _schema_string("Policy benefit, cover, condition, or exclusion heading."),
        "category": _schema_string("Normalized category only when explicitly supported."),
        "status": {**status, "description": "Whether the source says included, selected, optional, not selected, excluded, or unclear."},
        "summary": _schema_string("Concise Hebrew explanation of the source clause; do not broaden its meaning."),
        "limit": _schema_string("Exact stated amount/limit, including units and qualifiers."),
        "currency": _schema_string("Exact currency code or symbol when stated."),
        "deductible": _schema_string("Exact deductible/excess when stated."),
        "conditions": string_list,
    }, ["title", "status"])
    extension = _policy_item_schema({
        "name": _schema_string("Name of an extension, rider, add-on, or optional benefit."),
        "status": {**status, "description": "Distinguish selected/included from merely offered, not selected, excluded, or unknown."},
        "details": _schema_string("Concise Hebrew details grounded in the policy."),
        "limit": _schema_string("Exact stated limit, including units and qualifiers."),
        "currency": _schema_string("Exact currency code or symbol when stated."),
        "deductible": _schema_string("Exact deductible/excess when stated."),
        "conditions": string_list,
    }, ["name", "status"])
    contact = _policy_item_schema({
        "label": _schema_string("Emergency or assistance service name as shown in the policy."),
        "contact_name": _schema_string("Named organization or person, only if explicitly listed."),
        "phone": _schema_string("Phone number and dialing prefix exactly as printed."),
        "phone_from_abroad": _schema_string("Separate number explicitly designated for calls from abroad/outside Israel."),
        "email": _schema_string("Email address exactly as printed."),
        "whatsapp": _schema_string("WhatsApp contact exactly as printed, if any."),
        "hours": _schema_string("Stated service hours and availability."),
        "timezone": _schema_string("Timezone only if explicitly stated."),
        "country": _schema_string("Country/territory served when explicitly stated."),
        "scope": _schema_string("Service purpose, such as medical assistance or claims, as explicitly stated."),
        "languages": string_list,
    }, ["label"])
    claim_step = _policy_item_schema({
        "step": _schema_string("One explicit claim or emergency instruction, in concise Hebrew."),
        "deadline": _schema_string("Exact stated reporting/submission deadline, if any."),
        "contact": _schema_string("Contact/channel explicitly tied to this step."),
    }, ["step"])
    territory = _policy_item_schema({
        "area": _schema_string("Country/region/territory exactly as stated."),
        "status": {"type": "string", "enum": ["covered", "excluded", "restricted", "unknown"]},
        "conditions": string_list,
    }, ["area", "status"])
    return {
        "policy_number": _schema_string(),
        "insurer": _schema_string(),
        "insured_travelers": string_list,
        "valid_from": _schema_string("Policy start date, exactly/clearly stated."),
        "valid_to": _schema_string("Policy end date, exactly/clearly stated."),
        "coverage_summary": _schema_string("Short overview only; detailed sections must also be itemized below."),
        "covered_items": string_list,
        "exclusions": string_list,
        "what_to_do": string_list,
        "coverage_sections": {"type": "array", "items": coverage},
        "extensions": {"type": "array", "items": extension},
        "emergency_contacts": {"type": "array", "items": contact},
        "claims_steps": {"type": "array", "items": claim_step},
        "geographic_scope": {"type": "array", "items": territory},
        "document": _schema_string(),
        "source_language": _schema_string(),
    }


def _clean_policy_string(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    return str(value).strip()


def _normalize_policy_records(value: Any, fields: tuple[str, ...], list_fields: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    records = [value] if isinstance(value, dict) else value
    if not isinstance(records, list):
        return []
    result: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        item: dict[str, Any] = {}
        for key in fields:
            raw = record.get(key)
            if key == "source_page":
                if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 1000:
                    item[key] = raw
                continue
            if key in list_fields:
                values = raw if isinstance(raw, list) else [raw] if isinstance(raw, str) else []
                cleaned = [_clean_policy_string(entry) for entry in values]
                cleaned = [entry for entry in cleaned if entry]
                if cleaned:
                    item[key] = cleaned
                continue
            cleaned = _clean_policy_string(raw)
            if cleaned:
                item[key] = cleaned
        if item:
            result.append(item)
    return result


def normalize_insurance(ai: dict[str, Any] | None) -> dict[str, Any]:
    ai = ai if isinstance(ai, dict) else {}
    string_fields = (
        "insurer", "policy_number", "valid_from", "valid_to", "coverage_summary",
        "document", "source_language",
    )
    list_fields = ("insured_travelers", "covered_items", "exclusions", "what_to_do")
    result: dict[str, Any] = {}
    for key in string_fields:
        value = _clean_policy_string(ai.get(key))
        if value:
            result[key] = value
    for key in list_fields:
        raw = ai.get(key)
        values = raw if isinstance(raw, list) else [raw] if isinstance(raw, str) else []
        cleaned = [_clean_policy_string(value) for value in values]
        cleaned = [value for value in cleaned if value]
        if cleaned:
            result[key] = cleaned
    item_fields = {
        "coverage_sections": (("title", "category", "status", "summary", "limit", "currency", "deductible", "conditions", "source_page", "source_quote"), ("conditions",)),
        "extensions": (("name", "status", "details", "limit", "currency", "deductible", "conditions", "source_page", "source_quote"), ("conditions",)),
        "emergency_contacts": (("label", "contact_name", "phone", "phone_from_abroad", "email", "whatsapp", "hours", "timezone", "country", "scope", "languages", "source_page", "source_quote"), ("languages",)),
        "claims_steps": (("step", "deadline", "contact", "source_page", "source_quote"), ()),
        "geographic_scope": (("area", "status", "conditions", "source_page", "source_quote"), ("conditions",)),
    }
    for key, (fields, nested_lists) in item_fields.items():
        values = _normalize_policy_records(ai.get(key), fields, nested_lists)
        if values:
            result[key] = values
    result["review_state"] = "needs_review"
    return result


def insurance_extraction_instructions() -> str:
    """Focused instructions that keep policy extraction exhaustive and evidence-bound."""
    return (
        " If and only if type is insurance, inspect the entire policy: every page, table, endorsement, rider, appendix, and contact panel."
        " Inventory every coverage section as a separate item; a short coverage_summary is not a substitute."
        " For each item capture its exact title, included/selected/optional/not_selected/excluded/unknown status, stated limit and currency, deductible, and conditions. When available, include source page and a short verbatim quote; omit unavailable evidence rather than infer it."
        " Distinguish a benefit that is included or selected from one merely offered as optional; never infer that an offered extension was purchased."
        " Extract all emergency and assistance contacts, including separate domestic and phone_from_abroad numbers, email, WhatsApp, service purpose, hours, timezone, and languages when printed."
        " Extract each explicit claim/emergency step and deadline, plus covered, excluded, or restricted geographic areas. Include source page and a short verbatim quote when available; omit unavailable evidence rather than infer it."
        " Translate explanations into concise Hebrew when useful, but keep source_quote verbatim in the original language."
        " Never infer coverage, limits, selected extensions, phone numbers, deadlines, exclusions, or legal/medical advice from general knowledge; if a fact is unclear, mark unknown or leave it blank."
    )
