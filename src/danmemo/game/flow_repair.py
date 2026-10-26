"""Shared progression repairs. Rookie activation is a server reconstruction.

Ticket order is recovered from native 0x234ae34: default, then subscription.
The snapshot-based skip adapter imports the post-debit stock and emits no
native completion operation; do not replay a second local consumption.
"""

import functools
import hashlib
import time
from danmemo.master import reader as master


def stamp(oc, state, table):
    state["tokens"][table] = hashlib.md5(
        oc.msgpack_value(state["tables"][table])
    ).hexdigest()


@functools.cache
def rookie_ids():
    from danmemo.game.reward_delivery import decoded as decoded, num as num

    return sorted(
        (
            num(r, 0)
            for r in decoded("achievement")
            if num(r, 2) == 5
            and str(r.get("1", "")).startswith("achievement.beginner_")
        )
    )


def activate_rookie(oc, state):
    rows = state["tables"].setdefault("UserAchievement", [])
    by_id = {r["achievementId"]: r for r in rows}
    for aid in rookie_ids():
        row = by_id.get(aid)
        if row and row.get("state") == 4:
            continue
        if not any((i in by_id for i in rookie_ids())):
            return False
        if row and row.get("state") in (2, 3):
            return False
        if row is None:
            row = dict(
                userId=oc.LOCAL_USER_ID,
                achievementId=aid,
                signature=max((r.get("signature", 0) for r in rows), default=0) + 1,
                progressParam=0,
                completedAt=0,
                lastResetAt=0,
                readState=1,
            )
            rows.append(row)
        row["state"] = 2
        stamp(oc, state, "UserAchievement")
        return True
    return False


@functools.cache
def rookie_stage_requirements():
    from danmemo.game.reward_delivery import decoded as decoded, num as num

    stages = {r.get("7"): num(r, 0) for r in decoded("adventureStage")}
    return {
        num(r, 0): (stages[r.get("10")], num(r, 9))
        for r in decoded("achievement")
        if num(r, 2) == 5 and num(r, 4) == 203 and (r.get("10") in stages)
    }


@functools.cache
def rookie_snapshot_requirements():
    from danmemo.game.reward_delivery import decoded as decoded, num as num

    return {
        num(r, 0): r
        for r in decoded("achievement")
        if num(r, 2) == 5 and num(r, 4) in (104, 105, 106)
    }


def reconcile_rookie_stages(oc, state):
    """Repair missed native completion notifications using saved stage evidence."""
    stages = {
        r["adventureStageId"]: r for r in state["tables"].get("UserAdventureStage", [])
    }
    changed = False
    for row in state["tables"].get("UserAchievement", []):
        rule = rookie_stage_requirements().get(row["achievementId"])
        if row.get("state") != 2 or rule is None:
            continue
        stage = stages.get(rule[0], {})
        if stage.get("state") != 4:
            continue
        progress = max(
            row.get("progressParam", 0), stage.get("cumulativeClearCount", 0)
        )
        if progress != row.get("progressParam", 0):
            row["progressParam"] = progress
            changed = True
        if progress >= rule[1] > 0:
            row.update(
                state=3,
                completedAt=stage.get("lastCompleteDateTime") or int(time.time()),
            )
            changed = True
    from danmemo.game.mission_sync import observed_progress as observed_progress
    from danmemo.game.reward_delivery import num as num

    active = {
        r["achievementId"]: r
        for r in state["tables"].get("UserAchievement", [])
        if r.get("state") == 2 and r["achievementId"] in rookie_snapshot_requirements()
    }
    rules = [rookie_snapshot_requirements()[aid] for aid in active]
    now = int(time.time())
    for aid, progress in observed_progress(state, rules, now).items() if rules else ():
        row = active[aid]
        progress = max(row.get("progressParam", 0), progress)
        if progress != row.get("progressParam", 0):
            row["progressParam"] = progress
            changed = True
        if progress >= num(rookie_snapshot_requirements()[aid], 9) > 0:
            row.update(state=3, completedAt=now)
            changed = True
    if changed:
        stamp(oc, state, "UserAchievement")
    return changed


@functools.cache
def ticket_ids():
    ids = {
        r.text(1): r.number(0) for r in master.rows(master.master_bytes("skipTicket"))
    }
    return [ids[s] for s in ("skip_ticket.default", "skip_ticket.sub")]


def debit_skip(oc, state, count):
    rows = state["tables"].get("UserSkipTicket", [])
    by_id = {r["skipTicketId"]: r for r in rows}
    available = sum((max(0, by_id.get(i, {}).get("amount", 0)) for i in ticket_ids()))
    if available < count:
        raise ValueError("Insufficient quest skip tickets")
    pending = state.setdefault("skipTicketReconciliation", {})
    for iid in ticket_ids():
        row = by_id.get(iid)
        if row is None:
            continue
        used = min(count, max(0, row.get("amount", 0)))
        if used:
            row["amount"] -= used
            pending[str(iid)] = row["amount"]
            count -= used
    stamp(oc, state, "UserSkipTicket")


def reconcile_ticket(state, row):
    pending = state.get("skipTicketReconciliation", {})
    iid = str(row["skipTicketId"])
    if iid not in pending:
        return row
    saved = next(
        (
            r
            for r in state["tables"].get("UserSkipTicket", [])
            if r["skipTicketId"] == row["skipTicketId"]
        ),
        None,
    )
    if saved is None:
        return row
    acknowledged = row.get("amount") == saved["amount"]
    row = dict(row)
    row["amount"] = min(row.get("amount", 0), saved["amount"])
    if acknowledged:
        pending.pop(iid, None)
    return row
