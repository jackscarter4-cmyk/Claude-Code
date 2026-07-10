"""`make eval` — the five gates. The system is graded, not vibed.

  G1 triage + inquiry classification vs evals/golden.yaml
     (100% on {chargeback, cancellation_request, over_capacity, spam};
      >=90% overall; 'unsure' -> human is never a failure)
  G2 draft quality judge (rubric.md): mean >= 4.0, no draft below 3
  G3 zero-hallucinated-price validator: one violation fails the build,
     plus: the conflicted Private Suite price must never be quoted
  G4 watcher no-repeat: identical inputs twice -> second digest says
     "No new signals"
  G5 escalation ladder: un-acked chargeback re-surfaces at 50% and 100%
     SLA and appears in the audit log; item is still open (nothing
     auto-closes)

Writes out/eval_report.md and evals/STATUS.md (the README badge).
Exit code 0 only if all five gates pass.
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

from common import kb, llm, validator
from common.util import OUT, ROOT, ensure_out, load_json_dir, now_iso, read_jsonl

GATE_CLASSES_CLUB = {"chargeback", "cancellation_request"}
report = []
results = {}


def log(line=""):
    report.append(line)
    print(line)


# ---------------------------------------------------------------- gate 1
def gate1():
    from responder.classify import classify_inquiry
    from triage.classify import classify_event

    golden = yaml.safe_load((ROOT / "evals" / "golden.yaml").read_text())
    errors, gate_errors, total, unsure_ok = [], [], 0, 0

    for event in load_json_dir(ROOT / "fixtures" / "club_events"):
        want = golden["club_events"][event["id"]]
        got = classify_event(event)
        total += 1
        if got == want:
            continue
        if got == "unsure":   # routing to human is never a failure
            unsure_ok += 1
            continue
        errors.append(f"{event['id']}: want {want}, got {got}")
        if want in GATE_CLASSES_CLUB:
            gate_errors.append(errors[-1])

    for inq in load_json_dir(ROOT / "fixtures" / "inquiries"):
        want = golden["inquiries"][inq["id"]]
        got = classify_inquiry(inq)
        total += 1
        intent_ok = got["intent"] == want["intent"]
        if got["over_capacity"] != want.get("over_capacity", False):
            gate_errors.append(f"{inq['id']}: over_capacity flag wrong")
        if intent_ok:
            continue
        if got["intent"] == "unsure":
            unsure_ok += 1
            continue
        errors.append(f"{inq['id']}: want {want['intent']}, got {got['intent']}")
        if want["intent"] == "spam":
            gate_errors.append(errors[-1])

    accuracy = (total - len(errors)) / total
    ok = not gate_errors and accuracy >= 0.90
    log(f"G1 classification: {accuracy:.0%} overall ({total - len(errors)}/{total}), "
        f"{unsure_ok} routed-to-human (never a failure), "
        f"gate-class errors: {len(gate_errors)} -> {'PASS' if ok else 'FAIL'}")
    for e in errors + gate_errors:
        log(f"     - {e}")
    return ok


# ------------------------------------------------- shared draft generation
def generate_all_drafts():
    """Fresh in-memory drafts for every fixture (never reads out/)."""
    from responder.classify import classify_inquiry
    from responder.drafts import draft as rdraft
    from triage.classify import classify_event
    from triage.drafts import draft as tdraft

    drafts = []  # (id, kind, text, source_text)
    for inq in load_json_dir(ROOT / "fixtures" / "inquiries"):
        source = f"{inq['subject']}\n{inq['body']}"
        body, _ = rdraft(inq, classify_inquiry(inq))
        if body is not None:
            drafts.append((inq["id"], "inquiry_reply", body, source))
    for event in load_json_dir(ROOT / "fixtures" / "club_events"):
        source = json.dumps(event, ensure_ascii=False)
        member, internal, _ = tdraft(event, classify_event(event))
        drafts.append((event["id"], "member_reply", member, source))
        drafts.append((event["id"], "internal_note", internal, source))
    return drafts


# ---------------------------------------------------------------- gate 2
def gate2(drafts):
    from evals.judge import judge_draft
    outbound = [d for d in drafts if d[1] != "internal_note"]
    scores = [(i, judge_draft(text, source)) for i, kind, text, source in outbound]
    mean = sum(s["overall"] for _, s in scores) / len(scores)
    worst_id, worst = min(scores, key=lambda x: x[1]["overall"])
    ok = mean >= 4.0 and worst["overall"] >= 3.0
    log(f"G2 draft quality ({scores[0][1]['judge']} judge, {len(scores)} drafts): "
        f"mean {mean:.2f} (gate >=4.0), min {worst['overall']} on {worst_id} (gate >=3.0) "
        f"-> {'PASS' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------- gate 3
def gate3(drafts):
    violations = []
    for i, kind, text, source in drafts:
        r = validator.validate_draft(text, source)
        violations += [f"{i}/{kind}: {v}" for v in r.violations]

    # The conflicted Private Suite price must never be quoted, and the
    # suite reply must carry the escape phrase instead.
    suite = next(text for i, kind, text, _ in drafts
                 if i == "inq-012" and kind == "inquiry_reply")
    for amount in kb.conflicted_dollar_amounts():
        if f"${amount:g}" in suite:
            violations.append(f"inq-012: quoted conflicted price ${amount:g}")
    if not validator.has_escape_phrase(suite):
        violations.append("inq-012: missing the literal confirm-and-follow-up escape phrase")

    ok = not violations
    log(f"G3 hallucinated-numbers validator: {len(drafts)} drafts parsed, "
        f"{len(violations)} violation(s) (gate: zero) -> {'PASS' if ok else 'FAIL'}")
    for v in violations:
        log(f"     - {v}")
    return ok


# ---------------------------------------------------------------- gate 4
def gate4():
    env_run = lambda args: subprocess.run(
        [sys.executable, "-m", "watcher.pipeline", *args],
        cwd=ROOT, capture_output=True, text=True, check=True)
    env_run(["--reset-state"])
    first = (OUT / f"digest_{date.today().isoformat()}.txt").read_text()
    env_run([])
    second = (OUT / f"digest_{date.today().isoformat()}.txt").read_text()
    ok = ("No new signals" not in first) and ("No new signals" in second)
    log(f"G4 watcher no-repeat: run1 reported signals, run2 said 'No new signals': "
        f"{'PASS' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------- gate 5
def gate5():
    state_path = OUT / "triage_state.json"
    log_path = OUT / "triage_log.jsonl"
    backup = state_path.read_text() if state_path.exists() else None
    if state_path.exists():
        state_path.unlink()
    before = len(read_jsonl(log_path))
    try:
        run = lambda mod, *args: subprocess.run(
            [sys.executable, "-m", mod, *args], cwd=ROOT,
            capture_output=True, text=True, check=True)
        run("triage.pipeline", "--only", "ce-008")   # chargeback, SLA 8h
        run("triage.escalate", "--advance-hours", "4")   # 50%
        run("triage.escalate", "--advance-hours", "4")   # 100%
        events = {(e.get("event"), e.get("id")) for e in read_jsonl(log_path)[before:]}
        state = json.loads(state_path.read_text())
        still_open = state["items"]["ce-008"]["status"] == "open"
        ok = (("escalation_50", "ce-008") in events and
              ("escalation_100", "ce-008") in events and still_open)
        log(f"G5 escalation ladder: 50% ping {'✓' if ('escalation_50','ce-008') in events else 'X'}, "
            f"100% ping {'✓' if ('escalation_100','ce-008') in events else 'X'}, "
            f"still open (nothing auto-closes) {'✓' if still_open else 'X'} "
            f"-> {'PASS' if ok else 'FAIL'}")
        return ok
    finally:
        if backup is not None:
            state_path.write_text(backup)
        elif state_path.exists():
            state_path.unlink()


def main():
    ensure_out()
    log(f"# Project Bergen — eval run {now_iso()}  (LLM mode: {llm.mode()})")
    log()
    results["G1"] = gate1()
    drafts = generate_all_drafts()
    results["G2"] = gate2(drafts)
    results["G3"] = gate3(drafts)
    results["G4"] = gate4()
    results["G5"] = gate5()
    log()
    passed = sum(results.values())
    verdict = "PASS" if passed == 5 else "FAIL"
    log(f"## RESULT: {verdict} — {passed}/5 gates")

    (OUT / "eval_report.md").write_text("\n".join(report) + "\n")
    (ROOT / "evals" / "STATUS.md").write_text(
        f"**EVALS: {verdict}** — {passed}/5 gates — {now_iso()} — "
        f"LLM mode: `{llm.mode()}`\n\nSee `out/eval_report.md` for details "
        f"(regenerate with `make eval`).\n")
    sys.exit(0 if passed == 5 else 1)


if __name__ == "__main__":
    main()
