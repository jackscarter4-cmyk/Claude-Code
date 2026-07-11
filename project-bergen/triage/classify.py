"""Club-event classification. Fixed label set, 'unsure -> human' default.

Typed Commerce7-style webhooks classify deterministically in code (the type
field IS the classification — no model needed). Only free-text inbound email
goes to the classifier model (haiku, low effort); offline mode uses ordered
keyword rules. Anything that doesn't match confidently is 'unsure', which
always routes to a human — that is never a failure mode, it's the default.
"""
from common import llm

LABELS = ["failed_payment", "cancellation_request", "chargeback",
          "address_change", "skip_request", "tier_downgrade", "unsure"]

TYPED_MAP = {
    "subscription.payment_failed": "failed_payment",
    "membership.cancellation_requested": "cancellation_request",
    "payment.chargeback_opened": "chargeback",
    "customer.address_updated": "address_change",
    "subscription.skip_requested": "skip_request",
}

_KEYWORD_RULES = [  # ordered; first hit wins
    ("chargeback", ["chargeback", "dispute", "disputed the charge"]),
    ("cancellation_request", ["cancel"]),
    ("failed_payment", ["card declined", "payment failed", "card was declined", "expired card"]),
    ("address_change", ["new address", "moved to", "change my address", "update my address"]),
    ("skip_request", ["skip"]),
    ("tier_downgrade", ["downgrade", "smaller tier", "fewer bottles"]),
]

_SYSTEM = """You classify one wine-club member email into exactly one label from:
failed_payment, cancellation_request, chargeback, address_change, skip_request, tier_downgrade, unsure.
Rules: respond with the single label and nothing else. If the email is ambiguous,
mixed-intent, or you are not confident, respond 'unsure' - a human will read it,
and that is the correct outcome for anything unclear."""


def _fallback_freetext(text: str) -> str:
    lowered = text.lower()
    for label, keywords in _KEYWORD_RULES:
        if any(k in lowered for k in keywords):
            return label
    return "unsure"


def classify_event(event: dict) -> str:
    etype = event.get("type", "")
    if etype in TYPED_MAP:
        return TYPED_MAP[etype]
    if etype == "subscription.tier_change_requested":
        # Only downgrades have a playbook here; anything else -> human.
        return "tier_downgrade" if event.get("data", {}).get("direction") == "down" else "unsure"
    if etype == "email.inbound":
        text = f"{event['data'].get('subject', '')}\n{event['data'].get('body', '')}"
        if llm.mode() == "offline":
            return _fallback_freetext(text)
        label = llm.complete("classifier", _SYSTEM, text).strip().lower()
        return label if label in LABELS else "unsure"
    return "unsure"
