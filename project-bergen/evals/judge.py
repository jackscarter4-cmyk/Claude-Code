"""Draft-quality judge (EVAL GATE #2), scoring per evals/rubric.md.

API mode: sonnet judges each draft SOLO against the rubric (position-bias
mitigation: never pairwise, never batched). Offline mode: a deterministic
heuristic scores the same four dimensions; the eval report labels which
judge ran. Score = mean of {grounded, tone, next_step, brevity}."""
import json
import re

from common import llm, validator
from common.util import ROOT


def _heuristic(draft: str, source: str) -> dict:
    grounded = 5 if validator.validate_draft(draft, source).ok else 1

    has_signoff = "macari" in draft.lower() and "team" in draft.lower()
    has_greeting = bool(re.match(r"^(hi|hello|hola|dear)\b", draft.strip(), re.IGNORECASE))
    personal = any(m in draft.lower() for m in
                   ("i'm", "i'd", "we'd love", "personally", "glad", "encantaria", "means a lot"))
    boilerplate = any(m in draft.lower() for m in
                      ("valued customer", "do not reply", "per our policy", "as per"))
    tone = 3 + has_signoff + personal - 2 * boilerplate - (0 if has_greeting else 1)

    action = any(m in draft.lower() for m in
                 ("reply with", "reply here", "call", "visit", "respond", "set up",
                  "schedule", "pencil in", "hear from"))
    invite = "?" in draft or "reply" in draft.lower()
    next_step = 5 if action else (3 if invite else 1)

    words = len(draft.split())
    brevity = 5 if words <= 150 else 4 if words <= 200 else 3 if words <= 260 else 2

    return {"grounded": grounded, "tone": max(1, min(5, tone)),
            "next_step": next_step, "brevity": brevity,
            "note": f"heuristic judge ({words} words)"}


def _llm_judge(draft: str, source: str) -> dict:
    rubric = (ROOT / "evals" / "rubric.md").read_text()
    raw = llm.complete(
        "judge",
        "You are a strict draft-quality judge. Score ONLY this draft against the rubric, "
        "solo — you never see or compare other drafts. Output strict JSON.",
        f"RUBRIC:\n{rubric}\n\nSOURCE MESSAGE:\n{source}\n\nDRAFT:\n{draft}")
    try:
        scores = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        return {k: scores[k] for k in ("grounded", "tone", "next_step", "brevity")} | \
               {"note": scores.get("note", "")}
    except (ValueError, KeyError):
        return _heuristic(draft, source)  # unparseable judge output -> deterministic path


def judge_draft(draft: str, source: str) -> dict:
    scores = _heuristic(draft, source) if llm.mode() == "offline" else _llm_judge(draft, source)
    dims = [scores["grounded"], scores["tone"], scores["next_step"], scores["brevity"]]
    scores["overall"] = round(sum(dims) / 4, 1)
    scores["judge"] = "heuristic" if llm.mode() == "offline" else "sonnet-solo"
    return scores
