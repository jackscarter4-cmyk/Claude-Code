"""Inquiry response drafting. Sonnet (grounded ONLY in packages.yaml) when a
key is present; deterministic grounded templates offline. Either way the
draft passes the code-level validator before it can enter the approval queue.

Style contract: warm, concise, Macari's family-owned voice, always one
concrete next step, never a price that isn't in the KB, signed
'The Macari Events Team'.
"""
import json
import re

from common import kb, llm, validator

SIGNOFF = "Warmly,\nThe Macari Events Team"


def guess_first_name(inq: dict):
    """Only trust an obvious sign-off; otherwise greet generically."""
    lines = [l.strip() for l in inq.get("body", "").strip().splitlines() if l.strip()]
    if not lines:
        return None
    last = lines[-1]
    m = re.search(r"(?:thanks[!.,]?|gracias[,!]?|best,|regards,|-)\s*([A-Za-z]+)\s*$",
                  last, re.IGNORECASE)
    if m and m.group(1)[0].isupper():
        return m.group(1)
    if re.fullmatch(r"[A-Z][a-zA-Z]+(\s(&|and)\s[A-Z][a-zA-Z]+)?", last):
        return last.split()[0]
    return None


def _greeting(inq, language="en"):
    name = guess_first_name(inq)
    if language == "es":
        return f"Hola {name}," if name else "Hola,"
    return f"Hi {name}," if name else "Hello,"


def _offline_template(inq: dict, cls: dict) -> str | None:
    """Returns the draft body, or None when no reply should be drafted (spam)."""
    intent = cls["intent"]
    esc = kb.escape_phrase()
    seated = kb.experience("seated_tasting")
    barrel = kb.experience("barrel_cellar")
    wedding = kb.experience("meadowlark_weddings")
    greet = _greeting(inq, cls["language"])

    if intent == "spam":
        return None

    if intent == "wedding":
        if cls["over_capacity"]:
            return (f"{greet}\n\nCongratulations, and thank you for thinking of us! Meadowlark, our "
                    f"dedicated event property, hosts weddings across {wedding['buildings']} buildings and "
                    f"{wedding['acres']} acres, seating up to {wedding['capacity_seated']} guests. For a "
                    f"celebration of {cls['guest_count']}, {esc} with our events director on exactly how "
                    "we could make the numbers work before you plan around it.\n\nCould we set up a quick "
                    "call this week, or better yet, a visit? Mornings are beautiful here.\n\n" + SIGNOFF)
        return (f"{greet}\n\nCongratulations - and thank you, it means a lot that the property stayed "
                f"with you. Meadowlark, our dedicated wedding venue, spans {wedding['buildings']} buildings "
                f"and {wedding['acres']} acres and comfortably seats up to {wedding['capacity_seated']} "
                f"guests, so your celebration fits beautifully. Every wedding is a custom proposal, so "
                "rather than quote a generic number, I'd love to check availability for your date and walk "
                "the grounds with you.\n\nWould a site visit this month suit you both? I'll bring the "
                "calendar.\n\n" + SIGNOFF)

    if intent == "corporate_event":
        return (f"{greet}\n\nWe'd love to host your team. As reference points: our seated tasting with "
                f"charcuterie is ${seated['price_per_guest']:g} per guest, and the Barrel Cellar experience "
                f"is ${barrel['price_per_guest']:g} per guest for more intimate groups. For a group your "
                f"size we'd design it as a private event across our spaces, and {esc} with a tailored "
                "proposal.\n\nCould we grab fifteen minutes by phone this week to talk headcount, date, "
                "and food? Reply with two times that work and it's booked.\n\n" + SIGNOFF)

    if intent == "group_tasting":
        if cls["language"] == "es":
            return (f"{_greeting(inq, 'es')}\n\nGracias por escribirnos - nos encantaria recibirlos. "
                    f"Nuestra cata sentada con tabla de charcuterie cuesta ${seated['price_per_guest']:g} "
                    f"por persona, para grupos de hasta {seated['max_guests']} personas. Tambien ofrecemos "
                    f"la experiencia Barrel Cellar por ${barrel['price_per_guest']:g} por persona.\n\n"
                    "Para reservar, respondanos con la fecha y la hora preferida y lo confirmamos "
                    "enseguida.\n\nUn cordial saludo,\nThe Macari Events Team")
        if cls["closed_date"]:
            return (f"{greet}\n\nThank you for thinking of us for the holiday! One honest note first: "
                    "we're closed on November 26 (Thanksgiving Day), so we can't host a tasting that day. "
                    "The rest of that weekend we'd be delighted to have your family - our seated tasting "
                    f"with charcuterie is ${seated['price_per_guest']:g} per guest.\n\nReply with another "
                    "day that weekend and a preferred time, and I'll hold a table for you right away.\n\n"
                    + SIGNOFF)
        return (f"{greet}\n\nWhat a fun occasion - we'd love to host your group! Our seated tasting with "
                f"charcuterie is ${seated['price_per_guest']:g} per guest and works beautifully for groups "
                f"up to {seated['max_guests']}. If you'd like something more intimate, the Barrel Cellar "
                f"experience is ${barrel['price_per_guest']:g} per guest for groups of "
                f"{barrel['min_guests']} to {barrel['max_guests']}.\n\nReply with your preferred time and "
                "I'll hold space for you today - summer weekends do fill up.\n\n" + SIGNOFF)

    if intent == "private_suite":
        # The KB carries two published prices for the Suite (conflict: true).
        # Refusing to quote is the correct behavior — and it's the live demo
        # of the stale-surface audit finding. No number leaves this function.
        return (f"{greet}\n\nGreat choice - the Private Tasting Suite is one of our favorite ways to host "
                "a group. You've caught something real: our site currently shows two different rates for "
                f"the Suite on two pages, and I'd rather be straight with you than guess - {esc} today "
                "with the exact rate, in writing.\n\nMeanwhile, shall I pencil in your preferred date "
                "so the Suite is held while we confirm? Just reply with the date and headcount.\n\n" + SIGNOFF)

    if intent == "press_media":
        return (f"{greet}\n\nThank you for reaching out - we'd be glad to help with your piece. I'm "
                "looping in our director, who handles press directly and can arrange the visit, the "
                "interview, and a walk through the vineyard program.\n\nYou'll hear from us within one "
                "business day to find a time that fits your deadline.\n\n" + SIGNOFF)

    if intent == "complaint":
        return (f"{greet}\n\nThank you for telling us this directly - I'm sorry, that is not the visit we "
                "want anyone to have. A manager will call you today to hear what happened and make it "
                "right; no scripts, just a real conversation.\n\nIf there's a number and time that works "
                "best today, reply here and we'll call then.\n\n" + SIGNOFF)

    if intent == "club_billing_misroute":
        return (f"{greet}\n\nThanks for flagging this - you've reached our events inbox, but I'm not going "
                "to bounce you around: I've routed your billing question directly to our wine-club team "
                "with a same-day flag, and a real person there will call you today.\n\nIf you'd rather "
                "reach them first, just reply here and I'll connect you straight away.\n\n" + SIGNOFF)

    if intent == "general_pricing":
        return (f"{greet}\n\nHappy to help! Our seated tasting with charcuterie is "
                f"${seated['price_per_guest']:g} per guest, and we have private options from barrel-cellar "
                "tastings to full events depending on your group.\n\nTell me two things - roughly how many "
                "people, and what date you have in mind - and I'll send exact options and hold space for "
                "you today.\n\n" + SIGNOFF)

    # unsure -> human, warm holding reply
    return (f"{greet}\n\nThanks so much for reaching out. I want to make sure you get a real answer rather "
            "than a canned one, so a member of our events team is reading your note and will reply "
            "personally within the day.\n\n" + SIGNOFF)


