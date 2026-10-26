"""Repair only membership objectives explicitly contaminated by test setup.

Original 0x2363f14 publishes FamiliaMaximumMembersEvent, not member capacity.
Never reset genuine historical completions or revoke an already claimed reward.
"""

import hashlib


def reconcile(oc, state):
    repairs = state.get("familiaMemberQuestRepair", {})
    world = getattr(oc, "ONLINE_SHARED", None)
    if not repairs or world is None:
        return False
    import json
    from danmemo.game.reward_delivery import decoded as decoded, num as num

    rules = {num(r, 0): r for r in decoded("achievement") if num(r, 4) == 3101}
    with world.connect() as db:
        family = db.execute(
            "SELECT f.info, f.id FROM members m JOIN familias f ON f.id=m.familia WHERE m.uid=?",
            (world.uid,),
        ).fetchone()
        if not family:
            return False
        count = db.execute(
            "SELECT COUNT(*) FROM members WHERE familia=?", (family["id"],)
        ).fetchone()[0]
        maximum = max(count, json.loads(family["info"]).get("maximumMembers", 0))
    changed = False
    for row in state["tables"].get("UserAchievement", []):
        aid = row["achievementId"]
        repair = repairs.get(str(aid))
        if not repair or aid not in rules or row.get("state") == 4:
            continue
        progress = max(
            repair["verifiedMaximum"],
            maximum if repair["familiaId"] == family["id"] else 0,
        )
        if repair["verifiedMaximum"] != progress:
            repair["verifiedMaximum"] = progress
            changed = True
        if progress >= num(rules[aid], 9):
            corrected = dict(row, progressParam=progress)
        else:
            corrected = dict(row, progressParam=progress, state=2, completedAt=0)
        if corrected != row:
            row.update(corrected)
            changed = True
    if changed:
        state["tokens"]["UserAchievement"] = hashlib.md5(
            oc.msgpack_value(state["tables"]["UserAchievement"])
        ).hexdigest()
    return changed
