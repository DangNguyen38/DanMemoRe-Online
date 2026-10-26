"""Master-backed visible missions and idempotent server reward mail.

See README.md for scope and limitations. Unsupported conditions fail closed.
"""

from danmemo import paths as _paths
import copy
import functools
import gzip
import hashlib
import json
import re
import time
from danmemo.game import gifts as gifts
from danmemo.master import reader as master
from danmemo.config import modes as modes

ROOT = _paths.ROOT


@functools.cache
def decoded(name):
    path = _paths.STATE / "asset-recovery" / "decoded" / "master" / f"{name}.json"
    if path.is_file():
        result = json.loads(path.read_text("utf-8")).get("0", [])
        return result if isinstance(result, list) else []
    return packaged()["masters"].get(name, [])


@functools.cache
def packaged():
    return json.loads(
        gzip.decompress(
            (_paths.METADATA / "offline_reward_delivery_catalog.json.gz").read_bytes()
        )
    )


def num(row, field):
    value = row.get(str(field), 0)
    return value if type(value) is int else 0


def subscription_required(rule):
    return num(rule, 2) == 8 and "subscribing to" in str(rule.get("21", "")).lower()


@functools.cache
def achievement_ids():
    return {r.get("1"): num(r, 0) for r in decoded("achievement")}


def original_id(label):
    return achievement_ids().get(label)


@functools.cache
def time_limited_ids():
    return {num(r, 0) for r in decoded("achievement") if num(r, 2) == 3}


@functools.cache
def manual_claim_ids():
    from danmemo.game.event_missions import rules as rules

    return {num(r, 0) for r in rules() if num(r, 2) in (1, 2, 3, 5, 8, 9)}


@functools.lru_cache(maxsize=4)
def event_mission_bindings(signature):
    from danmemo.game.event_missions import (
        rules as rules,
        original_windows_overlap as original_windows_overlap,
    )

    active = json.loads(signature)
    result = {}
    if not active:
        return result
    episodes = {str(num(r, 0)): r for r in decoded("adventureEpisode")}
    adventures = {num(r, 0): r for r in decoded("adventure")}
    stages = {}
    for row in decoded("adventureStage"):
        stages.setdefault(str(num(row, 1)), set()).add(row.get("7"))
    by_label = {}
    for index, rule in enumerate(rules()):
        if num(rule, 2) == 3:
            by_label.setdefault(rule.get("10"), []).append((index, rule))
    for identifier, period in active.items():
        ep = episodes.get(identifier)
        if not ep:
            continue
        labels = (
            stages.get(identifier, set())
            | {ep.get("1"), adventures.get(num(ep, 2), {}).get("1")}
        ) - {None, ""}
        for label in labels:
            if label:
                result["__event_label__:" + label] = period
        candidates = {
            index: rule for label in labels for index, rule in by_label.get(label, ())
        }
        for _, rule in sorted(candidates.items()):
            if original_windows_overlap(rule, ep):
                result["__achievement__:" + str(num(rule, 0))] = period
    return result


def periods(server):
    config = _paths.SCHEDULE
    active = (
        json.loads(config.read_text("utf-8")).get("events", {})
        if config.exists()
        else {}
    )
    if server == "offline":
        online_parents = set(modes.selected_familia_events(active))
        episodes = {str(num(r, 0)): num(r, 2) for r in decoded("adventureEpisode")}
        active = {
            eid: period
            for eid, period in active.items()
            if episodes.get(eid) not in online_parents
        }
    settings = modes.config(server)
    return _periods(
        json.dumps(settings["modes"], sort_keys=True),
        json.dumps(active, sort_keys=True),
        json.dumps(settings.get("historicalMissions", {}), sort_keys=True),
    )


@functools.lru_cache(maxsize=8)
def _periods(mode_signature, event_signature, mission_signature="{}"):
    settings = json.loads(mode_signature)
    active = json.loads(event_signature)
    archived_familia = modes.selected_familia_events(active)
    archived_records = modes.selected_record_buster_events(active)
    result = {}
    war = settings.get("war_game")
    if war:
        pid = modes.selected_season("war_game", war)
        info = next((r for r in decoded("pvpInformation") if num(r, 0) == pid), None)
        if info:
            result["__war_start__:" + str(num(info, 1))] = war
        from danmemo.game.arena import grades as grades

        for r in grades(pid):
            result[r.text(2)] = war
    for mode, name, aid_field in [
        ("record_buster", "pvbInformation", 2),
        ("familia_royale", "gveInformation", 2),
        ("ultimate_war_game", "serialPvpInformation", None),
        ("telskyura", "underPvpInformation", None),
        ("familia_war_game", "guildVsGuildInformation", None),
        ("master_familia_war_game", "guildVsGuildInformation", None),
        ("familia_rush", "guildRaidInformation", 2),
        ("union_raid", "unionRaidInformation", 2),
    ]:
        period = settings.get(mode)
        if (
            any((r["mode"] == mode for r in archived_familia.values()))
            and modes.MODES[mode]["id"] not in archived_familia
        ):
            continue
        if (
            mode == "record_buster"
            and archived_records
            and (modes.MODES[mode]["id"] not in archived_records)
        ):
            continue
        if not period:
            continue
        selected = modes.selected_season(mode, period)
        for r in decoded(name):
            if num(r, aid_field if aid_field else 0) == selected:
                tag = r.get("2" if mode == "telskyura" else "1")
                if isinstance(tag, str) and tag:
                    result[tag] = period
    active = json.loads(event_signature)
    for parent, selection in modes.selected_record_buster_events(active).items():
        for row in decoded("pvbInformation"):
            if num(row, 2) == parent and row.get("1"):
                result[row["1"]] = selection["period"]
    for selection in modes.selected_familia_events(active).values():
        result[selection["tag"]] = selection["period"]
        if selection["mode"] == "union_raid":
            result["__union__:" + str(selection["season"])] = selection["period"]
    for selection in modes.selected_element_events(active).values():
        result[selection["tag"]] = selection["period"]
    result.update(event_mission_bindings(json.dumps(active, sort_keys=True)))
    episodes = {str(num(r, 0)): r for r in decoded("adventureEpisode")}
    adventures = {num(r, 0): r for r in decoded("adventure")}
    for identifier, period in active.items():
        row = episodes.get(identifier)
        if row:
            for value in (row.get("1"), adventures.get(num(row, 2), {}).get("1")):
                if isinstance(value, str) and value:
                    result[value] = period
    for row in decoded("adventureStage"):
        period = active.get(str(num(row, 1)))
        if period and isinstance(row.get("7"), str) and row["7"]:
            result[row["7"]] = period
    union = next(
        (r["period"] for r in archived_familia.values() if r["mode"] == "union_raid"),
        settings.get("union_raid"),
    )
    if union:
        result["__union__:" + str(413000001)] = union
    from danmemo.config.historical_mission_schedule import (
        active_periods as active_periods,
    )

    result.update(active_periods(settings, active, json.loads(mission_signature)))
    under = settings.get("telskyura")
    under_info = next(
        (
            r
            for r in decoded("underPvpInformation")
            if under and num(r, 0) == modes.selected_season("telskyura", under)
        ),
        None,
    )
    abyss = settings.get("ultimate_war_game")
    abyss_info = next(
        (
            r
            for r in decoded("serialPvpInformation")
            if abyss and num(r, 0) == modes.selected_season("ultimate_war_game", abyss)
        ),
        None,
    )
    for rule in decoded("achievement"):
        if num(rule, 2) != 3:
            continue
        period = None
        if (
            under_info
            and num(rule, 4) == 905
            and (
                (num(rule, 13), num(rule, 14))
                == (num(under_info, 8), num(under_info, 9))
            )
        ):
            period = under
        if abyss_info and rule.get("10") == abyss_info.get("1"):
            period = abyss
        if period:
            result["__mode_achievement__:" + str(num(rule, 0))] = period
    lotto = settings.get("lotto_orario")
    if lotto:
        from danmemo.game.casino_lotto import lotto_catalog as lotto_catalog

        sid = modes.selected_season("lotto_orario", lotto)
        spec = lotto_catalog()[sid]
        item_label = next(
            (
                r.text(1)
                for r in master.rows(master.master_bytes("takarakujiItem"))
                if r.number(0) == spec["item"]
            )
        )
        for rule in decoded("achievement"):
            if (
                num(rule, 2) == 3
                and num(rule, 15) == 18
                and (rule.get("16") == item_label)
            ):
                draw_at = (
                    lotto.get("drawAt")
                    or lotto.get("end")
                    or lotto.get("start", 0) + 3600
                )
                result["__lotto_achievement__:" + str(num(rule, 0))] = dict(
                    start=lotto["start"], end=draw_at
                )
    from danmemo.game.campaign_missions import periods as campaign_periods

    result.update(campaign_periods(settings, decoded("achievement")))
    return result


