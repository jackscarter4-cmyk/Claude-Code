"""Escalation ladder CLI. An un-actioned item re-surfaces at 50% of its SLA
and pings again at 100%. Nothing auto-closes, ever — only a named human
resolve closes an item, and every transition is in out/triage_log.jsonl.

  python -m triage.escalate --status
  python -m triage.escalate --advance-hours 12      # simulated clock
  python -m triage.escalate --resolve ce-008 --by gabriella --note "called member"
"""
import argparse
from datetime import datetime

from triage import state as tstate


def print_alerts(alerts):
    for tag, item in alerts:
        marker = "!! OVERDUE (100% SLA)" if tag == "escalation_100" else "^ RE-SURFACED (50% SLA)"
        print(f"  {marker}  {item['id']}  {item['label']}  member: {item['member']}")
    if not alerts:
        print("  (no new escalations this sweep)")


def print_status():
    state = tstate.load()
    sim_now = datetime.fromisoformat(state["sim_now"])
    print(f"Simulated clock: {state['sim_now']}")
    if not state["items"]:
        print("No triage items. Run: python -m triage.pipeline")
        return
    print(f"{'id':<8} {'label':<22} {'status':<9} {'age':>6} {'SLA':>4}  escalations")
    for item in state["items"].values():
        age_h = (sim_now - datetime.fromisoformat(item["opened_at"])).total_seconds() / 3600
        esc = ",".join(e.split("_")[1] + "%" for e in item["escalations"]) or "-"
        print(f"{item['id']:<8} {item['label']:<22} {item['status']:<9} "
              f"{age_h:>5.1f}h {item['sla_hours']:>3}h  {esc}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--advance-hours", type=float)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--resolve")
    ap.add_argument("--by", default="human")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    if args.advance_hours:
        state = tstate.advance(args.advance_hours)
        print(f"Clock advanced {args.advance_hours:g}h -> {state['sim_now']}")
        print_alerts(tstate.sweep())
    elif args.resolve:
        item = tstate.resolve(args.resolve, args.by, args.note)
        print(f"{item['id']} resolved by {args.by} (logged). "
              "This is the only way an item closes.")
    elif args.status:
        print_status()
    else:
        print_alerts(tstate.sweep())


if __name__ == "__main__":
    main()
