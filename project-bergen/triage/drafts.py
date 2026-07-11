"""Drafting for club exceptions: member-facing reply + internal action note.

Sonnet (medium effort) when a key is present; deterministic templates
offline. Both paths are hard-gated by common/validator.py before anything
enters the approval queue — grounding is enforced in code, not prompts.
"""
import json

from common import kb, llm, validator

SIGNOFF = "Warmly,\nThe Macari Wine Club Team"

PUBLIC_RISK_MARKERS = ["posting about this", "review", "everywhere i can",
                       "called five times", "called 5 times", "bbb", "attorney"]


def public_risk(event: dict) -> bool:
    text = json.dumps(event).lower()
    return any(m in text for m in PUBLIC_RISK_MARKERS)


def _amount(event):
    amt = event.get("data", {}).get("amount")
    return f"${amt}" if isinstance(amt, (int, float)) else None


def _offline_templates(event: dict, label: str):
    first = event["member"]["name"].split()[0]
    d = event.get("data", {})
    amt = _amount(event)
    esc = kb.escape_phrase()

    if label == "failed_payment":
        member = (f"Hi {first},\n\nJust a friendly note - the card on file didn't go through for your "
                  f"{event['member']['tier']} shipment{f' ({amt})' if amt else ''}. This happens all the "
                  "time (expired cards especially) and your wines are safely set aside for you.\n\n"
                  "Whenever convenient, reply here or call the tasting room and we'll update the card "
                  "in under a minute. No rush and no fees.\n\n" + SIGNOFF)
        internal = (f"ACTION: retry billing after member updates card. Reason: {d.get('reason')}. "
                    f"Attempt {d.get('attempt')}. Hold shipment; do NOT re-attempt charge without member contact. "
                    "SLA 48h.")
    elif label == "cancellation_request":
        boundary = d.get("shipments_completed") is not None and \
            d.get("shipments_completed") < kb.load_kb()["wine_club"]["minimum_commitment_shipments"]
        member = (f"Hi {first},\n\nWe've received your cancellation request and I'm personally making sure "
                  "it's processed - you'll get a written confirmation from us, and you will not be billed "
                  "again in the meantime.\n\nIf anything we did fell short, I'd genuinely love to know. "
                  "And if a lighter option would suit you better, I'm happy to walk through it on a quick "
                  "call - but either way, your cancellation is being handled now.\n\n" + SIGNOFF)
        internal = ("ACTION: process cancellation in Commerce7 TODAY and send written confirmation. "
                    "Suppress next billing cycle FIRST, then complete paperwork. SLA 24h.")
        if boundary:
            internal += (f" NOTE: member has completed {d['shipments_completed']} of the "
                         f"{kb.load_kb()['wine_club']['minimum_commitment_shipments']}-shipment minimum - "
                         "manager call required before processing; do NOT auto-enforce the minimum on an "
                         "unhappy member.")
        if public_risk(event):
            internal += (" RISK: message signals public-review escalation (repeated unanswered calls, "
                         "billed after cancelling). Treat as same-day: call the member personally, "
                         "confirm the cancellation verbally, and own the refund question on that call.")
            member = (f"Hi {first},\n\nYou're right, and I'm sorry - a cancellation request should never "
                      "take repeated calls. I'm treating this personally today: your cancellation is being "
                      f"processed now, you will not be billed again, and on the recent charge {esc} with "
                      "you by phone before end of day.\n\nYou'll hear from me directly, not a queue.\n\n"
                      + SIGNOFF)
    elif label == "chargeback":
        member = (f"Hi {first},\n\nWe've just seen a payment dispute on your recent club shipment"
                  f"{f' ({amt})' if amt else ''} and I want to sort it out for you directly - disputes are "
                  "usually a sign we missed something on our end.\n\nCould we set up a five-minute call today? "
                  "If the shipment didn't arrive or wasn't right, we'll make it right immediately.\n\n" + SIGNOFF)
        internal = (f"ACTION (SAME-DAY): chargeback opened, reason '{d.get('reason_code')}'. "
                    f"Processor response due {d.get('processor_deadline')}. Pull fulfillment + tracking records "
                    "now; call member before responding to processor. Never auto-contest a member dispute.")
    elif label == "address_change":
        member = (f"Hi {first},\n\nThanks for the updated address - we've flagged it for your upcoming "
                  "shipment so your wines follow you, not your old doorstep. If the move date is close to "
                  "the ship date, reply here and we'll hold the box until you're settled.\n\n" + SIGNOFF)
        internal = (f"ACTION: verify address change applied to pending shipment "
                    f"({d.get('old_state')} -> {d.get('new_state')}). "
                    f"Shipment status: {d.get('next_shipment_status')}. SLA 24h.")
        if d.get("next_shipment_status") == "label_printed":
            internal += " URGENT: label already printed - manually intercept and reprint before carrier pickup."
    elif label == "skip_request":
        member = (f"Hi {first},\n\nDone - we've noted your request to skip the {d.get('shipment', 'upcoming')} "
                  "shipment. You won't be billed for it, and your membership simply picks back up next "
                  "quarter. Enjoy the summer, and come see us at the tasting room anytime.\n\n" + SIGNOFF)
        internal = (f"ACTION: apply skip for {d.get('shipment')} in Commerce7; confirm billing suppressed "
                    "for that cycle. SLA 48h.")
    elif label == "tier_downgrade":
        member = (f"Hi {first},\n\nOf course - we've received your request to move from the "
                  f"{d.get('from_tier')} tier to the {d.get('to_tier')} tier, and it will take effect with "
                  "your next shipment. No change fees, no fuss - we're just glad to have you in the club at "
                  "the level that fits.\n\n" + SIGNOFF)
        internal = (f"ACTION: change tier {d.get('from_tier')} -> {d.get('to_tier')} effective next cycle; "
                    "verify next billing amount matches the new tier before it runs. SLA 48h.")
    else:  # unsure -> human
        member = (f"Hi {first},\n\nThanks for reaching out - a real person on our club team is reading "
                  "your note and will call you within the day so we get you exactly the right answer.\n\n" + SIGNOFF)
        internal = ("ACTION (HUMAN READ REQUIRED): free-text message did not match a known exception "
                    "playbook. Read the original message and call the member. SLA 4h.")
    return member, internal


