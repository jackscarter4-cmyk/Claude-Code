"""Component 3 — Club Exception Triage pipeline (workflow-first: a fixed
sequence in code, one classify call + one draft call per event, no loops).

  ingest webhook fixtures -> classify -> assign SLA -> draft member reply +
  internal note -> approval queue -> register in escalation state.

Every state change is appended to out/triage_log.jsonl. Nothing auto-closes.

Usage:
  python -m triage.pipeline                 # all fixtures
  python -m triage.pipeline --only ce-014   # single event (demo replay)
"""
import argparse
import json

from approval import queue
from common.util import OUT, ROOT, append_jsonl, load_config, load_json_dir, now_iso
from triage import state as tstate
from triage.classify import classify_event
from triage.drafts import draft, public_risk

LOG = OUT / "triage_log.jsonl"


def process_event(event: dict) -> dict:
    cfg = load_config()
    label = classify_event(event)
    sla = cfg["sla_hours"][label]
    append_jsonl(LOG, {"at": now_iso(), "event": "opened", "id": event["id"],
                       "label": label, "sla_hours": sla,
                       "member": event["member"]["member_id"]})

    member_body, internal_note, mode = draft(event, label)
    member_name = event["member"]["name"]
    approval_id = queue.enqueue(
        component="triage", kind=f"member_reply/{label}",
        to=f"{member_name} <synthetic-member@example>",
        subject=f"Your Macari Wine Club - {label.replace('_', ' ')}",
        body=member_body,
        source_id=event["id"],
        meta={"internal_note": internal_note, "draft_mode": mode,
              "public_risk": public_risk(event)})
    append_jsonl(LOG, {"at": now_iso(), "event": "drafts_queued", "id": event["id"],
                       "approval_id": approval_id, "draft_mode": mode})

    item = tstate.open_item(event, label, sla, approval_id)
    return item


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="process a single event id (e.g. ce-014)")
    args = ap.parse_args()

    events = load_json_dir(ROOT / "fixtures" / "club_events")
    if args.only:
        events = [e for e in events if e["id"] == args.only]
        if not events:
            raise SystemExit(f"no fixture with id {args.only}")

    print(f"Triage: processing {len(events)} club event(s)  [drafting mode: see queue meta]\n")
    for event in events:
        item = process_event(event)
        risk = "  !! PUBLIC-REVIEW RISK" if public_risk(event) else ""
        print(f"  {event['id']}  -> {item['label']:<22} SLA {item['sla_hours']:>2}h  "
              f"escalates at {item['sla_hours'] / 2:g}h / {item['sla_hours']}h{risk}")
    print(f"\nAll drafts are waiting in the approval queue (python -m approval.cli list).")
    print(f"Nothing auto-closes: python -m triage.escalate --status")


if __name__ == "__main__":
    main()
