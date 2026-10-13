from __future__ import annotations

import re
from email.utils import parseaddr
from typing import Any


_PROVIDER_RULES: tuple[dict[str, Any], ...] = (
    {
        "provider": "booking.com",
        "category_hint": "marketplace",
        "domains": ("booking.com",),
        "markers": ("booking.com", "booking confirmation"),
    },
    {
        "provider": "trip.com",
        "category_hint": "marketplace",
        "domains": ("trip.com",),
        "markers": ("trip.com",),
    },
    {
        "provider": "sixt",
        "category_hint": "car_rental",
        "domains": ("sixt.com", "sixt.eu"),
        "markers": ("sixt", "s.i.x.t"),
    },
    {
        "provider": "car_trawler",
        "category_hint": "car_rental",
        "domains": ("cartrawler.com",),
        "markers": ("cartrawler",),
    },
    {
        "provider": "wizz_air",
        "category_hint": "flight",
        "domains": ("wizzair.com", "wizzair.hu"),
        "markers": ("wizz air", "wizzair"),
    },
    {
        "provider": "arkia",
        "category_hint": "flight",
        "domains": ("arkia.co.il",),
        "markers": ("arkia", "ארקיע"),
    },
    {
        "provider": "israir",
        "category_hint": "flight",
        "domains": ("israir.co.il",),
        "markers": ("israir", "ישראייר"),
    },
    {
        "provider": "el_al_group",
        "category_hint": "flight",
        "domains": ("elal.com", "sundor.co.il"),
        "markers": ("el al", "elal", "sundor", "sun d'or", "אל על"),
    },
    {
        "provider": "marriott",
        "category_hint": "hotel",
        "domains": ("marriott.com", "res-marriott.com"),
        "markers": ("marriott",),
    },
)


def _sender_domains(*values: str) -> set[str]:
    domains: set[str] = set()
    for value in values:
        text = str(value or "")
        addresses = re.findall(r"[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})", text, re.I)
        if not addresses:
            address = parseaddr(text)[1]
            if "@" in address:
                addresses = [address.rsplit("@", 1)[1]]
        domains.update(domain.casefold().rstrip(".") for domain in addresses)
    return domains


def identify_email_provider(sender: str = "", reply_to: str = "", subject: str = "", body: str = "") -> dict[str, Any]:
    """Identify the booking provider before document classification.

    Sender/reply-to domains are weighted above forwarded body text. The result
    is evidence, not a final booking type; marketplaces intentionally return a
    ``marketplace`` category hint so the document facts decide hotel/flight/etc.
    """
    sender_domains = _sender_domains(sender, reply_to)
    text = " ".join((str(subject or ""), str(body or ""))).casefold()
    candidates: list[tuple[int, dict[str, Any], list[str]]] = []

    for rule in _PROVIDER_RULES:
        evidence: list[str] = []
        score = 0
        for domain in rule["domains"]:
            if domain in sender_domains:
                score += 100
                evidence.append(f"sender_domain:{domain}")
        for marker in rule["markers"]:
            if marker.casefold() in text:
                score += 10
                evidence.append(f"text:{marker}")
        if score:
            candidates.append((score, rule, evidence))

    if not candidates:
        return {"provider": "unknown", "category_hint": "unknown", "confidence": 0.0, "evidence": []}

    candidates.sort(key=lambda item: (-item[0], item[1]["provider"]))
    score, rule, evidence = candidates[0]
    confidence = 0.99 if score >= 100 else 0.8 if score >= 10 else 0.0
    return {
        "provider": rule["provider"],
        "category_hint": rule["category_hint"],
        "confidence": confidence,
        "evidence": evidence,
    }
