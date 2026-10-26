"""Synchronize original mission conditions with accepted account facts.

Unknown action counters retain the client's saved progress; no historic
stamina spending, donations or training actions are invented.
"""

import functools
from danmemo.game.daily_windows import (
    mission_day as mission_day,
    mission_month as mission_month,
)


@functools.cache
def indexes():
    from danmemo.game.reward_delivery import decoded as decoded, num as num

    return (
        {num(r, 0): r for r in decoded("adventureStage")},
        {num(r, 0): r for r in decoded("adventureEpisode")},
        {num(r, 0): r for r in decoded("adventure")},
    )


def observed_progress(state, rules, now):
    from danmemo.game.reward_delivery import num as num, decoded as decoded

    masters, episodes, adventures = indexes()
    groups = {}
    result = {}
    aggregates = {}
    conditions = {num(rule, 4) for rule in rules}
    tags = {
        rule.get("10") for rule in rules if num(rule, 4) in (104, 105, 202, 203, 301)
    } - {None, ""}
    needs_battles = bool(conditions & {101, 102, 103})
    battle_totals = {None: [0, 0], 5: [0, 0], 6: [0, 0]}
    history = (
        state["tables"].get("UserAdventureStage", []) if tags or needs_battles else ()
    )
    for row in history:
        mr = masters.get(row["adventureStageId"])
        if not mr:
            continue
        ep = episodes.get(num(mr, 1), {})
        adv = adventures.get(num(ep, 2), {})
        if needs_battles and num(mr, 5) != 51:
            total = row.get(
                "cumulativeClearCount", int(bool(row.get("firstCompleteDateTime")))
            )
            daily = (
                row.get("dailyClearCount", 0)
                if mission_day(row.get("lastCompleteDateTime", 0)) == mission_day(now)
                else 0
            )
            for kind in (None, num(adv, 2)) if num(adv, 2) in (5, 6) else (None,):
                battle_totals[kind][0] += total
                battle_totals[kind][1] += daily
        for label in {mr.get("7"), ep.get("1"), adv.get("1")} & tags:
            groups.setdefault(label, []).append((mr, row))
    info = next(iter(state["tables"].get("UserInfo", [])), {})
    month = state.get("activeMonthlyContext", mission_month(now))
    counters = state.get("monthlyMissionCounters", {}).get(month, {})
    monthly_ids = {num(r, 0) for r in rules if num(r, 4) in (3107, 3109, 3110, 3111)}
    current_month_progress = (
        {
            r["achievementId"]: r.get("progressParam", 0)
            for r in state["tables"].get("UserAchievement", [])
            if r["achievementId"] in monthly_ids
            and mission_month(r.get("lastResetAt", 0)) == month
        }
        if monthly_ids
        else {}
    )
    rb_tags = {r.get("1"): str(num(r, 0)) for r in decoded("pvbInformation")}
    serial_tags = {r.get("1"): str(num(r, 0)) for r in decoded("serialPvpInformation")}
    for rule in rules:
        condition = num(rule, 4)
        tag = rule.get("10")
        aid = num(rule, 0)
        if condition == 1101:
            from danmemo.game.campaign_features import catalog as catalog

            events = [
                e
                for e in catalog()
                if e["start"] < (num(rule, 14) or 2147483647)
                and num(rule, 13) < e["end"]
            ]
            event_ids = {e["id"] for e in events}
            result[aid] = sum(
                (
                    r.get("count", 0)
                    for r in state["tables"].get("UserVoteInfo", [])
                    if r["voteInfoId"] in event_ids
                )
            )
        elif condition == 603:
            result[aid] = int(
                state.get("dailyLogin", {}).get("lastDay") == mission_day(now)
            )
        elif condition == 602:
            result[aid] = int(num(rule, 13) <= now < num(rule, 14))
        elif condition in (101, 102, 103):
            kind = {102: 5, 103: 6}.get(condition)
            result[aid] = battle_totals[kind][int(num(rule, 20) == 1)]
        elif condition == 106:
            result[aid] = info.get("playerStatus", {}).get("level", 1)
        elif condition in (3502, 3503) and tag in serial_tags:
            saved = state.get("arena", {}).get("serial", {}).get(serial_tags[tag], {})
            result[aid] = (
                saved.get("cumulativeScore", 0)
                if condition == 3502
                else saved.get("dailyBattles", {}).get(str(mission_day(now)), 0)
                if num(rule, 20) == 1
                else saved.get("battleCount", 0)
            )
        elif condition == 1202 and tag in rb_tags:
            saved = state.get("arena", {}).get("pvb", {}).get(rb_tags[tag], {})
            result[aid] = (
                saved.get("cumulativeScore", 0)
                if num(rule, 12) == 1
                else saved.get("scoreInt64", saved.get("score", 0))
            )
        elif condition in (104, 105, 202, 203, 301):
            event = num(rule, 2) == 3
            query = (
                condition,
                tag,
                num(rule, 11),
                num(rule, 20) == 1,
                event,
                num(rule, 13) if event else 0,
                num(rule, 14) if event else 0,
            )
            if query in aggregates:
                result[aid] = aggregates[query]
                continue
            entries = groups.get(tag)
            if entries is None:
                continue
            if num(rule, 2) == 3:
                from danmemo.game.event_missions import (
                    original_windows_overlap as original_windows_overlap,
                )

                entries = [
                    (mr, row)
                    for mr, row in entries
                    if original_windows_overlap(rule, episodes.get(num(mr, 1), {}))
                ]
            difficulty = num(rule, 11)
            if difficulty:
                entries = [(mr, row) for mr, row in entries if num(mr, 4) == difficulty]
            if condition in (104, 105):
                entries = [(mr, row) for mr, row in entries if num(mr, 5) != 51]
                result[aid] = sum(
                    (
                        bool(row.get("firstCompleteDateTime"))
                        and (
                            condition == 104
                            or (
                                bool(row.get("tasks"))
                                and all((t.get("state") == 2 for t in row["tasks"]))
                            )
                        )
                        for mr, row in entries
                    )
                )
            elif num(rule, 20) == 1:
                result[aid] = sum(
                    (
                        row.get("dailyClearCount", 0)
                        for mr, row in entries
                        if mission_day(row.get("lastCompleteDateTime", 0))
                        == mission_day(now)
                    )
                )
            else:
                result[aid] = sum(
                    (
                        row.get(
                            "cumulativeClearCount",
                            int(bool(row.get("firstCompleteDateTime"))),
                        )
                        for mr, row in entries
                    )
                )
            aggregates[query] = result[aid]
        elif condition in (3107, 3109, 3110, 3111):
            key = {
                3107: "staminaSpent",
                3109: "recordBusterScore",
                3110: "warWins",
                3111: "serialBattles",
            }[condition]
            if key in counters:
                result[aid] = max(counters[key], current_month_progress.get(aid, 0))
    return result


