"""Component 4 — Stale-Surface Audit.

Crawls (politely) or replays cached snapshots of every public page where
Macari states a price, hour, or policy; extracts those facts; cross-compares;
and writes the dated report out/surface_audit.md — the free-gift artifact.

Checks (all deterministic code, no LLM anywhere in this component):
  C1 price conflict      — same product, different published prices
  C2 draft page title    — a <title> containing 'draft' is live
  C3 stale policy date   — 'Last updated' more than 24 months old
  C4 stale listing       — booking page advertises a past-year offering
  C5 hours mismatch      — Google Business hours != website hours

Usage:
  python -m audit.pipeline           # from cached snapshots (deterministic)
  python -m audit.pipeline --live    # re-fetch each target first (fallback: snapshot)
"""
import argparse
import re
from datetime import date
from pathlib import Path

import yaml

from common.util import OUT, ROOT, ensure_out, now_iso

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
PRICE_LINE_RE = re.compile(r"\$\s?(\d[\d,]*)")
UPDATED_RE = re.compile(r"last\s+updated[:\s]+([A-Za-z]+ \d{1,2}, \d{4})", re.IGNORECASE)
HOURS_RE = re.compile(
    r"((?:daily|mon|tue|wed|thu|fri|sat|sun)[a-z]*(?:\s*[-–]\s*[a-z]+)?\s+\d{1,2}\s?[ap]m\s?[-–]\s?\d{1,2}\s?[ap]m)",
    re.IGNORECASE)
YEAR_RE = re.compile(r"\b(20\d{2})\b")

# Product keywords for grouping price mentions across pages.
PRODUCTS = {
    "Private Tasting Suite": ["private tasting suite", "tasting suite"],
    "Barrel Cellar": ["barrel cellar"],
    "Seated Tasting": ["seated tasting"],
    "Wine Club 6-bottle": ["6-bottle"],
    "Wine Club 9-bottle": ["9-bottle"],
    "Wine Club Cellar tier": ["cellar tier", "cellar at"],
}


def load_page(target, live: bool):
    body = None
    fetched_live = False
    if live:
        from common.http_fetch import polite_get
        body = polite_get(target["url"])
        fetched_live = body is not None
    if body is None:
        body = (ROOT / target["snapshot"]).read_text()
    return body, fetched_live


def extract_facts(target, html_body):
    text_lines = [TAG_RE.sub(" ", l).strip() for l in html_body.splitlines()]
    joined = " ".join(text_lines)
    title = TITLE_RE.search(html_body)
    facts = {
        "id": target["id"], "url": target["url"],
        "title": " ".join(title.group(1).split()) if title else "",
        "prices": [],   # (product, amount, quote)
        "updated": UPDATED_RE.search(joined).group(1) if UPDATED_RE.search(joined) else None,
        "hours": [h.strip() for h in HOURS_RE.findall(joined)],
        "offering_years": [],
    }
    lowered_all = joined.lower()
    # attribute each $ amount to a product by shared sentence/line context
    for line in text_lines:
        if "$" not in line:
            continue
        low = line.lower()
        # Bind each amount to the NEAREST PRECEDING product mention on the
        # line, so "6-bottle at $135, 9-bottle at $200" doesn't cross-wire.
        mentions = sorted(
            (low.index(k), product)
            for product, keys in PRODUCTS.items() for k in keys if k in low)
        for m in PRICE_LINE_RE.finditer(line):
            preceding = [p for pos, p in mentions if pos < m.start()]
            if preceding:
                amount = float(m.group(1).replace(",", ""))
                facts["prices"].append((preceding[-1], amount, line.strip()))
    if "tock" in target["id"] or "book" in lowered_all[:400]:
        facts["offering_years"] = [int(y) for y in YEAR_RE.findall(joined)]
    return facts


