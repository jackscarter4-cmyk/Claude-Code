"""Polite live RSS pull for press feeds listed in ground_truth/press_sources.yaml.

Etiquette (see config.yaml http block): identifiable UA, robots.txt honored,
rate-limited, cached to out/http_cache/, read-only. Any failure degrades
silently to fixtures — the digest never depends on the network.

Only items mentioning Macari are kept; everything else is discarded unread.
"""
import xml.etree.ElementTree as ET

import yaml

from common.http_fetch import polite_get
from common.util import ROOT


def fetch_press_rss():
    with open(ROOT / "ground_truth" / "press_sources.yaml") as f:
        sources = yaml.safe_load(f)["sources"]
    items, pulled, skipped = [], 0, 0
    for src in sources:
        if not src.get("rss"):
            continue
        body = polite_get(src["rss"])
        if body is None:
            skipped += 1
            continue
        pulled += 1
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            continue
        for entry in root.iter("item"):
            title = (entry.findtext("title") or "").strip()
            link = (entry.findtext("link") or "").strip()
            desc = (entry.findtext("description") or "").strip()
            pub = (entry.findtext("pubDate") or "").strip()
            if "macari" in f"{title} {desc}".lower():
                items.append({"id": f"live-{hash(link) & 0xffffff:x}",
                              "outlet": src["name"], "date": pub, "title": title,
                              "excerpt": desc[:300], "url": link, "live": True})
    return items, f"{pulled} feed(s) fetched, {skipped} unreachable, {len(items)} Macari mention(s)"
