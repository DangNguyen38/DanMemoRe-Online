"""Original dated Time-limited missions for operator historical presets.

Keeps archived reward/condition IDs and their own windows. No replacement
missions are invented when the selected archive has no surviving definitions.
"""

import functools
from collections import defaultdict
from danmemo.game import reward_delivery as delivery
from danmemo.config import modes as modes


@functools.cache
def owners():
    num = delivery.num
    result = defaultdict(set)
    parents = {num(r, 0): r for r in delivery.decoded("adventure")}
    episodes = {num(r, 0): r for r in delivery.decoded("adventureEpisode")}
    for eid, ep in episodes.items():
        for tag in (ep.get("1"), parents.get(num(ep, 2), {}).get("1")):
            if tag:
                result[tag].add("event:" + str(eid))
    for stage in delivery.decoded("adventureStage"):
        if stage.get("7"):
            result[stage["7"]].add("event:" + str(num(stage, 1)))
    starts = defaultdict(set)
    for key, spec in modes.MODES.items():
        if spec["master"] == "adventure":
            continue
        for row in delivery.decoded(spec["master"]):
            sid = num(row, 0)
            if spec["master"] == "guildVsGuildInformation" and (
                key == "master_familia_war_game"
            ) != (num(row, 12) == 1):
                continue
            owner = f"mode:{key}:{sid}"
            tag = row.get("2" if key == "telskyura" else "1")
            if isinstance(tag, str) and tag:
                result[tag].add(owner)
            if key == "war_game":
                starts[num(row, 1)].add(owner)
    return (result, starts)


def preview(plan):
    num = delivery.num
    labels, starts = owners()
    selected = {"event:" + str(i["id"]) for i in plan["events"]}
    selected.update((f"mode:{i['key']}:{i['id']}" for i in plan["modes"]))
    found = []
    blocked = []
    epoch = plan["epoch"]
    for row in delivery.decoded("achievement"):
        cat = num(row, 2)
        if cat not in (3, 8):
            continue
        start, end = (num(row, 13), num(row, 14))
        if not start or not start <= epoch < (end or 2147483646):
            continue
        linked = set(labels.get(row.get("10"), ()))
        if num(row, 4) in (901, 902):
            linked.update(starts.get(start, ()))
        active = linked & selected
        item = dict(
            id=num(row, 0),
            title=row.get("7", str(num(row, 0))),
            category="monthly_missions" if cat == 8 else "missions",
            originalStart=start,
            originalEnd=end or 2147483646,
            owners=sorted(active),
        )
        if linked and (not active):
            blocked.append(
                dict(
                    item,
                    reasons=["Parent event or season is not enabled by this preset"],
                )
            )
        else:
            found.append(item)
    return (found, blocked)


def active_periods(settings, active, history):
    """Removing an event/mode also closes its explicit historical missions."""
    if not history.get("enabled"):
        return {}
    selected = {"event:" + str(eid) for eid in active}
    selected.update(
        (
            f"mode:{key}:{modes.selected_season(key, period)}"
            for key, period in settings.items()
        )
    )
    result = {"__historical_missions__": {"start": 1, "end": 2}}
    archived_owners = set(history.get("owners", []))
    for tag, linked in owners()[0].items():
        if archived_owners.intersection(linked):
            result["__historical_original_label__:" + tag] = {"start": 1, "end": 2}
    for aid, period in history.get("periods", {}).items():
        if not period.get("owners") or selected.intersection(period["owners"]):
            result["__historical_achievement__:" + str(aid)] = period
    return result
