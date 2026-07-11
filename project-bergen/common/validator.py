"""EVAL GATE #3 — zero-hallucinated-price validator (hard gate, code not judge).

Every generated draft passes through validate_draft() before it may enter the
approval queue. Parses each dollar amount, capacity number, and date out of
the draft and requires it to be grounded:

  - Dollar amounts: must exist in ground_truth/packages.yaml as a
    NON-conflicted price. Quoting a conflicted price (e.g. the Private Suite
    $150/$185) is always a violation. This is stricter than "escape phrase
    excuses everything": an explicit unsupported number is never excused —
    the escape phrase is for what a draft does NOT say.
  - Capacity numbers: must exist in packages.yaml OR be echoed from the
    source message (the guest's own "party of 25" is grounded in the source).
  - Dates: must be echoed from the source message OR be a published closed
    date in packages.yaml.

A draft that has no answer must instead contain the literal escape phrase
("I'll confirm and follow up"). One violation fails the build.
"""
import re
from dataclasses import dataclass, field

from common import kb

DOLLAR_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)")
CAPACITY_RE = re.compile(
    r"(?:(?:party|group|team|celebration|wedding|event|offsite)\s+(?:of|for)\s+(\d{1,4}))"
    r"|(?:\b(\d{1,4})\s*(?:\+\s*)?(?:guests?|people|persons?|pp\b|ppl\b|attendees?|invitados?|personas?))"
    r"|(?:up\s+to\s+(\d{1,4})\b)",
    re.IGNORECASE,
)
DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DATE_US_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
MONTHS = ("january february march april may june july august september "
          "october november december").split()
DATE_NAME_RE = re.compile(
    r"\b(" + "|".join(MONTHS) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE)


def extract_dollars(text):
    return [float(m.replace(",", "")) for m in DOLLAR_RE.findall(text)]


def extract_capacities(text):
    out = []
    for m in CAPACITY_RE.finditer(text):
        num = next(g for g in m.groups() if g)
        out.append(int(num))
    return out


def extract_dates(text):
    """Return a set of (month, day) tuples for every date-looking string."""
    found = set()
    for y, mo, d in DATE_ISO_RE.findall(text):
        found.add((int(mo), int(d)))
    for mo, d, _y in DATE_US_RE.findall(text):
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            found.add((int(mo), int(d)))
    for name, d in DATE_NAME_RE.findall(text):
        found.add((MONTHS.index(name.lower()) + 1, int(d)))
    return found


@dataclass
class ValidationResult:
    ok: bool
    violations: list = field(default_factory=list)


def validate_draft(draft_text: str, source_text: str = "") -> ValidationResult:
    violations = []
    allowed_dollars = kb.allowed_dollar_amounts()
    conflicted = kb.conflicted_dollar_amounts()

    for amount in extract_dollars(draft_text):
        if amount in conflicted:
            violations.append(
                f"conflicted_price: ${amount:g} is published inconsistently and must never be quoted")
        elif amount not in allowed_dollars:
            violations.append(
                f"unsupported_price: ${amount:g} does not exist in ground_truth/packages.yaml")

    allowed_caps = kb.allowed_bare_numbers()
    source_caps = set(extract_capacities(source_text))
    source_caps |= {int(n) for n in re.findall(r"\b(\d{1,4})\b", source_text)}
    for cap in extract_capacities(draft_text):
        if cap not in allowed_caps and cap not in source_caps:
            violations.append(
                f"unsupported_capacity: {cap} is not in packages.yaml nor echoed from the source message")

    closed = {(int(d[5:7]), int(d[8:10])) for d in kb.closed_dates()}
    source_dates = extract_dates(source_text)
    for dt in extract_dates(draft_text):
        if dt not in source_dates and dt not in closed:
            violations.append(
                f"unsupported_date: month/day {dt[0]:02d}-{dt[1]:02d} is not from the source message or packages.yaml")

    return ValidationResult(ok=not violations, violations=violations)


def has_escape_phrase(draft_text: str) -> bool:
    """Curly/straight apostrophes both count — the phrase is checked literally
    otherwise."""
    phrase = kb.escape_phrase().lower().replace("’", "'")
    return phrase in draft_text.lower().replace("’", "'")
