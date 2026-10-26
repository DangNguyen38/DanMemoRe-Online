"""Retain seasonal quest progress when native screens discard inactive rows.

Snapshots are private-server persistence; original achievement IDs, resets and
claim receipts remain authoritative. No claim or reward is created here.
"""

import copy
import functools


@functools.cache
def rules():
    from danmemo.game.reward_delivery import decoded as decoded, num as num

    return {num(r, 0): r for r in decoded("achievement") if num(r, 2) in (3, 8)}


def preserve_push(row, previous):
    from danmemo.game.daily_windows import (
        mission_day as mission_day,
        mission_month as mission_month,
    )
    from danmemo.game.reward_delivery import num as num

    rule = rules().get(row.get("achievementId"))
    if not rule or not previous:
        return row
    recurring = num(rule, 20)
    same = (
        mission_day(row.get("lastResetAt", 0))
        == mission_day(previous.get("lastResetAt", 0))
        if recurring == 1
        else mission_month(row.get("lastResetAt", 0))
        == mission_month(previous.get("lastResetAt", 0))
        if recurring == 3 or num(rule, 2) == 8
        else True
    )
    if same and row.get("progressParam", 0) < previous.get("progressParam", 0):
        row = copy.deepcopy(row)
        row["progressParam"] = previous["progressParam"]
    return row


def snapshot(state):
    from danmemo.game.daily_windows import (
        mission_day as mission_day,
        mission_month as mission_month,
    )
    from danmemo.game.reward_delivery import num as num

    changed = False
    for row in state["tables"].get("UserAchievement", []):
        rule = rules().get(row["achievementId"])
        if not rule:
            continue
        reset = row.get("lastResetAt", 0)
        cycle = (
            "day:" + str(mission_day(reset))
            if num(rule, 20) == 1
            else "month:" + state.get("activeMonthlyContext", mission_month(reset))
            if num(rule, 2) == 8 or num(rule, 20) == 3
            else "season"
        )
        key = str(row["achievementId"]) + ":" + cycle
        history = state.setdefault("seasonalMissionHistory", {})
        if history.get(key) != row:
            history[key] = copy.deepcopy(row)
            changed = True
    return changed


def select_month(state, settings, now):
    """Changing historical month resumes that month's rows, never another's."""
    from danmemo.game.daily_windows import (
        mission_month as mission_month,
        mission_month_start as mission_month_start,
    )
    from danmemo.game.reward_delivery import num as num

    target = (
        "historical:" + mission_month(settings["historicalEpoch"])
        if settings.get("historicalEpoch")
        else mission_month(now)
    )
    previous = state.get("activeMonthlyContext")
    if previous == target:
        return False
    if previous is None:
        state["activeMonthlyContext"] = target
        counters = state.get("monthlyMissionCounters", {})
        if target not in counters and mission_month(now) in counters:
            counters[target] = copy.deepcopy(counters[mission_month(now)])
        snapshot(state)
        return True
    snapshot(state)
    history = state.setdefault("seasonalMissionHistory", {})
    for row in state["tables"].get("UserAchievement", []):
        rule = rules().get(row["achievementId"])
        if not rule or num(rule, 2) != 8:
            continue
        saved = history.get(str(row["achievementId"]) + ":month:" + target)
        if saved:
            row.update(copy.deepcopy(saved))
            row["lastResetAt"] = mission_month_start(now)
        else:
            row.update(
                state=1 if row.get("state") == 1 else 2,
                progressParam=0,
                completedAt=0,
                lastResetAt=now,
                readState=1,
            )
    state["activeMonthlyContext"] = target
    import hashlib
    from danmemo.game.engine import msgpack_value as msgpack_value

    state["tokens"]["UserAchievement"] = hashlib.md5(
        msgpack_value(state["tables"].get("UserAchievement", []))
    ).hexdigest()
    return True