def active_rules(server, now):
    tag_periods = periods(server)
    from danmemo.game.daily_windows import (
        is_login_bonus as is_login_bonus,
        login_window as login_window,
    )

    result = []
    for row, start, end in _rule_ranges(json.dumps(tag_periods, sort_keys=True)):
        if is_login_bonus(row) and now >= start:
            start, end = login_window(row, now)
            result.append(dict(row, **{"13": start, "14": end}))
        elif (not start or start <= now) and (not end or now < end):
            result.append(row)
    return result


@functools.lru_cache(maxsize=8)
def _rule_ranges(signature):
    from danmemo.game.event_missions import rules as rules

    tag_periods = json.loads(signature)
    result = []
    for row in rules():
        kind = num(row, 2)
        if kind not in (1, 2, 3, 8, 9):
            continue
        period = tag_periods.get(row.get("10")) if kind == 3 else None
        if kind == 3 and "__event_label__:" + str(row.get("10")) in tag_periods:
            period = tag_periods.get("__achievement__:" + str(num(row, 0)))
        if num(row, 4) in (901, 902):
            period = tag_periods.get("__war_start__:" + str(num(row, 13)), period)
        if kind == 9:
            period = tag_periods.get(
                "__union__:" + str(num(row, 11) or 413000001), period
            )
        if kind in (3, 8) and "__historical_missions__" in tag_periods:
            specific = tag_periods.get("__historical_achievement__:" + str(num(row, 0)))
            if specific is not None:
                period = specific
            elif kind == 3:
                period = (
                    None
                    if "__historical_original_label__:" + str(row.get("10"))
                    in tag_periods
                    else period
                )
                if period is None:
                    period = dict(start=1, end=2)
        period = tag_periods.get("__lotto_achievement__:" + str(num(row, 0)), period)
        period = tag_periods.get("__campaign_achievement__:" + str(num(row, 0)), period)
        if num(row, 4) == 2706:
            period = tag_periods.get(row.get("10"), period)
        period = tag_periods.get("__mode_achievement__:" + str(num(row, 0)), period)
        start, end = (
            (period["start"], period["end"]) if period else (num(row, 13), num(row, 14))
        )
        result.append((row, start, end))
    return result


def mail(oc, state, key, title, rewards, now):
    if key in state.setdefault("gameRewardMailReceipts", {}):
        return False
    title = original_mail_title(key, title)
    from danmemo.game.transactions import identities as identities

    identities_map = identities()
    pending = dict(state)
    pending["tables"] = dict(state["tables"])
    pending["tables"]["UserGift"] = list(state["tables"].get("UserGift", []))
    pending["tokens"] = dict(state.get("tokens", {}))
    pending["adminGiftItems"] = dict(state.get("adminGiftItems", {}))
    pending["gameRewardMailReceipts"] = dict(state.get("gameRewardMailReceipts", {}))
    identifiers = []
    for iid, amount in rewards:
        if amount <= 0:
            continue
        identity = identities_map.get(iid)
        if identity is None:
            raise ValueError("Reward identity not recovered")
        table, _ = identity
        gift = gifts.enqueue(oc, pending, oc.LOCAL_USER_ID, table, iid, amount, now)
        gift.update(title=title, message=reward_message(key, title))
        identifiers.append(gift["id"])
    if not identifiers:
        return False
    pending["gameRewardMailReceipts"][key] = dict(
        giftIds=identifiers, deliveredAt=now, source=title
    )
    gifts.stamp(oc, pending)
    state.clear()
    state.update(pending)
    return True


def reward_message(key, title):
    return title


@functools.cache
def royale_mission_mail_titles():
    template = next(
        (
            r["3"]
            for r in decoded("message")
            if r.get("1") == "guild_event_familia_total_score_reward_txt"
        ),
        "[True Familia Royale] Total Familia Score over {cumulativeScore}",
    )
    return {
        num(r, 0): template.format(cumulativeScore=num(r, 7))
        for r in decoded("familiaAchievement")
        if num(r, 2) == 201
    }


