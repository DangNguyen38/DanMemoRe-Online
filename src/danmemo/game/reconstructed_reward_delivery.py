"""Explicit private-server settlement policies using original prize masters.

Prize identities/amounts and available message templates are original data.
Snapshot timing, eligibility and uncovered ranking rules are reconstructions;
see re_analysis/reconstructed_reward_policies_2026_10_23.md.
"""

import functools
import json
import secrets
import struct
from collections import defaultdict
from danmemo.config import modes as modes
from danmemo.game.daily_windows import (
    mission_day as mission_day,
    mission_day_start as mission_day_start,
)
from danmemo.content.reward_text import text as text


@functools.cache
def rows(name):
    from danmemo.game.reward_delivery import decoded as decoded

    return tuple(decoded(name))


def number(row, field):
    value = row.get(str(field), 0)
    return value if type(value) is int else 0


def items(row, start=4, end=14, multiplier=1):
    return [
        (number(row, i), number(row, i + 1) * multiplier)
        for i in range(start, end, 2)
        if number(row, i) > 0 and number(row, i + 1) > 0
    ]


def freeze(db, key, now, builder):
    key = "reconstructed-v1:" + key
    saved = db.execute(
        "SELECT ranking FROM reward_rank_snapshots WHERE key=?", (key,)
    ).fetchone()
    if saved:
        return json.loads(saved["ranking"])
    encoded = json.dumps(builder())
    db.execute("INSERT INTO reward_rank_snapshots VALUES(?,?,?)", (key, encoded, now))
    return json.loads(encoded)


def position(entries, identity):
    return next(
        (index + 1 for index, row in enumerate(entries) if row["id"] == identity), None
    )


def rank_prizes(name, group, rank):
    if rank is None:
        return ()
    return (
        r
        for r in rows(name)
        if number(r, 1) == group
        and number(r, 2) <= rank
        and (not number(r, 3) or rank <= number(r, 3))
    )


def seventh(world, db, now):
    result = []
    infos = {number(r, 0): r for r in rows("elementEventInformation")}
    for selection in modes.selected_element_events().values():
        period = selection["period"]
        sid = selection["season"]
        if now < period["end"]:
            continue
        info = infos.get(sid)
        if not info:
            continue
        for zone in rows("elementEventStage"):
            zid = number(zone, 0)
            if number(zone, 1) != sid:
                continue
            ranked = freeze(
                db,
                f"element:{zid}",
                now,
                lambda: [
                    dict(id=r["uid"], score=r["score"])
                    for r in db.execute(
                        "SELECT uid,score FROM scores WHERE kind=? AND season=? AND score>0 ORDER BY score DESC,uid",
                        ("element", zid),
                    )
                ],
            )
            rank = position(ranked, world.uid)
            for prize in rank_prizes(
                "elementEventScoreRankingReward", number(zone, 4), rank
            ):
                title = text(
                    "element_event_score_ranking_reward_txt",
                    eventName=info["2"],
                    title=zone["3"],
                    rank=rank,
                )
                result.append(
                    (f"element-rank:{zid}:{number(prize, 0)}", title, items(prize))
                )
    return result


def legacy_royale(world, db, settings, now):
    from danmemo.game.reward_delivery import tally_period as tally_period

    result = []
    for info in rows("gveInformation"):
        sid = number(info, 0)
        tables = [
            name
            for name in ("gveHighScoreReward", "gveIndividualRankingReward")
            if any((number(r, 1) == sid for r in rows(name)))
        ]
        if not tables:
            continue
        period = tally_period("familia_royale", sid, settings)
        if not period or now < period["end"] + 600:
            continue
        for table in tables:

            def standings():
                if table == "gveHighScoreReward":
                    return [
                        dict(id=r["uid"], score=r["score"])
                        for r in db.execute(
                            "SELECT uid,MAX(score) score FROM royale_scores WHERE season=? GROUP BY uid HAVING MAX(score)>0 ORDER BY score DESC,uid",
                            (sid,),
                        )
                    ]
                from danmemo.storage.royale_score_history import (
                    player_totals as player_totals,
                )

                return [
                    dict(id=uid, score=score)
                    for uid, score in player_totals(db, sid, period["end"] + 600)
                    if score > 0
                ]

            ranked = freeze(db, f"{table}:{sid}", now, standings)
            rank = position(ranked, world.uid)
            for prize in rank_prizes(table, sid, rank):
                label = "High Score" if table == "gveHighScoreReward" else "Total Score"
                result.append(
                    (
                        f"legacy-royale:{sid}:{table}:{number(prize, 0)}",
                        f"{info['3']}: {label} Rank #{rank} Reward",
                        items(prize),
                    )
                )
    return result


