"""Approval-gate CLI.

  python -m approval.cli list
  python -m approval.cli show <id>
  python -m approval.cli approve <id> [--by NAME]
  python -m approval.cli reject  <id> [--by NAME]
"""
import argparse

from approval import queue


def main():
    ap = argparse.ArgumentParser(description="Project Bergen approval gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for c in ("show", "approve", "reject"):
        p = sub.add_parser(c)
        p.add_argument("id")
        p.add_argument("--by", default="cli")
    args = ap.parse_args()

    if args.cmd == "list":
        items = queue.list_pending()
        if not items:
            print("Approval queue is empty.")
            return
        print(f"{len(items)} pending draft(s):\n")
        for it in items:
            print(f"  {it['id']}  [{it['component']}/{it['kind']}]  to: {it['to']}")
            print(f"      subject: {it['subject']}")
        print("\nNothing here is sent, ever - approving writes to out/approved/ and logs it.")
    elif args.cmd == "show":
        it = queue.get(args.id)
        print(f"id:      {it['id']}\ncomponent: {it['component']} ({it['kind']})")
        print(f"to:      {it['to']}\nsubject: {it['subject']}\nqueued:  {it['queued_at']}")
        print("-" * 60)
        print(it["body"])
    elif args.cmd == "approve":
        it = queue.approve(args.id, args.by)
        print(f"APPROVED {it['id']} -> out/approved/{it['id']}.json (logged; no send path exists)")
    elif args.cmd == "reject":
        it = queue.reject(args.id, args.by)
        print(f"REJECTED {it['id']} -> out/rejected/{it['id']}.json")


if __name__ == "__main__":
    main()