@functools.cache
def royale_total_mail_titles():
    template = next(
        (
            r["3"]
            for r in decoded("message")
            if r.get("1") == "guild_event_familia_total_score_reward_txt"
        ),
        "[True Familia Royale] Total Familia Score over {cumulativeScore}",
    )
    return {
        num(r, 0): template.format(cumulativeScore=num(r, 2))
        for r in decoded("gveTotalFamiliaScoreReward")
    }


def royale_mail_title(key, title):
    if key.startswith("familia-mission:"):
        return royale_mission_mail_titles().get(int(key.rsplit(":", 1)[1]), title)
    if key.startswith("royale-total:"):
        return royale_total_mail_titles().get(int(key.rsplit(":", 1)[1]), title)
    if key.startswith("ranking:familia_royale:"):
        rank = re.search("\\brank (\\d+)\\b", title, re.IGNORECASE)
        if rank:
            template = next(
                (
                    r["3"]
                    for r in decoded("message")
                    if r.get("1") == "gve_familia_ranking_reward_txt"
                ),
                "{eventName} : Familia Ranking\nRank {rank} Reward",
            )
            return template.format(eventName="True Familia Royale", rank=rank[1])
    return title


def original_mail_title(key, title):
    from danmemo.content.reward_text import mail_title as mail_title

    return mail_title(key, royale_mail_title(key, title))


def label_existing_mail(state, key, title):
    receipt = state.get("gameRewardMailReceipts", {}).get(key)
    if not receipt or not title:
        return False
    title = original_mail_title(key, title)
    identifiers = set(receipt.get("giftIds", []))
    changed = False
    message = reward_message(key, title)
    for row in state["tables"].get("UserGift", []):
        if row["id"] in identifiers and (
            row.get("title") != title or row.get("message") != message
        ):
            row.update(title=title, message=message)
            changed = True
    if receipt.get("source") != title:
        receipt["source"] = title
        changed = True
    return changed


def pairs(row, start=4, end=13):
    return [
        (num(row, f), num(row, f + 1))
        for f in range(start, end, 2)
        if num(row, f) > 0 and num(row, f + 1) > 0
    ]


def tally_period(mode, season, settings, selections=None):
    name = {"familia_royale": "gveInformation", "familia_rush": "guildRaidInformation"}[
        mode
    ]
    info = next((r for r in decoded(name) if num(r, 0) == season), None)
    if info is None:
        return None
    parent = num(info, 2)
    selected = (
        modes.selected_familia_events() if selections is None else selections
    ).get(parent)
    if selected and selected["mode"] == mode:
        return selected["period"]
    period = settings.get(mode)
    return period if period and parent == modes.selected_season(mode, period) else None


@functools.cache
def original_item_labels():
    from danmemo.game import resources as resources

    return {
        r.text(2 if name == "memoriaExpItem" else 1): r.number(0)
        for name in resources.NAMES
        for r in master.rows(master.master_bytes(name))
    }


def monster_candidates(state):
    stages = {
        r["adventureStageId"]: r for r in state["tables"].get("UserAdventureStage", [])
    }
    bases = {num(r, 0): r for r in decoded("adventureStageScoreBase")}
    items = original_item_labels()
    result = []
    for rule in decoded("adventureStageScore"):
        sid = num(rule, 1)
        saved = stages.get(sid, {})
        if not saved.get("firstCompleteDateTime"):
            continue
        base = bases.get(num(rule, 2))
        if base is None:
            continue
        for rank, threshold, label_field, amount_field in [
            ("s", num(base, 2), 5, 6),
            ("a", num(base, 3), 8, 9),
            ("b", num(base, 4), 11, 12),
            ("c", 0, 14, 15),
        ]:
            iid = items.get(rule.get(str(label_field)))
            if (
                iid
                and num(rule, amount_field) > 0
                and (saved.get("highScore", 0) >= threshold)
            ):
                result.append(
                    (
                        f"monster-rank:{sid}:{rank}",
                        f"Monsterphilia stage {sid}: rank {rank.upper()} (score >= {threshold})",
                        [(iid, num(rule, amount_field))],
                    )
                )
    return result


def offline_candidates(oc, state, now):
    if hasattr(oc, "ONLINE_SHARED"):
        return []
    result = []
    settings = modes.config("offline")["modes"]
    for mode, table, id_key in [
        ("war_game", "UserPvPRecord", "pvpId"),
        ("record_buster", "UserPvBRecord", "pvbId"),
        ("ultimate_war_game", "UserSerialPvpRecord", "serialPvpId"),
    ]:
        period = settings.get(mode)
        if not period or now < period["end"]:
            continue
        target = modes.selected_season(mode, period)
        season = (
            next(
                (num(r, 0) for r in decoded("pvbInformation") if num(r, 2) == target), 0
            )
            if mode == "record_buster"
            else target
        )
        record = next(
            (r for r in state["tables"].get(table, []) if r.get(id_key) == season), None
        )
        if record is None:
            continue
        key = f"offline:{mode}:{season}"
        record = state.setdefault("offlineRewardRankSnapshots", {}).setdefault(
            key, copy.deepcopy(record)
        )
        if mode == "war_game":
            from danmemo.game.arena import grade_for_score as grade_for_score

            final_grade = grade_for_score(
                season, record.get("score", 1000), record.get("rank", 1)
            )
            for row in decoded("pvpRankingGradeReward"):
                if num(row, 1) == season and num(row, 2) == final_grade:
                    result.append(
                        (
                            key + ":" + str(num(row, 0)),
                            f"Offline War Game season {season}: final grade {final_grade}",
                            pairs(row, 3, 7),
                        )
                    )
        elif mode == "record_buster":
            if record.get("scoreInt64", record.get("score", 0)) <= 0:
                continue
            for name, field in [
                ("pvbTotalRankingReward", "totalRank"),
                ("pvbGroupRankingReward", "groupRank"),
            ]:
                rank = record.get(field, 0)
                for row in decoded(name):
                    if (
                        num(row, 1) == season
                        and num(row, 2) <= rank <= num(row, 3)
                        and (rank > 0)
                    ):
                        result.append(
                            (
                                key + ":" + str(num(row, 0)),
                                f"Offline Record Buster season {season}: {field} {rank}",
                                pairs(row),
                            )
                        )
        else:
            rank = record.get("rank", 0)
            for group in decoded("serialPvpRankingRewardGroup"):
                if (
                    rank > 0
                    and num(group, 1) == 1
                    and (num(group, 2) > 0)
                    and (rank >= num(group, 2))
                    and (num(group, 3) < 0 or rank <= num(group, 3))
                ):
                    items = [
                        (num(r, 2), num(r, 3))
                        for r in decoded("serialPvpRankingRewardItem")
                        if num(r, 1) == num(group, 0)
                    ]
                    result.append(
                        (
                            key + ":" + str(num(group, 0)),
                            f"Offline Ultimate War Game season {season}: final rank {rank}",
                            items,
                        )
                    )
    path = (
        getattr(oc, "PROGRESS_STATE", _paths.STATE / "progress.json").parent
        / "shooting.sqlite3"
    )
    if path.is_file():
        import sqlite3
        from danmemo.game.extra_modes import day as day

        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            for scored in db.execute(
                "SELECT * FROM shooting_scores WHERE uid=? AND day<?",
                (oc.LOCAL_USER_ID, day(now)),
            ):
                rank = (
                    1
                    + db.execute(
                        "SELECT COUNT(*) FROM shooting_scores WHERE season=? AND day=? AND (score>? OR (score=? AND uid<?))",
                        (
                            scored["season"],
                            scored["day"],
                            scored["score"],
                            scored["score"],
                            oc.LOCAL_USER_ID,
                        ),
                    ).fetchone()[0]
                )
                for row in decoded("shootingGameDailyRankingReward"):
                    if (
                        num(row, 1) == scored["season"]
                        and num(row, 4) <= rank
                        and (not num(row, 5) or rank <= num(row, 5))
                    ):
                        result.append(
                            (
                                f"offline-shooting:{scored['season']}:{scored['day']}:{num(row, 0)}",
                                f"Offline Shooting season {scored['season']}: JST day {scored['day']}, rank {rank}",
                                pairs(row, 6, 8),
                            )
                        )
    return result


