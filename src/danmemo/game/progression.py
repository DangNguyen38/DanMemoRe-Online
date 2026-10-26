"""Master-backed player EXP and persistent daily-login accounting.

Daily/monthly boundaries follow the recovered English client's PST clock.
No unrecorded historic login days are invented.
"""

from danmemo import paths as _paths
import functools
import gzip
import hashlib
import json
import time


@functools.cache
def catalog():
    return json.loads(
        gzip.decompress(
            (_paths.METADATA / "offline_progression_catalog.json.gz").read_bytes()
        )
    )


@functools.cache
def levels():
    return catalog()["levels"]


def tokens(oc, state, changed):
    for table in changed:
        state["tokens"][table] = hashlib.md5(
            oc.msgpack_value(state["tables"][table])
        ).hexdigest()


def award_exp(oc, state, stage_id, clear_count=1):
    amount = catalog()["stageExp"].get(str(stage_id), 0) * clear_count
    infos = state["tables"].get("UserInfo", [])
    if not infos or not amount:
        return (0, set())
    status = infos[0].get("playerStatus")
    if not isinstance(status, dict):
        return (0, set())
    old_level = status.get("level", 1)
    status["totalExp"] = status.get("totalExp", 0) + amount
    eligible = [r for r in levels() if r[0] <= status["totalExp"]]
    if eligible:
        status["level"] = max(status.get("level", 1), eligible[-1][1])
    restored = sum(
        (
            row[2]
            for row in levels()
            if old_level < row[1] <= status.get("level", old_level)
        )
    )
    if restored:
        status["baseSP"] = status.get("baseSP", 0) + restored
    changed = {"UserInfo"}
    tokens(oc, state, changed)
    return (amount, changed)


def award_clear_rewards(oc, state, stage, completed_tasks):
    """Issue archived first-clear/task rewards once; tables are authoritative."""
    from danmemo.game import resources as resources

    rules = catalog()
    rewards = []
    if stage.get("state") != 4 and (not stage.get("firstCompleteDateTime")):
        reward = rules.get("firstClearRewards", {}).get(str(stage["adventureStageId"]))
        if reward:
            rewards.append(reward)
    tasks = set(completed_tasks)
    for row in stage.get("tasks", []):
        if row["adventureStageTaskId"] in tasks and row.get("state") != 2:
            reward = rules.get("taskRewards", {}).get(str(row["adventureStageTaskId"]))
            if reward:
                rewards.append(reward)
    changed = set()
    for table, key, iid, amount in rewards:
        resources.add(
            state["tables"], table, key, iid, amount, oc.LOCAL_USER_ID, int(time.time())
        )
        changed.add(table)
    tokens(oc, state, changed)
    return changed


@functools.cache
def login_achievements():
    return catalog()["loginAchievements"]


def record_login(oc, now=None):
    from danmemo.game.daily_windows import (
        mission_day as mission_day,
        mission_month as mission_month,
        reset_mission_month as reset_mission_month,
    )

    now = int(time.time()) if now is None else int(now)
    day = mission_day(now)
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        from danmemo.game.mission_history import snapshot as snapshot_missions

        mission_history_changed = snapshot_missions(state)
        tables = state["tables"]
        rows = tables.setdefault("UserStatus", [])
        if not rows:
            rows.append(
                dict(
                    userId=oc.LOCAL_USER_ID,
                    totalLogin=0,
                    totalLoginDays=0,
                    continuousLogin=0,
                    createdAt=now,
                    lastLoginedAt=0,
                    lastLoginedDateAt=0,
                    adColonyLastViewedAt=0,
                    subscriptionAgreementState=[],
                    subscriptionFreeTrial=[],
                    subscriptionTrialEntryPeriod=[],
                )
            )
        status = rows[0]
        repaired = False
        for row in tables.get("UserAchievement", []):
            if row.get("readState") == 0:
                row["readState"] = 1
                repaired = True
        ledger = state.setdefault("dailyLogin", {})
        previous = ledger.get("lastDay")
        if ledger.get("dayBasis") != "JST9":
            stamp = status.get("lastLoginedAt") or status.get("lastLoginedDateAt", 0)
            if stamp:
                old_previous = previous
                previous = mission_day(stamp)
                for counter in ledger.get("periodCounts", {}).values():
                    if counter.get("lastDay") == old_previous:
                        counter["lastDay"] = previous
                for counter in state.get("monthlyLoginContexts", {}).values():
                    if counter.get("lastDay") == old_previous:
                        counter["lastDay"] = previous
            elif previous is not None:
                previous = day
            ledger.update(dayBasis="JST9", lastDay=previous)
            repaired = True
        new_day = previous != day
        total = max(status.get("totalLogin", 0), status.get("totalLoginDays", 0)) + int(
            new_day
        )
        if new_day:
            status.update(
                totalLogin=total,
                totalLoginDays=total,
                continuousLogin=status.get("continuousLogin", 0) + 1
                if previous == day - 1
                else 1,
                lastLoginedAt=now,
                lastLoginedDateAt=now,
            )
            ledger["lastDay"] = day
        period_counts = ledger.setdefault("periodCounts", {})
        changed = {"UserStatus"} if new_day else set()
        repaired = repaired or mission_history_changed
        if repaired:
            changed.add("UserAchievement")
        context = state.get("activeMonthlyContext", mission_month(now))
        login_counter = state.setdefault("monthlyLoginContexts", {}).setdefault(
            context, dict(days=0, lastDay=-1)
        )
        if login_counter["lastDay"] != day:
            login_counter.update(days=login_counter["days"] + 1, lastDay=day)
            changed.add("UserAchievement")
        achievements = {
            r["achievementId"]: r for r in tables.setdefault("UserAchievement", [])
        }
        for aid, kind, target, start, end in login_achievements():
            if start and now < start or (end and now >= end):
                continue
            if kind == 3105:
                month = mission_month(now)
                period = f"{start}:{end}:{month}"
                if period not in period_counts:
                    period_counts[period] = {"days": 0, "lastDay": -1}
                counter = period_counts[period]
                if counter["lastDay"] != day:
                    counter.update(days=counter["days"] + 1, lastDay=day)
                    changed.add("UserAchievement")
                value = counter["days"]
            else:
                value = total
            row = achievements.get(aid)
            if row is None:
                row = dict(
                    userId=oc.LOCAL_USER_ID,
                    achievementId=aid,
                    state=1,
                    progressParam=0,
                    completedAt=0,
                    lastResetAt=now,
                    readState=1,
                    signature=0,
                )
                tables["UserAchievement"].append(row)
                achievements[aid] = row
            elif kind == 3105 and reset_mission_month(row, now):
                changed.add("UserAchievement")
            if row.get("state") == 4:
                continue
            before = dict(row)
            row["progressParam"] = max(row.get("progressParam", 0), value)
            if row["progressParam"] >= target:
                row.update(state=3, completedAt=row.get("completedAt") or now)
            if row != before:
                changed.add("UserAchievement")
        for row in tables.get("UserAdventureLevelInfo", []):
            if row.get("totalLogin") != total:
                row["totalLogin"] = total
                changed.add("UserAdventureLevelInfo")
        if changed:
            tokens(oc, state, changed)
            oc.write_progress(state)
        return new_day