_DRAFT_SYSTEM = """You draft two texts for a family-owned winery's wine-club team:
(1) a member-facing email reply, (2) an internal action note.
Voice: warm, personal, hospitality-forward; sign the member email exactly
'Warmly,\\nThe Macari Wine Club Team'.
HARD RULES: quote no dollar amount, capacity, or date that is not present in
the provided GROUND TRUTH or the member's own message. If you lack a fact,
write the literal phrase "I'll confirm and follow up". Keep the member email
under 150 words with one concrete next step. Never promise to waive, refund,
or enforce anything not stated in ground truth.
Output strict JSON: {"member_email": "...", "internal_note": "..."}"""


def draft(event: dict, label: str):
    """Returns (member_body, internal_note, draft_mode)."""
    if llm.mode() == "offline":
        member, internal = _offline_templates(event, label)
        mode = "offline-template"
    else:
        ground = json.dumps({"wine_club": kb.load_kb()["wine_club"],
                             "policies": kb.load_kb()["policies"]}, default=str)
        raw = llm.complete("drafter", _DRAFT_SYSTEM,
                           f"GROUND TRUTH:\n{ground}\n\nCLASSIFICATION: {label}\n\nEVENT:\n{json.dumps(event)}")
        try:
            parsed = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
            member, internal = parsed["member_email"], parsed["internal_note"]
            mode = "sonnet"
        except (ValueError, KeyError):
            member, internal = _offline_templates(event, label)
            mode = "offline-template (LLM output unparseable)"

    # Hard gate — grounding is code, not vibes. An LLM draft that fails
    # validation is replaced by the grounded template, and the failure logged.
    source = json.dumps(event, ensure_ascii=False)
    for text in (member, internal):
        result = validator.validate_draft(text, source)
        if not result.ok:
            member, internal = _offline_templates(event, label)
            mode = f"offline-template (LLM draft failed validation: {result.violations})"
            break
    return member, internal, mode
