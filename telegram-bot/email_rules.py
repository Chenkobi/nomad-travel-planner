import re


TRAVEL_MARKERS = (
    "booking", "reservation", "confirmation", "itinerary", "check-in", "check in",
    "check-out", "check out", "hotel", "flight", "train", "rental car", "car rental",
    "attraction", "ticket", "voucher", "travel", "trip", "pickup", "drop-off",
    "cancellation", "cancelled", "canceled", "refund", "変更",
)
CANCEL_MARKERS = ("cancelled", "canceled", "cancellation", "cancel", "refund", "void")
MODIFY_MARKERS = ("changed", "change", "modified", "modification", "updated", "update", "rebooked", "rescheduled")
CONFIRM_MARKERS = ("confirmation", "confirmed", "reservation", "booking", "voucher", "ticket", "itinerary")


def _normalized(subject, body):
    return re.sub(r"\s+", " ", f"{subject or ''} {body or ''}").strip().casefold()


def looks_like_travel_email(subject, body):
    text = _normalized(subject, body)
    return any(marker in text for marker in TRAVEL_MARKERS)


def classify_email_intent(subject, body):
    text = _normalized(subject, body)
    if any(marker in text for marker in CANCEL_MARKERS):
        return "cancelled"
    if any(marker in text for marker in MODIFY_MARKERS):
        return "modified"
    if any(marker in text for marker in CONFIRM_MARKERS):
        return "confirmed"
    return "unknown"
