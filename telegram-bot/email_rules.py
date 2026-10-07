import re


TRAVEL_MARKERS = (
    "booking", "reservation", "confirmation", "itinerary", "check-in", "check in",
    "check-out", "check out", "hotel", "flight", "train", "rental car", "car rental",
    "attraction", "ticket", "voucher", "travel", "trip", "pickup", "drop-off",
    "cancellation", "cancelled", "canceled", "refund", "変更",
)
CANCEL_MARKERS = ("cancelled", "canceled", "cancel", "void")
MODIFY_MARKERS = ("changed", "change", "modified", "modification", "updated", "update", "rebooked", "rescheduled")
CONFIRM_MARKERS = ("confirmation", "confirmed", "reservation", "booking", "voucher", "ticket", "itinerary")


def _normalized(subject, body):
    return re.sub(r"\s+", " ", f"{subject or ''} {body or ''}").strip().casefold()


def looks_like_travel_email(subject, body):
    text = _normalized(subject, body)
    return any(marker in text for marker in TRAVEL_MARKERS)


def classify_email_intent(subject, body):
    text = _normalized(subject, body)
    cancellation_language = re.search(r"\b(?:reservation|booking|confirmation|itinerary|flight|hotel|trip)\b.{0,60}\b(?:cancelled|canceled|cancel|voided)\b|\b(?:cancelled|canceled|voided)\b.{0,60}\b(?:reservation|booking|confirmation|itinerary|flight|hotel|trip)\b|\b(?:refund issued|refund processed|refunded in full|cancellation confirmed)\b", text)
    if cancellation_language:
        return "cancelled"
    modification_text = re.sub(r"\blast updated\b|\bupdate your (?:email )?preferences\b", "", text)
    modification_language = re.search(r"\b(?:reservation|booking|itinerary|flight|hotel|trip|dates|date|time|room|route)\b.{0,60}\b(?:changed|change|modified|modification|updated|update|rebooked|rescheduled)\b|\b(?:changed|modified|rebooked|rescheduled)\b.{0,60}\b(?:reservation|booking|itinerary|flight|hotel|trip|dates|date|time|room|route)\b", modification_text)
    if modification_language:
        return "modified"
    if any(marker in text for marker in CONFIRM_MARKERS):
        return "confirmed"
    return "unknown"
