"""Recovered message-master templates; missing contracts retain existing text."""

from danmemo import paths as _paths
import functools
import json
import re


@functools.cache
def templates():
    return json.loads(
        (_paths.METADATA / "original_reward_messages.json").read_text("utf-8")
    )["messages"]


def text(key, **values):
    return templates()[key]["text"].format(**values)


@functools.cache
def information(name):
    from danmemo.game.reward_delivery import decoded as decoded, num as num

    rows = decoded(name)
    if not rows and name in ("pvpGrade", "guildVsGuildLeague"):
        from danmemo.master import reader as master

        fields = ("2", "3") if name == "pvpGrade" else ("2",)
        return {
            r.number(0, 8): {"0": r.number(0, 8), **{f: r.text(int(f)) for f in fields}}
            for r in master.rows(master.master_bytes(name))
        }
    return {num(r, 0): r for r in rows}


def mail_title(key, current):
    parts = key.split(":")
    try:
        if parts[0] == "achievement":
            rule = information("achievement").get(int(parts[1]))
            if rule and rule.get("7"):
                return rule["7"] + " Reward"
        if parts[0] == "lotto":
            prize = re.search("\\bPrize (\\d+)\\b", current)
            if prize:
                return f"Lotto Orario: Prize {prize[1]} Reward"
        if parts[0] == "offline" and len(parts) > 3:
            mode = parts[1]
            if mode == "war_game":
                return mail_title(":".join(["grade", *parts[1:]]), current)
            if mode == "record_buster":
                rank = re.search("\\b(totalRank|groupRank) (\\d+)\\b", current)
                if rank:
                    return mail_title(
                        ":".join(
                            [
                                "group-rank" if rank[1] == "groupRank" else "ranking",
                                *parts[1:],
                            ]
                        ),
                        "final rank " + rank[2],
                    )
            if mode == "ultimate_war_game":
                return mail_title(":".join(["serial-rank", *parts[1:]]), current)
        rank = re.search("\\brank\\s*#?\\s*(\\d+)\\b", current, re.IGNORECASE)
        if parts[0] in ("ranking", "group-rank", "under-rank", "serial-rank", "grade"):
            mode = parts[1]
            season = int(parts[2])
            specs = {
                "war_game": ("pvpInformation", "6"),
                "record_buster": ("pvbInformation", "4"),
                "ultimate_war_game": ("serialPvpInformation", "2"),
                "telskyura": ("underPvpInformation", "1"),
                "familia_rush": ("guildRaidInformation", "3"),
                "union_raid": ("unionRaidInformation", "3"),
                "familia_war_game": ("guildVsGuildInformation", "2"),
            }
            if mode not in specs:
                return current
            table, field = specs[mode]
            event = information(table)[season][field]
            if mode == "war_game" and parts[0] == "grade":
                grade = re.search("\\bgrade (\\d+)", current)
                if not grade:
                    return current
                return text(
                    "pvp_final_reward_txt",
                    eventName=event,
                    gradeName=information("pvpGrade")[int(grade[1])]["3"],
                )
            if not rank:
                return current
            if mode == "familia_war_game":
                prize = information("guildVsGuildRankingReward")[int(parts[-1])]
                league = information("guildVsGuildLeague").get(prize.get("14"))
                if league:
                    return text(
                        "guild_vd_guild_league_ranking_reward_txt",
                        eventName=event,
                        leagueName=league["2"],
                        rank=rank[1],
                    )
            names = {
                "record_buster": "pvb_final_group_reward_txt"
                if parts[0] == "group-rank"
                else "pvb_final_reward_txt",
                "familia_rush": "guild_raid_familia_ranking_reward_txt",
                "union_raid": "union_raid_familia_ranking_reward_txt",
                "familia_war_game": "guild_vd_guild_ranking_reward_txt",
                "telskyura": "stationaly_pvp_final_reward_txt",
                "ultimate_war_game": "stationaly_pvp_final_reward_txt",
            }
            if mode in names:
                return text(names[mode], eventName=event, rank=rank[1])
        elif parts[0] == "fwg-versus":
            event = information("guildVsGuildInformation")[int(parts[1])]["2"]
            from danmemo.game.reward_delivery import num as num

            rule = information("guildVsGuildFamiliaVersusReward")[int(parts[-1])]
            return text(
                "guild_vd_guild_familia_vs_"
                + ("win" if num(rule, 2) == 0 else "lose")
                + "_reward_txt",
                eventName=event,
            )
        elif parts[0] == "rush-count":
            event = information("guildRaidInformation")[int(parts[1])]["3"]
            rule = information("guildRaidDefeatCountReward")[int(parts[-1])]
            return text(
                "guild_raid_defeat_count_reward_txt",
                eventName=event,
                defeatCount=rule["2"],
            )
        elif parts[0] == "union-defeat":
            event = information("unionRaidInformation")[int(parts[1])]["3"]
            rule = information("unionRaidDefeatCountReward")[int(parts[-1])]
            return text(
                "union_raid_defeat_count_reward_txt",
                eventName=event,
                defeatCount=rule["2"],
            )
        elif parts[0] in ("shooting-daily", "offline-shooting") and rank:
            return templates()["shooting_daily_ranking_score_reward_txt"][
                "text"
            ].format(rank[1])
    except (KeyError, ValueError, IndexError):
        pass
    return current
