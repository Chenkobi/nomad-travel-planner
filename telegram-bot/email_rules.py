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
    if re.search(r"\b(?:support|helpdesk|ticket|case|issue)\b", text) and not re.search(
        r"\b(?:hotel|flight|train|reservation|itinerary|check[- ]in|check[- ]out)\b", text
    ):
        return False
    patterns = [rf"\b{re.escape(marker)}\b" for marker in TRAVEL_MARKERS if re.search(r"[a-z]", marker)]
    patterns.extend(rf"{re.escape(marker)}" for marker in TRAVEL_MARKERS if not re.search(r"[a-z]", marker))
    return any(re.search(pattern, text) for pattern in patterns)


def _strong_confirmation_subject(subject):
    subject = str(subject or "").casefold()
    return bool(re.search(
        r"\b(?:confirmed|booking\s+confirmed|reservation\s+confirmed|travel itinerary|e-ticket|voucher|ticket)\b",
        subject,
    ))


def _strong_cancellation_event(text):
    # Deliberately require an event sentence. Policy/footer language must not match.
    text = re.sub(r"\b(?:cancellation|cancellable|cancelation)\s+policy\b", "", text)
    text = re.sub(r"\bfree\s+cancellation\b|\bcancel\s+anytime\b|\bmay\s+cancel\b", "", text)
    return bool(re.search(
        r"\b(?:your|the)\s+(?:reservation|booking|confirmation|itinerary|flight|hotel|trip|rental)\s+"
        r"(?:(?:has been|was|is now|is)\s+)?(?:cancelled|canceled|voided)\b"
        r"|\b(?:reservation|booking|confirmation|itinerary|flight|hotel|trip|rental)\b.{0,30}\b(?:cancelled|canceled|voided)\b"
        r"|\b(?:we|the airline|the hotel|the provider)\s+(?:have|has)\s+cancelled\b"
        r"|\bcancellation\s+confirmed\b"
        r"|\b(?:booking|reservation|hotel|rental|trip|flight|itinerary)\s+cancellation\b"
        r"|\bcancellation\s+(?:of\s+)?(?:your\s+)?(?:booking|reservation|hotel|rental|trip|flight|itinerary)\b"
        r"|\b(?:refund issued|refund processed|refunded in full)\b",
        text,
    ))


def _strong_modification_event(text):
    text = re.sub(r"\blast updated\b|\bupdate your (?:email )?preferences\b", "", text)
    return bool(re.search(
        r"\b(?:your|the)\s+(?:reservation|booking|itinerary|flight|hotel|trip|dates|date|time|room|route)\s+"
        r"(?:(?:has(?:\s+been)?|was|is now|is)\s+)?(?:changed|modified|updated|rebooked|rescheduled)\b"
        r"|\b(?:your|the)\s+(?:flight|booking|reservation)\s+has\s+been\s+(?:changed|rescheduled)\b",
        text,
    ))


def classify_email_intent(subject, body):
    subject_text = re.sub(r"\s+", " ", str(subject or "")).strip().casefold()
    text = _normalized(subject, body)

    # Explicit lifecycle events in the subject outrank generic booking words.
    if _strong_cancellation_event(subject_text):
        return "cancelled"
    if _strong_modification_event(subject_text):
        return "modified"
    # Confirmation subjects are the strongest signal for provider booking emails.
    # This prevents policy/footer terms in the body from changing a new booking.
    if _strong_confirmation_subject(subject_text):
        return "confirmed"
    if _strong_cancellation_event(subject_text):
        return "cancelled"
    if _strong_modification_event(text):
        return "modified"
    if any(marker in subject_text for marker in ("confirmation", "reservation", "booking", "itinerary", "voucher", "ticket")):
        return "confirmed"
    return "unknown"
