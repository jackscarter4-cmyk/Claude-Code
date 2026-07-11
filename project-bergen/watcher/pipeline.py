"""Component 1 — Reputation Watcher.

  ingest (fixtures; --live adds polite RSS pulls for press feeds)
  -> dedupe against watcher/state.json (report-by-exception: an item is
     reported exactly once, ever)
  -> classify each NEW item: {press|review} x {positive|negative|mixed} x
     fixed theme from watcher/labels.yaml (haiku; keyword fallback offline)
  -> render out/digest_YYYY-MM-DD.html (+ .txt): WHAT THE PRESS SAID /
     WHAT GUESTS SAID / THE GAP.

If nothing is new, the digest is one line: "No new signals this week."

Usage:
  python -m watcher.pipeline [--live] [--reset-state]
"""
import argparse
import html
import json
from datetime import date
from pathlib import Path

import yaml

from common import llm
from common.util import OUT, ROOT, ensure_out, load_json_dir, now_iso

STATE_PATH = Path(__file__).resolve().parent / "state.json"


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"reported_ids": [], "last_run": None}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2))


def load_labels():
    with open(Path(__file__).resolve().parent / "labels.yaml") as f:
        return yaml.safe_load(f)["labels"]


def ingest(live: bool):
    reviews = load_json_dir(ROOT / "fixtures" / "reviews")
    press = load_json_dir(ROOT / "fixtures" / "press")
    if live:
        from watcher.live_fetch import fetch_press_rss
        fetched, note = fetch_press_rss()
        press.extend(fetched)
        print(f"  live press pull: {note}")
    return press, reviews


# --------------------------------------------------------------- classify
_SYSTEM_TMPL = """You classify one item about a winery. Choose:
sentiment: positive | negative | mixed
theme: exactly one label from the definitions below (use other_human when unsure -
that routes to a person and is always acceptable).

{label_block}

Respond with strict JSON: {{"sentiment": "...", "theme": "..."}}"""


def _fallback_classify(item, labels):
    text = " ".join(str(item.get(k, "")) for k in ("title", "text", "excerpt")).lower()
    # Score every label by keyword hits and take the strongest signal; a
    # first-hit-wins scan would let an incidental keyword steal the item.
    scores = {name: sum(k.lower() in text for k in spec.get("keywords", []))
              for name, spec in labels.items()}
    best = max(scores, key=scores.get)
    theme = best if scores[best] > 0 else "other_human"
    if "rating" in item:
        sentiment = ("positive" if item["rating"] >= 4 else
                     "mixed" if item["rating"] == 3 else "negative")
    else:
        sentiment = "positive"  # press fixtures skew positive; negatives carry keywords
        if any(w in text for w in ("lawsuit", "scandal", "decline", "worst")):
            sentiment = "negative"
    return {"sentiment": sentiment, "theme": theme}


def classify(item, labels):
    kind = "review" if "rating" in item else "press"
    if llm.mode() == "offline":
        result = _fallback_classify(item, labels)
    else:
        label_block = "\n".join(f"- {n}: {s['definition'].strip()}" for n, s in labels.items())
        raw = llm.complete("classifier", _SYSTEM_TMPL.format(label_block=label_block),
                           json.dumps(item))
        try:
            result = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
            if result.get("theme") not in labels:
                result["theme"] = "other_human"
        except (ValueError, KeyError):
            result = _fallback_classify(item, labels)
    return {**item, "kind": kind, **result}


# ------------------------------------------------------------------ digest
def synthesize_gap(press_items, review_items):
    """Returns a list of terse bullet strings — telegram style, no essay."""
    if llm.mode() != "offline":
        prompt = ("Write 3-4 bullet points (each one short line, telegram style, no filler, "
                  "no adjectives you don't need) on where the press narrative and the guest "
                  "experience diverge this week, for a winery's operations director. "
                  "Start each line with '- '.\n\n"
                  f"PRESS:\n{json.dumps(press_items)}\n\nGUESTS:\n{json.dumps(review_items)}")
        raw = llm.complete("drafter", "You write terse, specific operational syntheses.", prompt)
        bullets = [l.lstrip("-• ").strip() for l in raw.splitlines() if l.strip().lstrip("-• ")]
        if bullets:
            return bullets

    press_themes = sorted({i["theme"].replace("_", " ") for i in press_items}) or ["(none)"]
    neg = [i for i in review_items if i["sentiment"] in ("negative", "mixed")]
    neg_themes = sorted({i["theme"].replace("_", " ") for i in neg})
    if not neg_themes:
        return ["Press and guests point the same direction this week.",
                "No divergence worth your time."]
    return [f"Press this week: {', '.join(press_themes)}.",
            f"Guests agree on the wine - the praise matches the coverage.",
            f"The low ratings aren't about the product: {', '.join(neg_themes)}.",
            "The wine earns the coverage; the seams spend it."]