def recover_record_buster_counters(state):
    scores = state.get("arena", {}).get("pvb", {})
    missing = {sid for sid, r in scores.items() if "cumulativeScore" not in r}
    if not missing:
        return False
    totals = {sid: 0 for sid in missing}
    seen = set()
    for receipt in state.get("completed", {}).values():
        result = receipt.get("pvbResult", {})
        sid = str(result.get("pvbId", receipt.get("userPvbRecord", {}).get("pvbId", 0)))
        stages = receipt.get("UserAdventureStage", [])
        stage = next(
            (
                r
                for r in stages
                if r.get("adventureStageId") == result.get("adventureStageId")
            ),
            None,
        )
        if sid not in missing or not stage or (not stage.get("lastTryDateTime")):
            continue
        key = (sid, stage["adventureStageId"], stage["lastTryDateTime"])
        if key not in seen:
            totals[sid] += result.get("finalScore", 0)
            seen.add(key)
    for sid, value in totals.items():
        scores[sid]["cumulativeScore"] = max(
            value, scores[sid].get("scoreInt64", scores[sid].get("score", 0))
        )
    return True


def record_clear(state, stage_id, count, now, rb_score=0, stamina_spent=None):
    from danmemo.game.reward_delivery import num as num

    mr = indexes()[0].get(stage_id, {})
    month = state.get("activeMonthlyContext", mission_month(now))
    counters = state.setdefault("monthlyMissionCounters", {}).setdefault(month, {})
    counters["staminaSpent"] = counters.get("staminaSpent", 0) + (
        num(mr, 6) * count if stamina_spent is None else stamina_spent
    )
    if rb_score:
        counters["recordBusterScore"] = counters.get("recordBusterScore", 0) + rb_score


def record_battle(state, kind, won, now):
    month = state.get("activeMonthlyContext", mission_month(now))
    counters = state.setdefault("monthlyMissionCounters", {}).setdefault(month, {})
    if kind == "war":
        counters["warWins"] = counters.get("warWins", 0) + int(won)
    else:
        counters["serialBattles"] = counters.get("serialBattles", 0) + 1