_DRAFT_SYSTEM = """You draft a reply from a family-owned winery's events team.
Voice: warm, concise, hospitality-forward. Under 150 words. Exactly one concrete
next step (a call window, a site visit, or 'reply with X and Y'). Sign exactly
'Warmly,\\nThe Macari Events Team'.
HARD RULES: every price, capacity, or date must come from the provided GROUND
TRUTH or the guest's own message. The Private Tasting Suite price is published
inconsistently (conflict) - NEVER quote any number for it; say you'll confirm.
If ground truth lacks the answer, write the literal phrase "I'll confirm and
follow up". If the classification is spam, output exactly NO_REPLY.
Output only the email body text."""


def draft(inq: dict, cls: dict):
    """Returns (body_or_None, mode)."""
    if llm.mode() == "offline":
        return _offline_template(inq, cls), "offline-template"

    ground = json.dumps(kb.load_kb(), default=str)
    raw = llm.complete(
        "drafter", _DRAFT_SYSTEM,
        f"GROUND TRUTH:\n{ground}\n\nCLASSIFICATION: {json.dumps(cls)}\n\n"
        f"INQUIRY:\nFrom: {inq['from']}\nSubject: {inq['subject']}\n\n{inq['body']}")
    if raw.strip() == "NO_REPLY" or cls["intent"] == "spam":
        return None, "sonnet"

    source = f"{inq.get('subject', '')}\n{inq.get('body', '')}"
    result = validator.validate_draft(raw, source)
    if result.ok:
        return raw, "sonnet"
    # Hard gate: an ungrounded LLM draft never reaches the queue.
    return (_offline_template(inq, cls),
            f"offline-template (LLM draft failed validation: {result.violations})")
