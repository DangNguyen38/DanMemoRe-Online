"""Stream-backed pickup reconstruction; archived pools remain unchanged on disk.

The reference Prelude stream shows 4% Adventurer / 2% Assist at rarity 4,
with two featured Adventurers at 1% each. The regular Anniversary stream shows
one featured Adventurer at 1% within a 2% bucket. Both allocate half of the
mixed role bucket to pickups. The corresponding archived normal pool
contains uniform 4%/40 and 2%/21 entries, so server pickup allocation is absent.
Generalization to other banners is an explicitly offline policy, not original
server recovery. Preserve each rarity/role budget and guarantee membership.
"""

from collections import defaultdict
import math

POLICY = "stream-pickup-v2"


def probabilities(pool):
    result = defaultdict(float)
    total = math.fsum((weight for weight, _ in pool))
    if total <= 0:
        return {}
    for weight, pcs in pool:
        if not pcs or not math.isfinite(weight) or weight < 0:
            raise ValueError("Invalid lottery pool")
        for pc in pcs:
            result[pc] += weight / total / len(pcs)
    return dict(result)


def adjust_pool(pool, featured, pcs):
    """Return the same pool when no deficient mixed 4-star bucket exists."""
    original = probabilities(pool)
    buckets = defaultdict(dict)
    for pid, rate in original.items():
        meta = pcs.get(str(pid), {})
        buckets[meta.get("rarity"), meta.get("role")][pid] = rate
    effective = dict(original)
    changed = False
    for (rarity, role), rates in buckets.items():
        if rarity != 4 or role not in ("Adventurer", "Assist"):
            continue
        picked = set(rates) & set(featured)
        others = set(rates) - picked
        if not picked or not others:
            continue
        if all(
            (
                rates[pid] > max((rates[other] for other in others)) + 1e-15
                for pid in picked
            )
        ):
            continue
        budget = math.fsum(rates.values())
        target = budget / (2 * len(picked))
        if all((rates[pid] + 1e-15 >= target for pid in picked)):
            continue
        allocation = {pid: max(rates[pid], target) for pid in picked}
        used = math.fsum(allocation.values())
        if used >= budget:
            allocation = {pid: target for pid in picked}
            used = math.fsum(allocation.values())
        remaining = budget - used
        other_total = math.fsum((rates[pid] for pid in others))
        if other_total <= 0:
            continue
        effective.update(allocation)
        effective.update({pid: rates[pid] * remaining / other_total for pid in others})
        changed = True
    if not changed:
        return pool
    total = math.fsum((weight for weight, _ in pool))
    return [[effective[pid] * total, [pid]] for pid in original]


def apply(catalog, notices):
    """Transform the freshly decoded runtime catalog, including future banners."""
    for lid, banner in catalog["lotteries"].items():
        if banner.get("selection"):
            continue
        featured = notices.get("banners", {}).get(str(lid), {}).get("featured", [])
        if not featured:
            continue
        changed = False

        def update(container, key):
            nonlocal changed
            before = container[key]
            after = adjust_pool(before, featured, notices["pcs"])
            if after is not before:
                container[key] = after
                changed = True

        for key in banner["pools"]:
            update(banner["pools"], key)
        for step in banner["steps"].values():
            for key in ("normal", "fixed"):
                update(step, key)
        for key in banner.get("guarantees", {}):
            update(banner["guarantees"], key)
        if changed:
            banner["ratePolicy"] = POLICY
    return catalog
