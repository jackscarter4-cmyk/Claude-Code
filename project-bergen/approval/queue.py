"""Shared approval queue. THE GATE LIVES HERE, IN CODE, NOT IN PROMPTS.

Every outbound draft from every component lands in out/queue/ and stays
there until a human runs `python -m approval.cli approve <id>`. Approving
moves the draft to out/approved/ and logs it — and that is the END of the
line: there is no send function in this codebase. Nothing can email, post,
or publish, approved or not.
"""
import json
import uuid
from pathlib import Path

from common.util import OUT, append_jsonl, ensure_out, now_iso

QUEUE_DIR = OUT / "queue"
APPROVED_DIR = OUT / "approved"
REJECTED_DIR = OUT / "rejected"
LOG = OUT / "approval_log.jsonl"


def enqueue(component: str, kind: str, to: str, subject: str, body: str,
            source_id: str, meta: dict | None = None) -> str:
    ensure_out("queue")
    item_id = f"apq-{source_id}-{uuid.uuid4().hex[:6]}"
    item = {
        "id": item_id, "component": component, "kind": kind,
        "to": to, "subject": subject, "body": body,
        "source_id": source_id, "meta": meta or {},
        "status": "pending", "queued_at": now_iso(),
    }
    with open(QUEUE_DIR / f"{item_id}.json", "w") as f:
        json.dump(item, f, indent=2, ensure_ascii=False)
    append_jsonl(LOG, {"at": now_iso(), "event": "queued", "id": item_id,
                       "component": component, "source_id": source_id})
    return item_id


def list_pending():
    if not QUEUE_DIR.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(QUEUE_DIR.glob("*.json"))]


def get(item_id: str) -> dict:
    p = QUEUE_DIR / f"{item_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"no pending item {item_id}")
    return json.loads(p.read_text())


def _resolve(item_id: str, action: str, dest: Path, approver: str) -> dict:
    item = get(item_id)
    item["status"] = action
    item[f"{action}_at"] = now_iso()
    item[f"{action}_by"] = approver
    dest.mkdir(parents=True, exist_ok=True)
    with open(dest / f"{item_id}.json", "w") as f:
        json.dump(item, f, indent=2, ensure_ascii=False)
    (QUEUE_DIR / f"{item_id}.json").unlink()
    append_jsonl(LOG, {"at": now_iso(), "event": action, "id": item_id,
                       "by": approver, "component": item["component"],
                       "source_id": item["source_id"]})
    return item


def approve(item_id: str, approver: str = "cli") -> dict:
    """'Approve' means: the human has read it and would send it. The file is
    written to out/approved/ and logged. No transport exists beyond this."""
    return _resolve(item_id, "approved", APPROVED_DIR, approver)


def reject(item_id: str, approver: str = "cli") -> dict:
    return _resolve(item_id, "rejected", REJECTED_DIR, approver)