def shared_candidates(oc, now):
    world = getattr(oc, "ONLINE_SHARED", None)
    if world is None:
        return []
    candidates = []
    specs = [
        ("record_buster", "pvbInformation", "pvbTotalRankingReward", "pvb", False),
        ("war_game", None, "pvpRankingGradeReward", "pvp", False),
        ("ultimate_war_game", None, "serialPvpRankingRewardGroup", "serial", False),
        ("telskyura", None, "underPvpGroupRankingReward", "under", False),
        (
            "familia_rush",
            "guildRaidInformation",
            "guildRaidFamiliaRankingReward",
            "familia_raid_scores",
            True,
        ),
        (
            "familia_royale",
            "gveInformation",
            "gveFamiliaRankingReward",
            "royale_scores",
            True,
        ),
        (
            "union_raid",
            "unionRaidInformation",
            "unionRaidFamiliaRankingReward",
            "union_scores",
            True,
        ),
        (
            "familia_war_game",
            None,
            "guildVsGuildRankingReward",
            "familia_war_battles",
            True,
        ),
    ]
    settings = modes.config("online")["modes"]
    current_buster = modes.MODES["record_buster"]["id"]
    selected_busters = modes.selected_record_buster_events()
    work_items = [
        (
            *spec,
            None,
            selected_busters[current_buster]["period"]
            if spec[0] == "record_buster" and current_buster in selected_busters
            else None,
        )
        for spec in specs
    ]
    work_items.extend(
        (
            (
                "record_buster",
                "pvbInformation",
                "pvbTotalRankingReward",
                "pvb",
                False,
                parent,
                selection["period"],
            )
            for parent, selection in selected_busters.items()
            if parent != current_buster
        )
    )
    selected_familia = modes.selected_familia_events()
    for parent, selection in selected_familia.items():
        mode = selection["mode"]
        for index, spec in enumerate(work_items):
            if spec[0] == mode:
                if parent == modes.MODES[mode]["id"]:
                    work_items[index] = (*spec[:5], parent, selection["period"])
                else:
                    work_items.append((*spec[:5], parent, selection["period"]))
                break
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        from danmemo.game import familia_war_lifecycle as war_lifecycle

        war_lifecycle.sync(world, db, war_lifecycle.REGULAR, now)
        candidates.extend(
            war_lifecycle.mail_candidates(db, war_lifecycle.REGULAR, world.uid)
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS reward_rank_snapshots(key TEXT PRIMARY KEY,ranking TEXT NOT NULL,created INTEGER NOT NULL)"
        )
        member = db.execute(
            "SELECT familia,joined FROM members WHERE uid=?", (world.uid,)
        ).fetchone()
        for (
            mode,
            info,
            reward,
            source,
            clan,
            selected_parent,
            selected_period,
        ) in work_items:
            period = (
                selected_period if selected_period is not None else settings.get(mode)
            )
            if period is None or now < period["end"]:
                continue
            if mode == "familia_royale" and now < period["end"] + 600:
                continue
            if (
                mode == "familia_war_game"
                and war_lifecycle.policy(war_lifecycle.REGULAR) is not None
                and (not war_lifecycle.finished_season(db, war_lifecycle.REGULAR, now))
            ):
                continue
            target = (
                selected_parent
                if selected_parent is not None
                else modes.selected_season(mode, period)
            )
            season = (
                target
                if info is None
                else next(
                    (num(r, 0) for r in decoded(info) if num(r, 2) == target), None
                )
            )
            if season is None:
                continue
            key = f"{mode}:{season}"
            snapshot = db.execute(
                "SELECT ranking FROM reward_rank_snapshots WHERE key=?", (key,)
            ).fetchone()
            if snapshot:
                ranked = json.loads(snapshot["ranking"])
            else:
                if not clan:
                    ranked = [
                        dict(
                            id=r["uid"],
                            score=r["score"],
                            gradeId=json.loads(r["detail"]).get("gradeId", 0),
                            groupId=json.loads(r["detail"]).get("groupId", 0),
                        )
                        for r in db.execute(
                            "SELECT uid,score,detail FROM scores WHERE kind=? AND season=? ORDER BY score DESC,uid",
                            (source, season),
                        )
                    ]
                elif source == "familia_war_battles":
                    counts = {}
                    cutoff = (
                        "started"
                        if war_lifecycle.policy(season) is not None
                        else "finished"
                    )
                    for row in db.execute(
                        f"SELECT familia,enemy_familia,response FROM familia_war_battles WHERE season=? AND finished IS NOT NULL AND {cutoff}<?",
                        (season, period["end"]),
                    ):
                        result = json.loads(row["response"]).get("gvgResult", {})
                        counts[row["familia"]] = counts.get(
                            row["familia"], 0
                        ) + result.get("point", 0)
                        counts.setdefault(row["enemy_familia"], 0)
                    if war_lifecycle.policy(season) is not None:
                        for fid in counts:
                            counts[fid] += war_lifecycle.standing(db, season, fid)[
                                "bonus"
                            ]
                    ranked = [
                        dict(id=fid, score=score)
                        for fid, score in sorted(
                            counts.items(), key=lambda v: (-v[1], v[0])
                        )
                    ]
                elif source == "royale_scores":
                    from danmemo.storage import royale_score_history as history
                    from danmemo.game import extra_modes as extra

                    history.import_legacy(db, season, extra.catalogs()[1])
                    ranked = [
                        dict(id=fid, score=score)
                        for fid, score in history.family_totals(
                            db, season, period["end"] + 600, final=True
                        )
                    ]
                else:
                    ranked = [
                        dict(id=r["familia"], score=r["score"])
                        for r in db.execute(
                            f"SELECT familia,SUM(score) score FROM {source} WHERE season=? GROUP BY familia ORDER BY score DESC,familia",
                            (season,),
                        )
                    ]
                if clan:
                    for entry in ranked:
                        entry["members"] = [
                            r["uid"]
                            for r in db.execute(
                                "SELECT uid FROM members WHERE familia=? AND joined<?",
                                (entry["id"], period["end"]),
                            )
                        ]
                        if mode == "familia_rush":
                            raid = db.execute(
                                "SELECT state FROM familia_raid_state WHERE season=? AND familia=?",
                                (season, entry["id"]),
                            ).fetchone()
                            entry["defeats"] = (
                                json.loads(raid["state"]).get("defeats", 0)
                                if raid
                                else 0
                            )
                db.execute(
                    "INSERT INTO reward_rank_snapshots VALUES(?,?,?)",
                    (key, json.dumps(ranked), now),
                )
            entity = (
                world.uid
                if not clan
                else next(
                    (r["id"] for r in ranked if world.uid in r.get("members", [])), None
                )
            )
            if (
                clan
                and entity is None
                and ranked
                and (not any(("members" in r for r in ranked)))
            ):
                entity = (
                    member["familia"]
                    if member and member["joined"] < period["end"]
                    else None
                )
            rank = next(
                (i + 1 for i, r in enumerate(ranked) if r["id"] == entity), None
            )
            if rank is None:
                continue
            if mode == "war_game":
                from danmemo.game.arena import grade_for_score as grade_for_score

                grade = grade_for_score(
                    season,
                    next((r["score"] for r in ranked if r["id"] == entity)),
                    rank,
                )
                for row in decoded(reward):
                    if num(row, 1) == season and num(row, 2) == grade:
                        candidates.append(
                            (
                                f"grade:{key}:{num(row, 0)}",
                                f"War Game season {season}: final grade {grade}, rank {rank}",
                                pairs(row, 3, 7),
                            )
                        )
                continue
            if mode == "ultimate_war_game":
                for group in decoded(reward):
                    if (
                        num(group, 1) == 1
                        and num(group, 2) > 0
                        and (rank >= num(group, 2))
                        and (num(group, 3) < 0 or rank <= num(group, 3))
                    ):
                        items = [
                            (num(r, 2), num(r, 3))
                            for r in decoded("serialPvpRankingRewardItem")
                            if num(r, 1) == num(group, 0)
                        ]
                        candidates.append(
                            (
                                f"serial-rank:{key}:{num(group, 0)}",
                                f"Ultimate War Game season {season}: final rank {rank}",
                                items,
                            )
                        )
                continue
            if mode == "telskyura":
                for row in decoded(reward):
                    if (
                        num(row, 1) == 1
                        and num(row, 2) <= rank
                        and (not num(row, 3) or rank <= num(row, 3))
                    ):
                        candidates.append(
                            (
                                f"under-rank:{key}:{num(row, 0)}",
                                f"Telskyura season {season}: final rank {rank}",
                                pairs(row),
                            )
                        )
                continue
            for row in decoded(reward):
                if mode == "familia_war_game":
                    from danmemo.game.familia_war import league as league

                    if num(row, 14) != league(season, rank):
                        continue
                if num(row, 1) == season and num(row, 2) <= rank <= num(row, 3):
                    candidates.append(
                        (
                            f"ranking:{key}:{num(row, 0)}",
                            f"{modes.MODES[mode]['name']} season {season}: final "
                            + ("Familia " if clan else "")
                            + f"rank {rank}",
                            pairs(row),
                        )
                    )
            if mode == "record_buster":
                group = next((r.get("groupId", 0) for r in ranked if r["id"] == entity))
                if group > 0:
                    group_rank = next(
                        (
                            i + 1
                            for i, r in enumerate(
                                [r for r in ranked if r.get("groupId") == group]
                            )
                            if r["id"] == entity
                        )
                    )
                    for row in decoded("pvbGroupRankingReward"):
                        if num(row, 1) == season and num(row, 2) <= group_rank <= num(
                            row, 3
                        ):
                            candidates.append(
                                (
                                    f"group-rank:{key}:{num(row, 0)}",
                                    f"Record Buster season {season}: group {group}, final rank {group_rank}",
                                    pairs(row),
                                )
                            )
        royale_milestones = {}

        def royale_entitlement(season):
            if season in royale_milestones:
                return royale_milestones[season]
            period = tally_period("familia_royale", season, settings, selected_familia)
            if not period or now < period["start"]:
                royale_milestones[season] = (None, 0)
                return (None, 0)
            if season not in royale_milestones:
                from danmemo.storage import royale_score_history as history
                from danmemo.game import extra_modes as extra

                history.import_legacy(db, season, extra.catalogs()[1])
                snap = db.execute(
                    "SELECT ranking FROM reward_rank_snapshots WHERE key=?",
                    (f"familia_royale:{season}",),
                ).fetchone()
                earned = (
                    next(
                        (
                            r
                            for r in json.loads(snap["ranking"])
                            if world.uid in r.get("members", [])
                        ),
                        None,
                    )
                    if snap
                    else None
                )
                if (
                    earned is None
                    and (not snap)
                    and member
                    and (member["joined"] <= history.tally_cutoff(now))
                ):
                    fid = member["familia"]
                    earned = dict(
                        id=fid,
                        score=dict(history.family_totals(db, season, now)).get(fid, 0),
                    )
                previous = 0
                if (
                    earned
                    and member
                    and (member["familia"] == earned["id"])
                    and (member["joined"] > period["start"])
                ):
                    previous = dict(
                        history.family_totals(db, season, member["joined"] - 1)
                    ).get(earned["id"], 0)
                royale_milestones[season] = (earned, previous)
            return royale_milestones[season]

        for row in decoded("gveTotalFamiliaScoreReward"):
            season = num(row, 1)
            earned, previous = royale_entitlement(season)
            if earned and earned["score"] >= num(row, 2) > previous:
                candidates.append(
                    (
                        f"royale-total:{season}:{earned['id']}:{num(row, 0)}",
                        f"True Familia Royale season {season}: Familia score >= {num(row, 2)}",
                        pairs(row, 3, 7),
                    )
                )
        labels = original_item_labels()
        for row in decoded("familiaAchievement"):
            season = num(row, 19)
            if num(row, 2) != 201 or not season:
                continue
            earned, previous = royale_entitlement(season)
            if earned and earned["score"] >= num(row, 7) > previous:
                iid = labels.get(row.get("14"))
                if iid is None:
                    continue
                candidates.append(
                    (
                        f"familia-mission:{season}:{num(row, 0)}",
                        royale_mission_mail_titles()[num(row, 0)],
                        [(iid, num(row, 15))],
                    )
                )
        for row in decoded("guildRaidDefeatCountReward"):
            season = num(row, 1)
            period = tally_period("familia_rush", season, settings, selected_familia)
            if not period or now < period["end"]:
                continue
            snap = db.execute(
                "SELECT ranking FROM reward_rank_snapshots WHERE key=?",
                (f"familia_rush:{season}",),
            ).fetchone()
            earned = (
                next(
                    (
                        r
                        for r in json.loads(snap["ranking"])
                        if world.uid in r.get("members", [])
                    ),
                    None,
                )
                if snap
                else None
            )
            if earned and earned.get("defeats", 0) >= num(row, 2) > 0:
                candidates.append(
                    (
                        f"rush-count:{season}:{earned['id']}:{num(row, 0)}",
                        f"Familia Rush season {season}: defeats >= {num(row, 2)}",
                        pairs(row, 3, 11),
                    )
                )
        from danmemo.game.extra_modes import day as day

        for scored in db.execute(
            "SELECT * FROM shooting_scores WHERE uid=? AND day<?", (world.uid, day(now))
        ):
            rank = (
                1
                + db.execute(
                    "SELECT COUNT(*) FROM shooting_scores WHERE season=? AND day=? AND (score>? OR (score=? AND uid<?))",
                    (
                        scored["season"],
                        scored["day"],
                        scored["score"],
                        scored["score"],
                        world.uid,
                    ),
                ).fetchone()[0]
            )
            for row in decoded("shootingGameDailyRankingReward"):
                if (
                    num(row, 1) == scored["season"]
                    and num(row, 4) <= rank
                    and (not num(row, 5) or rank <= num(row, 5))
                ):
                    candidates.append(
                        (
                            f"shooting-daily:{scored['season']}:{scored['day']}:{num(row, 0)}",
                            f"Shooting season {scored['season']}: JST day {scored['day']}, rank {rank}",
                            pairs(row, 6, 8),
                        )
                    )
        from danmemo.game.reconstructed_reward_delivery import (
            candidates as reconstructed_candidates,
        )

        candidates.extend(reconstructed_candidates(world, db, settings, now))
    return candidates


