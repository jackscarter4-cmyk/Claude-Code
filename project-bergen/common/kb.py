"""Loader + typed views over ground_truth/packages.yaml (the single source
of truth). Everything the validator and the drafters know about allowed
numbers comes from here."""
from functools import lru_cache

import yaml

from common.util import ROOT


@lru_cache(maxsize=1)
def load_kb():
    with open(ROOT / "ground_truth" / "packages.yaml") as f:
        return yaml.safe_load(f)


def allowed_dollar_amounts():
    """Dollar amounts a draft is allowed to quote: every non-conflicted
    published price plus wine-club tier prices. Conflicted prices are
    deliberately EXCLUDED — quoting one is a validation failure."""
    kb = load_kb()
    amounts = set()
    for exp in kb["experiences"].values():
        if exp.get("conflict"):
            continue
        price = exp.get("price_per_guest")
        if isinstance(price, (int, float)):
            amounts.add(float(price))
    for tier in kb["wine_club"]["tiers"]:
        amounts.add(float(tier["price_per_shipment"]))
    return amounts


def conflicted_dollar_amounts():
    kb = load_kb()
    amounts = set()
    for exp in kb["experiences"].values():
        if exp.get("conflict"):
            for p in exp.get("prices", []):
                amounts.add(float(p["price_per_guest"]))
    return amounts


def allowed_bare_numbers():
    """Non-dollar numbers a draft may state: capacities, guest ranges,
    building/acre counts, club cadence numbers."""
    kb = load_kb()
    nums = set()
    for exp in kb["experiences"].values():
        for key in ("min_guests", "max_guests", "capacity_seated", "buildings",
                    "acres", "rentals_per_day"):
            v = exp.get(key)
            if isinstance(v, (int, float)):
                nums.add(int(v))
    nums.add(int(kb["wine_club"]["minimum_commitment_shipments"]))
    nums.add(len(kb["wine_club"]["tiers"]))
    return nums


def closed_dates():
    kb = load_kb()
    return {str(d) for d in kb["policies"]["closed_dates_2026"]}


def escape_phrase():
    return load_kb()["policies"]["escape_phrase"]


def experience(key):
    return load_kb()["experiences"][key]
