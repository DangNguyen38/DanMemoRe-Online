"""Select archived availability windows without rewinding multiplayer clocks.

Inspired by upstream createEventClock, but this is an operator schedule preset,
not the upstream per-save virtual clock or recovered publisher server behavior.
"""

from danmemo.config import modes as modes
from danmemo.master import reader as master


def closed_record_buster(epoch):
    """Keep the original result/display season without reopening its battles."""
    parents = {r.number(0, 8): r for r in master.rows(master.master_bytes("adventure"))}
    candidates = []
    for info in master.rows(master.master_bytes("pvbInformation")):
        parent = parents.get(info.number(2, 8))
        if parent is None:
            continue
        start, end, display_start, display_end = (
            parent.number(f, 8) for f in (6, 7, 8, 9)
        )
        if display_start <= epoch < display_end and end <= epoch:
            candidates.append(
                dict(
                    id=info.number(0, 8),
                    parentId=info.number(2, 8),
                    key="record_buster",
                    title=info.text(4),
                    artwork=info.text(6),
                    originalStart=start,
                    originalEnd=end,
                    originalDisplayEnd=display_end,
                    originalTimestamps={
                        str(f): parent.number(f, 8) for f in (6, 7, 8, 9)
                    },
                )
            )
    return candidates[0] if candidates else None


def previous_war_game(epoch):
    rows = [
        r
        for r in master.rows(master.master_bytes("pvpInformation"))
        if r.number(1, 8) <= epoch
    ]
    if not rows:
        return None
    r = rows[-1]
    start, end, tally, display_end = (r.number(f, 8) for f in (1, 2, 3, 4))
    return dict(
        id=r.number(0, 8),
        key="war_game",
        title=f"War Game Season {r.number(0, 8) % 1000}",
        originalStart=start,
        originalEnd=end,
        originalDisplayEnd=display_end,
        originalTimestamps={str(f): r.number(f, 8) for f in (1, 2, 3, 4)},
    )


def previous_familia_war(epoch):
    rows = [
        r
        for r in master.rows(master.master_bytes("guildVsGuildInformation"))
        if r.number(6, 8) <= epoch
    ]
    if not rows:
        return None
    r = rows[-1]
    is_master = r.number(12) == 1
    key = "master_familia_war_game" if is_master else "familia_war_game"
    start, end, display_end = (r.number(f, 8) for f in (6, 7, 8))
    timestamps = {str(f): r.number(f, 8) for f in (6, 7, 8, 9, 10, 11)}
    return dict(
        id=r.number(0, 8),
        key=key,
        title=r.text(2) or f"FWG Season {r.number(0, 8) % 1000}",
        originalStart=start,
        originalEnd=end,
        originalDisplayEnd=display_end,
        originalBattleStart=r.number(11, 8),
        originalTimestamps=timestamps,
    )


def previous_record_buster(epoch):
    closed = closed_record_buster(epoch)
    if closed:
        return closed
    parents = {r.number(0, 8): r for r in master.rows(master.master_bytes("adventure"))}
    candidates = []
    for info in master.rows(master.master_bytes("pvbInformation")):
        parent = parents.get(info.number(2, 8))
        if parent is None:
            continue
        start, end, display_start, display_end = (
            parent.number(f, 8) for f in (6, 7, 8, 9)
        )
        if end <= epoch:
            candidates.append(
                dict(
                    id=info.number(0, 8),
                    parentId=info.number(2, 8),
                    key="record_buster",
                    title=info.text(4),
                    artwork=info.text(6),
                    originalStart=start,
                    originalEnd=end,
                    originalDisplayEnd=display_end,
                    originalTimestamps={
                        str(f): parent.number(f, 8) for f in (6, 7, 8, 9)
                    },
                )
            )
    return (
        sorted(candidates, key=lambda c: c["originalEnd"])[-1] if candidates else None
    )


def closed_arena_modes(epoch, active_modes):
    active_keys = {m["key"] for m in active_modes}
    active_groups = {
        modes.MODES[m["key"]].get("group") or m["key"] for m in active_modes
    }
    closed = {}
    if "war_game" not in active_keys:
        pw = previous_war_game(epoch)
        if pw:
            closed["war_game"] = pw
    if "telskyura" not in active_keys:
        rows_under = [
            r
            for r in master.rows(master.master_bytes("underPvpInformation"))
            if r.number(8, 8) <= epoch
        ]
        if rows_under:
            r = rows_under[-1]
            start, end, tally, display_end = (r.number(f, 8) for f in (8, 9, 10, 11))
            closed["telskyura"] = dict(
                id=r.number(0, 8),
                key="telskyura",
                title=r.text(1),
                originalStart=start,
                originalEnd=end,
                originalDisplayEnd=display_end,
                originalTimestamps={str(f): r.number(f, 8) for f in (8, 9, 10, 11)},
            )
    if "ultimate_war_game" not in active_keys:
        rows_serial = [
            r
            for r in master.rows(master.master_bytes("serialPvpInformation"))
            if r.number(3, 8) <= epoch
        ]
        if rows_serial:
            r = rows_serial[-1]
            start, end, tally, display_end = (r.number(f, 8) for f in (3, 4, 5, 6))
            closed["ultimate_war_game"] = dict(
                id=r.number(0, 8),
                key="ultimate_war_game",
                title=r.text(2),
                originalStart=start,
                originalEnd=end,
                originalDisplayEnd=display_end,
                originalTimestamps={str(f): r.number(f, 8) for f in (3, 4, 5, 6)},
            )
    if "familia_war" not in active_groups:
        pf = previous_familia_war(epoch)
        if pf:
            closed[pf["key"]] = pf
    if "record_buster" not in active_keys:
        pr = previous_record_buster(epoch)
        if pr:
            closed["record_buster"] = pr
    return closed


def shifted_closed(item, epoch, now):
    s_start = now + item["originalStart"] - epoch
    s_end = now + item["originalEnd"] - epoch
    end = min(s_end, now - 60)
    start = min(s_start, end - 86400)
    s_disp = now + item.get("originalDisplayEnd", item["originalEnd"]) - epoch
    display_end = max(now + 30 * 86400, s_disp)
    period = dict(
        start=max(1, start),
        end=max(2, end),
        displayEnd=min(2147483646, display_end),
        seasonId=item["id"],
        closed=True,
    )
    key = item["key"]
    if key == "war_game":
        period["timestampFields"] = {
            "1": max(1, start),
            "2": max(2, end),
            "3": max(1, start),
            "4": min(2147483646, display_end),
        }
    elif key == "ultimate_war_game":
        period["timestampFields"] = {
            "3": max(1, start),
            "4": max(2, end),
            "5": max(1, start),
            "6": min(2147483646, display_end),
        }
    elif key == "telskyura":
        period["timestampFields"] = {
            "8": max(1, start),
            "9": max(2, end),
            "10": max(1, start),
            "11": min(2147483646, display_end),
        }
    elif key in ("familia_war_game", "master_familia_war_game"):
        period["timestampFields"] = {
            "6": max(1, start),
            "7": max(2, end),
            "8": min(2147483646, display_end),
            "9": max(1, start - 86400),
            "10": max(2, start - 1),
            "11": max(1, start),
        }
        period["battleStart"] = max(1, start)
        period["displayEnd"] = min(2147483646, display_end)
    elif key == "record_buster":
        period["timestampFields"] = {
            "6": max(1, start),
            "7": max(2, end),
            "8": max(1, start),
            "9": min(2147483646, display_end),
        }
    return period
