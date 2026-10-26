"""Native UserAchievement.progressParam is signed int32, not a score int64.

Original MessagePack reader 0x2651e30 calls checked conversion 0x28b7d6c:
positive values with any bit above bit 30 throw before save-data loading.
Actual RB/FR/other score counters retain their original full precision.
"""

import hashlib

MIN_PROGRESS = -2147483648
MAX_PROGRESS = 2147483647


def project(state, row, value):
    if type(value) is not int or MIN_PROGRESS <= value <= MAX_PROGRESS:
        return (value, False)
    exact = dict(
        progressParam=value,
        lastResetAt=row.get("lastResetAt", 0),
        completedAt=row.get("completedAt", 0),
    )
    ledger = state.setdefault("achievementProgressOverflow", {})
    key = str(row["achievementId"])
    changed = ledger.get(key) != exact
    if changed:
        ledger[key] = exact
    return (min(MAX_PROGRESS, max(MIN_PROGRESS, value)), changed)


def repair(client, state):
    changed = False
    rows = state.get("tables", {}).get("UserAchievement", [])
    for row in rows:
        value = row.get("progressParam", 0)
        if type(value) is int and (not MIN_PROGRESS <= value <= MAX_PROGRESS):
            row["progressParam"], _ = project(state, row, value)
            changed = True
    if changed:
        state.setdefault("tokens", {})["UserAchievement"] = hashlib.md5(
            client.msgpack_value(rows)
        ).hexdigest()
    return changed