def master_points(db, sid, start, end):
    totals = defaultdict(int)
    participants = defaultdict(set)
    opponents = {}
    for row in db.execute(
        "SELECT actor,familia,enemy_familia,response FROM familia_war_battles WHERE season=? AND started>=? AND started<? AND finished IS NOT NULL AND finished<=?",
        (sid, start, end, end + 600),
    ):
        fid = row["familia"]
        other = row["enemy_familia"]
        result = json.loads(row["response"]).get("gvgResult", {})
        totals[fid] += int(result.get("point", 0))
        totals.setdefault(other, 0)
        participants[fid].add(row["actor"])
        opponents.setdefault(fid, other)
        opponents.setdefault(other, fid)
    return (totals, participants, opponents)


def master(world, db, settings, now):
    period = settings.get("master_familia_war_game")
    if not period:
        return []
    sid = modes.selected_season("master_familia_war_game", period)
    tournament = next(
        (r for r in rows("guildVsGuildTournament") if number(r, 1) == sid), None
    )
    info = next(
        (r for r in rows("guildVsGuildInformation") if number(r, 0) == sid), None
    )
    if not tournament or not info:
        return []
    tid = number(tournament, 0)
    event = info["2"]
    result = []
    for choice in db.execute(
        "SELECT * FROM familia_war_vote_days WHERE season=? AND uid=? AND support>0",
        (sid, world.uid),
    ):
        end = min((choice["day"] + 1) * 86400 - 32400, period["end"])
        if now < end + 600:
            continue

        def outcome():
            totals, _, _ = master_points(
                db, sid, max(choice["day"] * 86400 - 32400, period["start"]), end
            )
            a = totals.get(choice["familia"], 0)
            b = totals.get(choice["enemy"], 0)
            return dict(won=a > b, draw=a == b)

        settled = freeze(
            db,
            f"master-support:{sid}:{choice['day']}:{choice['familia']}",
            now,
            outcome,
        )
        for prize in rows("guildVsGuildSupporterReward"):
            if number(prize, 1) != tid or number(prize, 2) != int(settled["won"]):
                continue
            title = text(
                "guild_vs_guild_support_"
                + ("ok" if settled["won"] else "ng")
                + "_reward_txt",
                eventName=event,
            )
            result.append(
                (
                    f"master-support:{sid}:{choice['day']}:{world.uid}:{number(prize, 0)}",
                    title,
                    items(prize, 3, 13),
                )
            )
    if now < period["end"] + 600:
        return result

    def final():
        points, played, _ = master_points(db, sid, period["start"], period["end"])
        ranked = []
        for fid, score in sorted(points.items(), key=lambda v: (-v[1], v[0])):
            members = {
                r["uid"]
                for r in db.execute(
                    "SELECT uid FROM members WHERE familia=? AND joined<?",
                    (fid, period["end"]),
                )
            }
            members.update(played[fid])
            ranked.append(dict(id=fid, score=score, members=sorted(members)))
        return ranked

    ranked = freeze(db, f"master-final:{sid}", now, final)
    own = next((r for r in ranked if world.uid in r["members"]), None)
    rank = position(ranked, own["id"]) if own else None
    for prize in rank_prizes("guildVsGuildTournamentReward", tid, rank):
        result.append(
            (
                f"master-rank:{sid}:{number(prize, 0)}",
                text(
                    "guild_vs_guild_tournament_ranking_reward_txt",
                    eventName=event,
                    rank=rank,
                ),
                items(prize),
            )
        )
    winner = (
        ranked[0]["id"]
        if ranked and (len(ranked) == 1 or ranked[0]["score"] > ranked[1]["score"])
        else None
    )
    if winner:
        votes = freeze(
            db,
            f"master-predictions:{sid}",
            now,
            lambda: [
                dict(r)
                for r in db.execute(
                    "SELECT uid,familia,votes FROM familia_war_votes WHERE season=? AND votes>0",
                    (sid,),
                )
            ],
        )
        count = sum(
            (
                r["votes"]
                for r in votes
                if r["uid"] == world.uid and r["familia"] == winner
            )
        )
        if count > 0:
            for prize in rows("guildVsGuildVoteReward"):
                if number(prize, 1) == tid:
                    result.append(
                        (
                            f"master-prediction:{sid}:{number(prize, 0)}",
                            text("guild_vs_guild_vote_reward_txt", eventName=event),
                            items(prize, 3, 13, count),
                        )
                    )
    return result


