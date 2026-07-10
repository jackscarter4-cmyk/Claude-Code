"""Component 2 — Events Inquiry Responder (workflow-first pipeline).

  parse inquiry -> classify intent+urgency -> draft grounded ONLY in
  packages.yaml -> code-level validator -> approval queue.

Seam behavior: a wine-club billing question misrouted to events@ is
cross-routed into the triage component with a same-day flag — the inquiry
doesn't bounce between inboxes, it changes lanes in software.

The headline metric — inquiry-received -> draft-ready — is measured per
inquiry and written to out/responder_metrics.json (target: < 60s).

Usage:
  python -m responder.pipeline                    # all fixtures
  python -m responder.pipeline --only inq-003
  python -m responder.pipeline --paste            # fresh inquiry from stdin (live demo)
"""
import argparse
import json
import sys
import time

from approval import queue
from common import validator
from common.util import OUT, ROOT, append_jsonl, ensure_out, load_json_dir, now_iso
from responder.classify import classify_inquiry
from responder.drafts import draft

LOG = OUT / "responder_log.jsonl"
METRICS = OUT / "responder_metrics.json"


def cross_route_to_triage(inq: dict):
    """The seam moment: build a synthetic inbound-email event and drop it into
    the club-exception triage lane, same-day flagged."""
    from triage.pipeline import process_event
    event = {
        "_synthetic": "cross-routed from events inbox by responder",
        "id": f"xr-{inq['id']}",
        "type": "email.inbound",
        "occurred_at": now_iso(),
        "member": {"member_id": "LOOKUP_REQUIRED", "name": inq["from"], "tier": "unknown"},
        "data": {"subject": inq["subject"], "body": inq["body"]},
    }
    return process_event(event)


def process_inquiry(inq: dict) -> dict:
    started = time.monotonic()
    received_at = now_iso()  # simulated arrival = the moment it hits the pipeline

    cls = classify_inquiry(inq)
    body, mode = draft(inq, cls)

    record = {"id": inq["id"], "received_at": received_at, **cls, "draft_mode": mode}

    if body is None:
        record.update(action="discarded_spam", seconds=round(time.monotonic() - started, 2))
        append_jsonl(LOG, {"at": now_iso(), **record})
        return record

    result = validator.validate_draft(body, f"{inq.get('subject','')}\n{inq.get('body','')}")
    if not result.ok:  # belt over braces: nothing invalid may be enqueued
        raise RuntimeError(f"{inq['id']}: draft failed validation: {result.violations}")

    approval_id = queue.enqueue(
        component="responder", kind=f"inquiry_reply/{cls['intent']}",
        to=inq["from"], subject=f"Re: {inq['subject']}", body=body,
        source_id=inq["id"],
        meta={"classification": cls, "draft_mode": mode})
    record.update(action="queued_for_approval", approval_id=approval_id)

    if cls["intent"] == "club_billing_misroute":
        triage_item = cross_route_to_triage(inq)
        record["cross_routed_to_triage"] = triage_item["id"]

    record["seconds"] = round(time.monotonic() - started, 2)
    append_jsonl(LOG, {"at": now_iso(), **record})
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--paste", action="store_true",
                    help="read a fresh inquiry from stdin (subject on line 1, body after)")
    args = ap.parse_args()

    if args.paste:
        raw = sys.stdin.read().strip()
        lines = raw.splitlines()
        inquiries = [{"id": "live-paste", "from": "live-demo@example",
                      "subject": lines[0] if lines else "(no subject)",
                      "body": "\n".join(lines[1:]) or raw}]
    else:
        inquiries = load_json_dir(ROOT / "fixtures" / "inquiries")
        if args.only:
            inquiries = [i for i in inquiries if i["id"] == args.only]
            if not inquiries:
                raise SystemExit(f"no fixture with id {args.only}")

    print(f"Responder: processing {len(inquiries)} inquiry(ies)\n")
    records = []
    for inq in inquiries:
        rec = process_inquiry(inq)
        records.append(rec)
        flags = [f for f in ("over_capacity", "closed_date") if rec.get(f)]
        if rec.get("cross_routed_to_triage"):
            flags.append(f"cross-routed -> {rec['cross_routed_to_triage']}")
        flag_str = ("  [" + ", ".join(flags) + "]") if flags else ""
        print(f"  {rec['id']}  {rec['intent']:<22} urgency={rec['urgency']:<7} "
              f"{rec['action']:<20} {rec['seconds']:>5.2f}s{flag_str}")

    drafted = [r for r in records if r["action"] == "queued_for_approval"]
    if drafted:
        slowest = max(r["seconds"] for r in drafted)
        print(f"\ninquiry-received -> draft-ready: slowest {slowest:.2f}s "
              f"(target < 60s) across {len(drafted)} drafts")
    ensure_out()
    METRICS.write_text(json.dumps(
        {"generated_at": now_iso(), "target_seconds": 60, "records": records}, indent=2))
    print("Drafts are in the approval queue: python -m approval.cli list")


if __name__ == "__main__":
    main()
