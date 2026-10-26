"""Publish settled native Arena record flags from private-server standings.

The isAggregate field and row identities come from the original serializers.
Global rank computation is a private-server reconstruction, not publisher data.
"""

import copy
import functools
import hashlib
import json
from danmemo.config import modes as modes
from danmemo.master import reader as master

SPECS = (
    ("war_game", "pvp", "UserPvPRecord", "pvpId", "pvpInformation", 2),
    ("record_buster", "pvb", "UserPvBRecord", "pvbId", "pvbInformation", None),
    (
        "ultimate_war_game",
        "serial",
        "UserSerialPvpRecord",
        "serialPvpId",
        "serialPvpInformation",
        4,
    ),
    (
        "telskyura",
        "under",
        "UserUnderPvPRecord",
        "underPvpId",
        "underPvpInformation",
        9,
    ),
)


@functools.cache
def originals(name):
    return {r.number(0, 8): r for r in master.rows(master.master_bytes(name))}


def refresh(oc, state, now):
    server = "online" if getattr(oc, "ONLINE_SHARED", None) else "offline"
    settings = modes.config(server)["modes"]
    world = getattr(oc, "ONLINE_SHARED", None)
    changed = False
    for mode, kind, table, key, name, end_field in SPECS:
        rows = state["tables"].get(table, [])
        if not rows:
            continue
        archive = originals(name)
        for row in rows:
            sid = row[key]
            original = archive.get(sid)
            if original is None:
                continue
            parent = original.number(2, 8) if kind == "pvb" else sid
            period = settings.get(mode)
            selected = None
            if kind == "pvb":
                selected = modes.selected_record_buster_events().get(parent)
                if selected:
                    period = selected["period"]
            if period and (
                kind == "pvb"
                and modes.selected_record_buster_events().get(parent)
                or modes.selected_season(mode, period) == parent
            ):
                end = period["end"]
            elif kind == "pvb":
                adventure = originals("adventure")[parent]
                end = adventure.number(7, 8)
            else:
                end = original.number(end_field, 8)
            if now <= end:
                if (
                    period
                    and period["start"] <= now
                    and row.get("isAggregate")
                    and (
                        selected is not None
                        or modes.selected_season(mode, period) == parent
                    )
                ):
                    row["isAggregate"] = False
                    changed = True
                    state["tokens"][table] = hashlib.md5(
                        oc.msgpack_value(rows)
                    ).hexdigest()
                continue
            if row.get("isAggregate"):
                continue
            before = copy.deepcopy(row)
            if world:
                with world.connect() as db:
                    exists = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='reward_rank_snapshots'"
                    ).fetchone()
                    frozen = (
                        db.execute(
                            "SELECT ranking FROM reward_rank_snapshots WHERE key=?",
                            (f"{mode}:{sid}",),
                        ).fetchone()
                        if exists
                        else None
                    )
                ranking = (
                    json.loads(frozen["ranking"])
                    if frozen
                    else [
                        dict(id=r["userId"], score=r["score"])
                        for r in world.ranking(kind, sid)
                    ]
                )
                position = next(
                    (
                        i + 1
                        for i, r in enumerate(ranking)
                        if r["id"] == oc.LOCAL_USER_ID
                    ),
                    None,
                )
                if position is not None:
                    row["totalRank" if kind == "pvb" else "rank"] = position
                    if kind == "pvb":
                        group = row.get("groupId", 1)
                        group_rows = [
                            r for r in ranking if r.get("groupId", 1) == group
                        ]
                        row["groupRank"] = next(
                            (
                                i + 1
                                for i, r in enumerate(group_rows)
                                if r["id"] == oc.LOCAL_USER_ID
                            ),
                            position,
                        )
            row["isAggregate"] = True
            if row != before:
                changed = True
                state["tokens"][table] = hashlib.md5(oc.msgpack_value(rows)).hexdigest()
    return changed