def candidates(world, db, settings, now):
    return (
        seventh(world, db, now)
        + legacy_royale(world, db, settings, now)
        + master(world, db, settings, now)
        + union(world, db, settings, now)
    )


def union_facts(db):
    """Bounded import of accepted battle logs; no account-file scans."""
    stages = {number(r, 0): r for r in rows("unionRaidStage")}
    watermark = db.execute(
        "SELECT last_id FROM union_reward_watermark WHERE id=1"
    ).fetchone()
    last = watermark["last_id"] if watermark else 0
    logs = db.execute(
        "SELECT * FROM union_battle_logs WHERE id>? ORDER BY id LIMIT 1000", (last,)
    ).fetchall()
    running = {}
    for log in logs:
        entry = json.loads(log["entry"])
        stage = stages.get(entry.get("params", {}).get("p2"))
        if stage is not None:
            damage = entry["params"]["p1"]
            stamp = entry["attackedAt"]
            sid = number(stage, 0)
            key = (log["season"], log["side"], sid)
            if key not in running:
                running[key] = db.execute(
                    "SELECT COALESCE(SUM(damage),0) FROM union_reward_facts WHERE season=? AND side=? AND stage=?",
                    key,
                ).fetchone()[0]
            prior = running[key]
            running[key] += damage
            hp = sum((number(stage, i) for i in range(9, 14)))
            kills = (prior + damage) // hp - prior // hp if hp else 0
            rate = struct.unpack("<f", struct.pack("<I", number(stage, 15)))[0]
            db.execute(
                "INSERT INTO union_reward_facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    log["id"],
                    log["season"],
                    entry["userId"],
                    log["side"],
                    stamp,
                    mission_day(stamp),
                    sid,
                    number(stage, 2),
                    number(stage, 14),
                    damage,
                    int(damage * rate),
                    kills,
                ),
            )
        last = log["id"]
    db.execute("INSERT OR REPLACE INTO union_reward_watermark VALUES(1,?)", (last,))
    return not db.execute(
        "SELECT 1 FROM union_battle_logs WHERE id>? LIMIT 1", (last,)
    ).fetchone()


def union_snapshot(db, sid, start, end):
    """Freeze battle-derived totals and eligible recipients together."""
    sides = {}
    for row in db.execute(
        "SELECT side,SUM(medals) medals,SUM(kills) kills FROM union_reward_facts WHERE season=? AND stamp>=? AND stamp<? GROUP BY side",
        (sid, start, end),
    ):
        sides[row["side"]] = dict(medals=row["medals"], kills=row["kills"], members=[])
    for row in db.execute(
        "SELECT DISTINCT side,uid FROM union_reward_facts WHERE season=? AND stamp<? UNION SELECT u.side,m.uid FROM union_membership u JOIN members m ON m.familia=u.familia WHERE u.season=? AND m.joined<?",
        (sid, end, sid, end),
    ):
        sides.setdefault(row["side"], dict(medals=0, kills=0, members=[]))[
            "members"
        ].append(row["uid"])
    return [dict(id=side, **value) for side, value in sorted(sides.items())]


