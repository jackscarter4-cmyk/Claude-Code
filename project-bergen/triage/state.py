"""Triage escalation state store (out/triage_state.json) with a simulated
clock so the escalation ladder can be demonstrated (and eval-gated) without
waiting real hours. All transitions are appended to out/triage_log.jsonl."""
import json
from datetime import datetime, timedelta, timezone

from common.util import OUT, append_jsonl, ensure_out, now_iso

STATE_PATH = OUT / "triage_state.json"
LOG = OUT / "triage_log.jsonl"


def load():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"sim_now": now_iso(), "items": {}}


def save(state):
    ensure_out()
    STATE_PATH.write_text(json.dumps(state, indent=2))


def open_item(event, label, sla_hours, approval_id):
    state = load()
    item = {
        "id": event["id"], "label": label, "sla_hours": sla_hours,
        "member": event["member"]["name"],
        "opened_at": state["sim_now"], "status": "open",
        "escalations": [], "approval_id": approval_id,
    }
    state["items"][event["id"]] = item
    save(state)
    return item


def advance(hours: float):
    state = load()
    t = datetime.fromisoformat(state["sim_now"]) + timedelta(hours=hours)
    state["sim_now"] = t.isoformat(timespec="seconds")
    save(state)
    append_jsonl(LOG, {"at": now_iso(), "event": "clock_advanced",
                       "hours": hours, "sim_now": state["sim_now"]})
    return state


def sweep():
    """Re-surface every open item past 50% / 100% of its SLA. Returns the
    list of alerts raised this sweep. NOTHING here closes an item — only an
    explicit human resolve() does."""
    state = load()
    sim_now = datetime.fromisoformat(state["sim_now"])
    alerts = []
    for item in state["items"].values():
        if item["status"] != "open":
            continue
        elapsed_h = (sim_now - datetime.fromisoformat(item["opened_at"])).total_seconds() / 3600
        pct = elapsed_h / item["sla_hours"]
        for threshold, tag in ((0.5, "escalation_50"), (1.0, "escalation_100")):
            if pct >= threshold and tag not in item["escalations"]:
                item["escalations"].append(tag)
                append_jsonl(LOG, {"at": now_iso(), "event": tag, "id": item["id"],
                                   "label": item["label"], "member": item["member"],
                                   "elapsed_hours": round(elapsed_h, 1),
                                   "sla_hours": item["sla_hours"],
                                   "sim_now": state["sim_now"]})
                alerts.append((tag, item))
    save(state)
    return alerts


def resolve(item_id: str, resolver: str, note: str = ""):
    state = load()
    if item_id not in state["items"]:
        raise SystemExit(f"unknown triage item {item_id}")
    item = state["items"][item_id]
    item["status"] = "resolved"
    item["resolved_by"] = resolver
    item["resolved_at"] = state["sim_now"]
    save(state)
    append_jsonl(LOG, {"at": now_iso(), "event": "resolved_by_human", "id": item_id,
                       "by": resolver, "note": note})
    return item