def run_checks(pages):
    findings = []
    today = date.today()

    # C1 — price conflicts across pages
    by_product = {}
    for p in pages:
        for product, amount, quote in p["prices"]:
            by_product.setdefault(product, []).append((p["url"], amount, quote))
    for product, mentions in by_product.items():
        amounts = {m[1] for m in mentions}
        if len(amounts) > 1:
            findings.append({
                "check": "C1 price conflict", "severity": "HIGH",
                "what": (f"**{product}** is published at "
                         f"{' and '.join(f'${a:g}' for a in sorted(amounts))} on different live pages. "
                         "Any guest can find both; whichever one they saw first is the one they'll "
                         "expect at checkout."),
                "evidence": [f"[{u}]({u}) — “{q}”" for u, a, q in mentions]})

    for p in pages:
        # C2 — draft artifacts live in production
        if "draft" in p["title"].lower():
            findings.append({
                "check": "C2 draft page title", "severity": "MEDIUM",
                "what": (f"The page title of {p['url']} is literally **“{p['title']}”** — a working "
                         "draft is live in production (and that title is what Google shows)."),
                "evidence": [f"[{p['url']}]({p['url']}) — <title>{p['title']}</title>"]})
        # C3 — stale policy dates
        if p["updated"]:
            year = int(p["updated"].split(",")[-1])
            age = today.year - year
            if age >= 2:
                findings.append({
                    "check": "C3 stale policy date", "severity": "MEDIUM",
                    "what": (f"The privacy policy at {p['url']} was last updated **{p['updated']}** "
                             f"— roughly {age} years ago, predating current NY/CA privacy rules and "
                             "the current e-commerce stack."),
                    "evidence": [f"[{p['url']}]({p['url']}) — “Last updated: {p['updated']}”"]})
        # C4 — stale offerings on booking surfaces
        stale_years = sorted({y for y in p["offering_years"] if y < today.year - 1})
        if stale_years:
            findings.append({
                "check": "C4 stale listing", "severity": "MEDIUM",
                "what": (f"The booking page {p['url']} still advertises offerings dated "
                         f"**{', '.join(map(str, stale_years))}** — a guest booking today is looking "
                         "at a listing at least two seasons old."),
                "evidence": [f"[{p['url']}]({p['url']})"]})

    # C5 — hours mismatch between Google Business and the website
    hour_sets = {p["id"]: (p["hours"], p["url"]) for p in pages if p["hours"]}
    google = hour_sets.get("google_business")
    if google:
        for pid, (hours, url) in hour_sets.items():
            if pid == "google_business":
                continue
            if {h.lower() for h in hours} != {h.lower() for h in google[0]}:
                findings.append({
                    "check": "C5 hours mismatch", "severity": "MEDIUM",
                    "what": (f"Google Business says **“{google[0][0]}”** but {url} says "
                             f"**“{hours[0]}”**. Whichever is right, one public surface is telling "
                             "guests the wrong hours."),
                    "evidence": [f"[{google[1]}]({google[1]}) — “{google[0][0]}”",
                                 f"[{url}]({url}) — “{hours[0]}”"]})
    return findings


def render_report(findings, pages, live_count):
    today = date.today().isoformat()
    src_note = (f"{live_count} page(s) captured live today, "
                f"{len(pages) - live_count} from cached snapshots"
                if live_count else
                "all pages from cached snapshots — run `python -m audit.pipeline --live` "
                "the morning of the meeting for same-day captures")
    lines = [
        f"# Macari Vineyards — Public-Surface Audit",
        f"**Date:** {today} · **Pages reviewed:** {len(pages)} · **Findings:** {len(findings)}",
        "",
        "*Every fact below is quoted from a public page, with its source linked. "
        "No logins, no personal data, read-only.* "
        f"({src_note})",
        "",
        "---", ""]
    if not findings:
        lines.append("No contradictions found across the reviewed surfaces. ✅")
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    for i, f in enumerate(sorted(findings, key=lambda f: order[f["severity"]]), 1):
        lines += [f"## Finding {i} — {f['check']}  ·  {f['severity']}", "", f["what"], "",
                  "Evidence:", *[f"- {e}" for e in f["evidence"]], ""]
    lines += ["---",
              f"Pages reviewed: " + ", ".join(f"[{p['id']}]({p['url']})" for p in pages), "",
              f"*Generated {now_iso()} by Project Bergen stale-surface audit "
              "(deterministic checks; no generative model touches this report).*"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    with open(ROOT / "audit" / "targets.yaml") as f:
        targets = yaml.safe_load(f)["targets"]

    pages, live_count = [], 0
    for t in targets:
        body, fetched_live = load_page(t, args.live)
        live_count += fetched_live
        pages.append(extract_facts(t, body))

    findings = run_checks(pages)
    report = render_report(findings, pages, live_count)
    ensure_out()
    out_path = OUT / "surface_audit.md"
    out_path.write_text(report)
    print(f"Surface audit: {len(findings)} finding(s) across {len(pages)} pages -> {out_path}")
    for f in findings:
        print(f"  [{f['severity']:<6}] {f['check']}")


if __name__ == "__main__":
    main()
