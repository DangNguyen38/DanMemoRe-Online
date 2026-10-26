"""Retain original non-resetting purchase counters through delayed saves.

Native 0x241fc10 reads shopProduct field 7: only values 1/2/3 enter
calendar-reset comparisons; zero returns false at 0x241fc6c. Do not
invent a reset when another item in the same exchange is purchased.
"""

import copy
import functools


@functools.cache
def rules():
    from danmemo.master import reader as master

    return {
        r.number(0): dict(reset=r.number(7), limit=r.number(8), kind=r.number(9))
        for r in master.rows(master.master_bytes("shopProduct"))
    }


def count(row):
    return max(row.get("count", 0), row.get("purchaseCount", 0))


def protected(row):
    rule = rules().get(row.get("shopProductId"), {})
    return count(row) > 0 and (
        rule.get("kind") == 4 or (rule.get("limit", 0) > 0 and rule.get("reset") == 0)
    )


def reconcile(incoming, saved):
    if protected(saved) and count(incoming) < count(saved):
        result = copy.deepcopy(incoming)
        result["count"] = count(saved)
        if "purchaseCount" in saved or "purchaseCount" in incoming:
            result["purchaseCount"] = count(saved)
        result["lastExchangedAt"] = saved.get("lastExchangedAt", 0)
        return result
    return incoming


def push_requires_correction(state, request):
    """A normal ACK must not import a full table over newer unsent purchases."""
    saved = {r["shopProductId"]: r for r in state["tables"].get("UserShopProduct", [])}
    for delta in request.get("deltas", []):
        for row in delta.get("putItems", {}).get("UserShopProduct", []):
            current = saved.get(row["shopProductId"], {})
            if count(row) != count(current) or row.get(
                "lastExchangedAt", 0
            ) != current.get("lastExchangedAt", 0):
                return True
        for row in delta.get("deleteItems", {}).get("UserShopProduct", []):
            if row["shopProductId"] in saved:
                return True
    return False