CSS = """
body{font-family:'Palatino Linotype',Palatino,'Book Antiqua',Georgia,serif;
     background:#faf7f2;color:#2b2320;
     max-width:720px;margin:2rem auto;padding:0 1.5rem;line-height:1.55}
h1{font-size:1.6rem;font-weight:normal;letter-spacing:.12em;border-bottom:1px solid #c9b8a8;
   padding-bottom:.6rem} h1 small{display:block;font-size:.75rem;letter-spacing:.2em;color:#8a7360}
h2{font-size:.8rem;letter-spacing:.25em;color:#5b1f2e;margin-top:2.2rem;text-transform:uppercase}
.item{margin:1rem 0;padding-left:1rem;border-left:2px solid #e0d4c3}
.item .meta{font-size:.75rem;color:#8a7360;letter-spacing:.05em}
.item.negative{border-left-color:#5b1f2e}.item.mixed{border-left-color:#b08947}
.gap{background:#fff;border:1px solid #e0d4c3;padding:1.2rem 1.4rem;margin-top:1rem}
.gap ul{margin:0;padding-left:1.1rem}.gap li{margin:.35rem 0}
.tag{font-size:.7rem;color:#5b1f2e;letter-spacing:.1em}
footer{margin-top:3rem;font-size:.7rem;color:#8a7360}
"""


def render_digest(new_press, new_reviews, today, mode_note):
    title = f"Macari — External Signals Digest — {today}"
    if not new_press and not new_reviews:
        body_html = "<p><em>No new signals this week.</em></p>"
        body_txt = "No new signals this week."
    else:
        gap = synthesize_gap(new_press, new_reviews)

        def item_html(i):
            head = i.get("title") or (i.get("text", "")[:80] + "…")
            src = i.get("outlet") or i.get("platform", "")
            stars = f" · {'★' * i['rating']}{'☆' * (5 - i['rating'])}" if "rating" in i else ""
            quote = i.get("excerpt") or i.get("text", "")
            return (f'<div class="item {i["sentiment"]}">'
                    f'<div class="meta">{html.escape(src)} · {i.get("date","")}{stars} · '
                    f'<span class="tag">{i["theme"]}</span></div>'
                    f'<strong>{html.escape(str(head))}</strong><br>'
                    f'{html.escape(quote)} <a href="{i.get("url","#")}">source</a></div>')

        def item_txt(i):
            src = i.get("outlet") or i.get("platform", "")
            return (f"  [{i['sentiment']}/{i['theme']}] {src} {i.get('date','')} - "
                    f"{i.get('title') or ''} {i.get('excerpt') or i.get('text','')}\n"
                    f"    {i.get('url','')}")

        gap_html = "".join(f"<li>{html.escape(b)}</li>" for b in gap)
        body_html = (
            "<h2>What the press said</h2>" + "".join(map(item_html, new_press)) +
            "<h2>What guests said</h2>" + "".join(map(item_html, new_reviews)) +
            f"<h2>The gap</h2><div class='gap'><ul>{gap_html}</ul></div>")
        body_txt = ("WHAT THE PRESS SAID\n" + "\n".join(map(item_txt, new_press)) +
                    "\n\nWHAT GUESTS SAID\n" + "\n".join(map(item_txt, new_reviews)) +
                    "\n\nTHE GAP\n" + "\n".join(f"  - {b}" for b in gap))

    page = (f"<title>{title}</title><style>{CSS}</style>"
            f"<h1>MACARI VINEYARDS<small>EXTERNAL SIGNALS — WEEK OF {today}</small></h1>"
            f"{body_html}<footer>Generated {now_iso()} · {mode_note} · report-by-exception: "
            f"previously reported items never repeat · Project Bergen</footer>")
    txt = f"MACARI — EXTERNAL SIGNALS DIGEST — {today}\n{'=' * 48}\n\n{body_txt}\n\n({mode_note})\n"
    return page, txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="also pull press RSS feeds politely")
    ap.add_argument("--reset-state", action="store_true")
    args = ap.parse_args()

    if args.reset_state and STATE_PATH.exists():
        STATE_PATH.unlink()

    labels = load_labels()
    state = load_state()
    press, reviews = ingest(args.live)

    seen = set(state["reported_ids"])
    new_press = [classify(i, labels) for i in press if i["id"] not in seen]
    new_reviews = [classify(i, labels) for i in reviews if i["id"] not in seen]

    today = date.today().isoformat()
    mode_note = f"classification mode: {'api' if llm.mode() == 'api' else 'offline keyword rules'}"
    page, txt = render_digest(new_press, new_reviews, today, mode_note)

    ensure_out()
    html_path = OUT / f"digest_{today}.html"
    html_path.write_text(page)
    (OUT / f"digest_{today}.txt").write_text(txt)

    state["reported_ids"] = sorted(seen | {i["id"] for i in new_press + new_reviews})
    state["last_run"] = now_iso()
    save_state(state)

    n = len(new_press) + len(new_reviews)
    print(f"Digest written: {html_path}  ({n} new signal(s)"
          f"{'' if n else ' - one-line digest'})")


if __name__ == "__main__":
    main()
