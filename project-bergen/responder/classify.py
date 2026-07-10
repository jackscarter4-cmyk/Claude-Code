"""Inquiry intent + urgency classification (haiku, low effort; ordered
keyword rules offline). Fixed intent set; anything unmatched -> 'unsure',
which routes to a human — never a failure."""
import re

from common import kb, llm, validator

INTENTS = ["wedding", "corporate_event", "group_tasting", "private_suite",
           "press_media", "general_pricing", "complaint",
           "club_billing_misroute", "spam", "unsure"]

URGENCY = {"wedding": "high", "complaint": "high", "club_billing_misroute": "high",
           "press_media": "medium", "spam": "none"}

_RULES = [  # ordered; first hit wins
    ("spam", ["seo", "backlink", "guaranteed ranking", "boost your traffic", "crypto"]),
    ("club_billing_misroute", [("wine club", "membership"), "club member", "membership billing"]),
    ("press_media", ["journalist", "writing a feature", "writing a story", "press",
                     "interview", "publication", "editor"]),
    ("complaint", ["disappointed", "speak to a manager", "unacceptable", "refund",
                   "never came", "very unhappy"]),
    ("wedding", ["wedding", "fiance", "fiancé", "ceremony", "reception"]),
    ("corporate_event", ["offsite", "corporate", "our company", "team of"]),
    ("private_suite", ["tasting suite", "private suite"]),
    ("group_tasting", ["bachelorette", "birthday", "tasting for", "group of",
                       "party of", "cata", "grupo", "despedida", "cumpleanos", "cumpleaños"]),
    ("general_pricing", ["how much", "price", "cost", "rates"]),
]

_SYSTEM = """You classify one inbound email to a winery's events inbox into exactly one intent:
wedding, corporate_event, group_tasting, private_suite, press_media, general_pricing,
complaint, club_billing_misroute, spam, unsure.
Notes: a complaint disguised as a booking inquiry is 'complaint'. A wine-club billing
question sent to the events inbox is 'club_billing_misroute'. If unclear, answer
'unsure' - a human reads those, and that is the correct choice when in doubt.
Respond with the single label and nothing else."""


def _fallback(text: str) -> str:
    lowered = text.lower()
    for label, keywords in _RULES:
        for k in keywords:
            if isinstance(k, tuple):  # all terms must appear
                if all(part in lowered for part in k):
                    return label
            elif k in lowered:
                return label
    return "unsure"


def guest_count(text: str):
    counts = validator.extract_capacities(text)
    return max(counts) if counts else None


def classify_inquiry(inq: dict) -> dict:
    text = f"{inq.get('subject', '')}\n{inq.get('body', '')}"
    if llm.mode() == "offline":
        intent = _fallback(text)
    else:
        label = llm.complete("classifier", _SYSTEM, text).strip().lower()
        intent = label if label in INTENTS else "unsure"

    count = guest_count(text)
    wedding_cap = kb.experience("meadowlark_weddings")["capacity_seated"]
    over_capacity = bool(intent == "wedding" and count and count > wedding_cap)

    closed = {(int(d[5:7]), int(d[8:10])) for d in kb.closed_dates()}
    closed_date = bool(validator.extract_dates(text) & closed)

    return {"intent": intent, "urgency": URGENCY.get(intent, "normal"),
            "guest_count": count, "over_capacity": over_capacity,
            "closed_date": closed_date,
            "language": inq.get("language", "en")}
