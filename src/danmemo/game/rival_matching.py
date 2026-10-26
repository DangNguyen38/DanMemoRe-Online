"""Stable local matching windows; publisher matchmaking is not archived."""

import copy


def select(pool, saved, info_key):
    """Keep reads stable, rotate on explicit updates or completed battles."""
    if not pool:
        return []
    offset = saved.get("matchingGeneration", 0) % len(pool)
    ordered = pool[offset:] + pool[:offset]
    recent = saved.get("lastAdversaryIds", [])[:1]
    ordered = [r for r in ordered if r[info_key]["userId"] not in recent] + [
        r for r in ordered if r[info_key]["userId"] in recent
    ]
    return copy.deepcopy(ordered[:3])


def advance(saved, rival=None, receipt=None):
    if saved.get("activeBattle"):
        return False
    receipts = saved.setdefault("matchingReceipts", [])
    if receipt is not None and str(receipt) in receipts:
        return False
    saved["matchingGeneration"] = saved.get("matchingGeneration", 0) + 1
    if rival is not None:
        saved["lastAdversaryIds"] = [rival] + [
            uid for uid in saved.get("lastAdversaryIds", []) if uid != rival
        ][:9]
    if receipt is not None:
        receipts.append(str(receipt))
        del receipts[:-128]
    return True