def union(world, db, settings, now):
    period = settings.get("union_raid")
    if not period:
        return []
    if not union_facts(db):
        return []
    target = modes.selected_season("union_raid", period)
    info = next(
        (
            r
            for r in rows("unionRaidInformation")
            if number(r, 0) == target or number(r, 2) == target
        ),
        None,
    )
    if info is None:
        return []
    sid = number(info, 0)
    event = info["3"]
    result = []
    days = db.execute(
        "SELECT DISTINCT day FROM union_reward_facts WHERE season=? AND stamp>=? AND stamp<? ORDER BY day",
        (sid, period["start"], min(now, period["end"])),
    ).fetchall()
    for row in days:
        day = row["day"]
        start = max(day * 86400 - 32400, period["start"])
        end = min((day + 1) * 86400 - 32400, period["end"])
        if now < end + 600:
            continue
        snapshot = freeze(
            db,
            f"union-day:{sid}:{day}",
            now,
            lambda: union_snapshot(db, sid, start, end),
        )
        own = next((r for r in snapshot if world.uid in r["members"]), None)
        if own is None:
            continue
        for metric, table, field in [
            ("kills", "unionRaidDailyBossReward", 25),
            ("medals", "unionRaidDailyMedalReward", 24),
        ]:
            maximum = max((r[metric] for r in snapshot), default=0)
            if (
                own[metric] <= 0
                or own[metric] != maximum
                or sum((r[metric] == maximum for r in snapshot)) != 1
            ):
                continue
            prize = [
                (number(r, 2), number(r, 3))
                for r in rows(table)
                if number(r, 1) == number(info, field)
            ]
            if prize:
                result.append(
                    (
                        f"union-midway:{sid}:{day}:{metric}",
                        text("union_raid_intermediate_reward_txt", eventName=event),
                        prize,
                    )
                )
    if now >= period["end"] + 600:
        snapshot = freeze(
            db,
            f"union-final:{sid}",
            now,
            lambda: union_snapshot(db, sid, period["start"], period["end"]),
        )
        own = next((r for r in snapshot if world.uid in r["members"]), None)
        if own and any((r["kills"] > 0 or r["medals"] > 0 for r in snapshot)):
            best = max(((r["kills"], r["medals"]) for r in snapshot))
            won = (own["kills"], own["medals"]) == best and sum(
                ((r["kills"], r["medals"]) == best for r in snapshot)
            ) == 1
            group = number(info, 22 if won else 23)
            prize = [
                (number(r, 2), number(r, 3))
                for r in rows("unionRaidTotalReward")
                if number(r, 1) == group
            ]
            if prize:
                result.append(
                    (
                        f"union-final:{sid}",
                        text(
                            "union_raid_final_"
                            + ("win" if won else "lose")
                            + "_reward_txt",
                            eventName=event,
                        ),
                        prize,
                    )
                )
    for battle in db.execute(
        "SELECT * FROM union_reward_facts WHERE season=? AND uid=? AND kind=2 AND stamp>=? AND stamp<? ORDER BY id",
        (sid, world.uid, period["start"], min(now + 1, period["end"])),
    ):

        def draw():
            count = db.execute(
                "SELECT COUNT(*) FROM union_reward_facts WHERE season=? AND uid=? AND kind=2 AND id<=?",
                (sid, world.uid, battle["id"]),
            ).fetchone()[0]
            group = battle["reward_group"]
            pulls = sum(
                (
                    max(
                        (
                            number(r, 3)
                            for r in rows(table)
                            if number(r, 1) == group and number(r, 2) <= value
                        ),
                        default=0,
                    )
                    for table, value in [
                        ("unionRaidSPBossRewardDrawCount", count),
                        ("unionRaidSPBossRewardDrawDamage", battle["damage"]),
                    ]
                )
            )
            pool = [
                r
                for r in rows("unionRaidSPBossRewardItem")
                if number(r, 1) == group and number(r, 4) > 0
            ]
            total = sum((number(r, 4) for r in pool))
            awards = defaultdict(int)
            for _ in range(pulls if total else 0):
                pick = secrets.randbelow(total)
                for r in pool:
                    pick -= number(r, 4)
                    if pick < 0:
                        awards[number(r, 2)] += number(r, 3)
                        break
            return sorted(awards.items())

        prizes = freeze(db, f"union-special:{sid}:{battle['id']}", now, draw)
        if prizes:
            result.append(
                (
                    f"union-special:{sid}:{battle['id']}",
                    f"{event}: Special Enemy Reward",
                    prizes,
                )
            )
    return result