def reconcile_mail_receipts(oc, state):
    world = getattr(oc, "ONLINE_SHARED", None)
    if world is None:
        return
    ledger = state.get("gameRewardMailReceipts", {})
    with world.connect() as db:
        for r in db.execute(
            "SELECT * FROM union_defeat_rewards WHERE uid=? AND claimed=0", (world.uid,)
        ).fetchall():
            if f"union-defeat:{r['season']}:{r['side']}:{r['reward']}" in ledger:
                db.execute(
                    "UPDATE union_defeat_rewards SET claimed=1 WHERE season=? AND side=? AND uid=? AND reward=?",
                    (r["season"], r["side"], world.uid, r["reward"]),
                )
        for r in db.execute(
            "SELECT * FROM familia_raid_rewards WHERE uid=? AND claimed=0", (world.uid,)
        ).fetchall():
            if f"rush-defeat:{r['season']}:{r['familia']}:{r['defeat']}" in ledger:
                db.execute(
                    "UPDATE familia_raid_rewards SET claimed=1 WHERE season=? AND familia=? AND uid=? AND defeat=?",
                    (r["season"], r["familia"], world.uid, r["defeat"]),
                )


def run(oc, now=None, *, return_state=False):
    from danmemo.game.daily_windows import (
        mission_day as mission_day,
        mission_day_start as mission_day_start,
        mission_month as mission_month,
        reset_mission_day as reset_mission_day,
        reset_mission_month as reset_mission_month,
    )

    now = int(time.time()) if now is None else now
    server = "online" if hasattr(oc, "ONLINE_SHARED") else "offline"
    rules = active_rules(server, now)
    tag_periods = periods(server)
    candidates = shared_candidates(oc, now)
    evidence = {}
    world = getattr(oc, "ONLINE_SHARED", None)
    if world:
        with world.connect() as db:
            member = db.execute(
                "SELECT familia FROM members WHERE uid=?", (world.uid,)
            ).fetchone()
            if member:
                from danmemo.storage import royale_score_history as history
                from danmemo.game import extra_modes as extra

                for r in decoded("gveInformation"):
                    season = num(r, 0)
                    history.import_legacy(db, season, extra.catalogs()[1])
                    score = dict(history.family_totals(db, season, now)).get(
                        member["familia"], 0
                    )
                    evidence[1801, r.get("1"), False] = score
            war_counts = {
                r["season"]: (r["total"], r["daily"])
                for r in db.execute(
                    "SELECT season,COUNT(*) AS total,SUM(started>=?) AS daily FROM familia_war_battles WHERE actor=? AND finished IS NOT NULL AND started>=0 GROUP BY season",
                    (mission_day_start(now), world.uid),
                )
            }
            for r in decoded("guildVsGuildInformation"):
                for daily in (False, True):
                    count = war_counts.get(num(r, 0), (0, 0))[int(daily)]
                    evidence[2706, r.get("1"), daily] = count
            from danmemo.game.union_raid import stages as union_stages

            union_counts = {
                False: {3401: 0, 3402: 0, 3403: 0, 3404: 0},
                True: {3401: 0, 3402: 0, 3403: 0, 3404: 0},
            }
            for receipt in db.execute(
                "SELECT signature,stamp FROM union_battle_receipts WHERE uid=?",
                (world.uid,),
            ):
                stage = union_stages().get(
                    json.loads(receipt["signature"]).get("unionRaidStageId")
                )
                condition = {0: 3401, 1: 3402}.get(stage["kind"]) if stage else None
                if condition:
                    union_counts[False][condition] += 1
                    if mission_day(receipt["stamp"]) == mission_day(now):
                        union_counts[True][condition] += 1
            for receipt in db.execute(
                "SELECT signature,response FROM union_mutation_receipts WHERE uid=?",
                (world.uid,),
            ):
                req = json.loads(receipt["signature"])
                response = json.loads(receipt["response"])
                condition = {
                    "union_raid/chat/send": 3403,
                    "union_raid/skill/growth": 3404,
                }.get(req["action"])
                if condition:
                    amount = (
                        req["request"].get("medalAmount", 1) if condition == 3404 else 1
                    )
                    union_counts[False][condition] += amount
                    if mission_day(response.get("acceptedAt", 0)) == mission_day(now):
                        union_counts[True][condition] += amount
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        if not state["tables"].get("UserInfo"):
            return (False, state) if return_state else False
        rows = state["tables"].setdefault("UserAchievement", [])
        from danmemo.game.mission_history import (
            snapshot as snapshot_missions,
            select_month as select_month,
        )

        mission_history_changed = select_month(state, modes.config(server), now)
        mission_history_changed = snapshot_missions(state) or mission_history_changed
        from danmemo.game.mission_sync import (
            observed_progress as observed_progress,
            recover_record_buster_counters as recover_record_buster_counters,
        )

        recovered_counters = recover_record_buster_counters(state)
        from danmemo.game.flow_repair import (
            reconcile_rookie_stages as reconcile_rookie_stages,
        )

        rookie_changed = reconcile_rookie_stages(oc, state)
        observed_by_id = observed_progress(state, rules, now)
        if world:
            for rule in rules:
                if num(rule, 2) == 9 and num(rule, 4) in union_counts[False]:
                    observed_by_id[num(rule, 0)] = union_counts[num(rule, 20) == 1][
                        num(rule, 4)
                    ]
        war_id = modes.selected_season(
            "war_game", modes.config(server)["modes"].get("war_game")
        )
        war = state.get("arena", {}).get("pvp", {}).get(str(war_id), {})
        from danmemo.game.arena import grades as grades

        grade_rows = {r.text(2): r for r in grades(war_id)}
        highest = war.get("highestGradeId", 0)
        from danmemo.game.arena import pvp_record as pvp_record

        if war:
            highest = max(highest, pvp_record(oc, state, war_id)["gradeId"])
        for rule in rules:
            condition = num(rule, 4)
            if condition in (901, 902):
                daily = num(rule, 20) == 1
                observed = (
                    war.get("dailyBattles", {})
                    .get(str(mission_day(now)), {})
                    .get("battles" if condition == 901 else "wins", 0)
                    if daily
                    else war.get("battleCount" if condition == 901 else "winCount", 0)
                )
                evidence[condition, rule.get("10"), daily] = observed
            elif condition == 903 and rule.get("10") in grade_rows:
                evidence[903, rule.get("10"), num(rule, 20) == 1] = int(
                    highest >= grade_rows[rule["10"]].number(0, 8)
                )
        by_id = {r["achievementId"]: r for r in rows}
        from danmemo.game.casino_lotto import reconcile as reconcile_lotto

        lotto_changed = reconcile_lotto(oc, state, now)
        from danmemo.game.arena_record_state import refresh as refresh_arena_records

        arena_changed = refresh_arena_records(oc, state, now)
        from danmemo.game.familia_quest_repair import (
            reconcile as reconcile_familia_quests,
        )

        familia_changed = reconcile_familia_quests(oc, state)
        changed = (
            recovered_counters
            or rookie_changed
            or lotto_changed
            or arena_changed
            or familia_changed
            or mission_history_changed
        )
        delivered = []
        from danmemo.game.event_missions import rules as mission_rules

        original_rules = {num(r, 0): r for r in mission_rules()}
        categories = {
            1: "Daily",
            2: "Total",
            3: "Time-limited",
            5: "Rookie",
            8: "Monthly",
            9: "Union",
        }
        for key, receipt in state.get("gameRewardMailReceipts", {}).items():
            rule = original_rules.get(receipt.get("achievementId"))
            if rule and (not receipt.get("voidReason")):
                name = str(rule.get("7") or rule.get("1") or "").replace("\n", " ")
                source = f"Previously delivered {categories.get(num(rule, 2), 'mission')} reward: {name}"
                changed = label_existing_mail(state, key, source) or changed
            elif not receipt.get("voidReason") and receipt.get("source"):
                changed = label_existing_mail(state, key, receipt["source"]) or changed

        def defer(key, reason):
            nonlocal changed
            ledger = state.setdefault("gameRewardDeferred", {})
            if ledger.get(key, {}).get("reason") != reason:
                ledger[key] = dict(reason=reason, observedAt=now)
                changed = True

        snapshots_before = json.dumps(
            state.get("offlineRewardRankSnapshots", {}), sort_keys=True
        )
        for key, title, rewards in candidates + offline_candidates(oc, state, now):
            try:
                changed = label_existing_mail(state, key, title) or changed
                if mail(oc, state, key, title, rewards, now):
                    changed = True
                    delivered.append(key)
            except ValueError as error:
                defer(key, str(error))
                continue
        changed = changed or snapshots_before != json.dumps(
            state.get("offlineRewardRankSnapshots", {}), sort_keys=True
        )
        rows = state["tables"].setdefault("UserAchievement", [])
        by_id = {r["achievementId"]: r for r in rows}
        for rule in rules:
            aid, category, condition, target, reset_kind = (
                num(rule, k) for k in (0, 2, 4, 9, 20)
            )
            row = by_id.get(aid)
            subscription_locked = subscription_required(rule)
            if subscription_locked:
                receipts = [
                    (k, v)
                    for k, v in state.get("gameRewardMailReceipts", {}).items()
                    if v.get("achievementId") == aid and (not v.get("voidReason"))
                ]
                if receipts:
                    for key, receipt in receipts:
                        ids = set(receipt["giftIds"])
                        gifts_rows = state["tables"].get("UserGift", [])
                        removable = {
                            g["id"]
                            for g in gifts_rows
                            if g["id"] in ids and g.get("state") == 0
                        }
                        state["tables"]["UserGift"] = [
                            g for g in gifts_rows if g["id"] not in removable
                        ]
                        for gid in removable:
                            state.get("adminGiftItems", {}).pop(str(gid), None)
                        receipt["voidReason"] = (
                            "Paid subscription entitlement unavailable; collected inventory preserved"
                        )
                        state.get("achievementClaims", {}).pop(
                            f"{aid}:{receipt.get('completedAt', 0)}", None
                        )
                        state.get("gameRewardAchievementCycles", {}).pop(str(aid), None)
                    if row:
                        row["state"] = 1
                    changed = True
            if row is None:
                prior = by_id.get(original_id(rule.get("8"))) if rule.get("8") else None
                locked = subscription_locked or (
                    bool(rule.get("8")) and (prior is None or prior.get("state") != 4)
                )
                row = dict(
                    userId=oc.LOCAL_USER_ID,
                    achievementId=aid,
                    state=1 if locked else 2,
                    readState=1,
                    completedAt=0,
                    progressParam=0,
                    lastResetAt=now,
                    signature=0,
                )
                rows.append(row)
                by_id[aid] = row
                changed = True
            elif (
                row.get("state") == 1
                and (not subscription_locked)
                and (
                    not rule.get("8")
                    or by_id.get(original_id(rule.get("8")), {}).get("state") == 4
                )
            ):
                row["state"] = 2
                changed = True
            from danmemo.game.daily_windows import is_login_bonus as is_login_bonus

            if reset_kind == 1:
                if is_login_bonus(rule):
                    if (
                        not num(rule, 14) - 86400
                        <= row.get("lastResetAt", 0)
                        < num(rule, 14)
                    ):
                        row.update(
                            state=2,
                            completedAt=0,
                            progressParam=0,
                            lastResetAt=now,
                            readState=1,
                        )
                        changed = True
                elif reset_mission_day(row, now):
                    changed = True
            if (category == 8 or reset_kind == 3) and reset_mission_month(
                row, now, subscription_locked
            ):
                changed = True
            if subscription_locked:
                if row.get("state") not in (1, 4):
                    row["state"] = 1
                    changed = True
                continue
            if category in (1, 2, 3, 8, 9) and row.get("state") == 4:
                for receipt in state.get("gameRewardMailReceipts", {}).values():
                    completed = receipt.get("completedAt", 0)
                    marker = f"{aid}:{completed}"
                    if (
                        receipt.get("achievementId") == aid
                        and (not receipt.get("voidReason"))
                        and (completed == row.get("completedAt"))
                        and (completed > 0)
                        and (
                            marker not in state.get("timeLimitedVisibilityRestored", {})
                        )
                    ):
                        state.setdefault("timeLimitedVisibilityRestored", {})[
                            marker
                        ] = True
                        state.setdefault("achievementClaims", {})[marker] = True
                        row["state"] = 3
                        changed = True
                        break
            observed = evidence.get(
                (condition, rule.get("10"), reset_kind == 1), observed_by_id.get(aid)
            )
            if observed is not None and row.get("state") != 4:
                from danmemo.game.achievement_progress import project as project

                observed, overflow_changed = project(state, row, observed)
                changed = changed or overflow_changed
                if row.get("progressParam") != observed:
                    row["progressParam"] = observed
                    changed = True
                ready = observed >= target > 0
                if ready and row.get("state") not in (1, 3):
                    row.update(state=3, completedAt=row.get("completedAt") or now)
                    changed = True
                elif not ready and row.get("state") == 3:
                    row.update(state=2, completedAt=0)
                    changed = True
            if condition == 3105 and row.get("state") != 4:
                month = mission_month(now)
                context = state.get("activeMonthlyContext", month)
                observed = (
                    state.get("monthlyLoginContexts", {})
                    .get(context, {})
                    .get(
                        "days",
                        max(
                            (
                                v.get("days", 0)
                                for k, v in state.get("dailyLogin", {})
                                .get("periodCounts", {})
                                .items()
                                if k.endswith(":" + month)
                            ),
                            default=0,
                        )
                        if context == month
                        else 0,
                    )
                )
                if observed > row.get("progressParam", 0):
                    row["progressParam"] = observed
                    changed = True
                if observed >= target and row.get("state") not in (1, 3):
                    row.update(state=3, completedAt=row.get("completedAt") or now)
                    changed = True
            if row.get("state") != 3 or row.get("progressParam", 0) < target:
                continue
            if category in (1, 2, 3, 8, 9):
                continue
            table_reward = oc.achievement_rewards().get(str(aid))
            if not table_reward or table_reward[0] in (
                "stamina",
                "UserPC",
                "UserMemoria",
            ):
                defer(
                    "achievement:" + str(aid),
                    "Original reward inventory contract unavailable"
                    if not table_reward
                    else "Existing client reward flow retained",
                )
                continue
            completed = row.get("completedAt") or now
            period = tag_periods.get(rule.get("10"))
            cycle = (
                str(mission_day(now))
                if reset_kind == 1
                else mission_month(now)
                if category == 8 or reset_kind == 3
                else str(rule.get("10"))
                if period
                else "once"
            )
            key = f"achievement:{aid}:{cycle}"
            old = state.get("gameRewardMailReceipts", {}).get(key)
            if old:
                row.update(state=4, completedAt=old.get("completedAt", completed))
                changed = True
                continue
            try:
                sent = mail(
                    oc,
                    state,
                    key,
                    "Mission reward",
                    [(table_reward[2], table_reward[3])],
                    now,
                )
            except ValueError as error:
                defer(key, str(error))
                continue
            if sent:
                rows = state["tables"]["UserAchievement"]
                by_id = {r["achievementId"]: r for r in rows}
                row = by_id[aid]
                row.update(state=4, completedAt=completed)
                state.setdefault("achievementClaims", {})[f"{aid}:{completed}"] = True
                state["gameRewardMailReceipts"][key].update(
                    achievementId=aid, completedAt=completed
                )
                state.setdefault("gameRewardAchievementCycles", {})[str(aid)] = cycle
                changed = True
                delivered.append(key)
        if changed:
            if state.get("missionCatalogVersion", 0) < 2:
                state["missionCatalogVersion"] = 2
            if not state.get("rewardDeliveryMigration"):
                state["rewardDeliveryMigration"] = True
            state["tokens"]["UserAchievement"] = hashlib.md5(
                oc.msgpack_value(rows)
            ).hexdigest()
            gifts.stamp(oc, state)
            oc.write_progress(state)
        reconcile_mail_receipts(oc, state)
        if delivered:
            from danmemo.services import journal as test_journal

            test_journal.emit(
                "game-reward-mail",
                "reward/deliver",
                uid=oc.LOCAL_USER_ID,
                response={
                    "code": 0,
                    "rewardCount": len(delivered),
                    "UserGift": state["tables"].get("UserGift", []),
                },
            )
        return (bool(changed), state) if return_state else bool(changed)
