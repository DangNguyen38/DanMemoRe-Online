"""Documented reconstructions only for archived events without mission masters."""

import functools
import struct
from danmemo.master import reader as master

EPISODES = (7080005101, 7080005201, 7090005501, 7090005601)


def original_windows_overlap(rule, episode):
    """Original native periods are half open; absent bounds are unbounded."""
    from danmemo.game.reward_delivery import num as num

    start, end = (num(rule, 13), num(rule, 14))
    ep_start, ep_end = (num(episode, 6), num(episode, 7))
    if start and start == end == ep_start == ep_end:
        return True
    return (not start or not ep_end or start < ep_end) and (
        not end or not ep_start or end > ep_start
    )


@functools.cache
def reconstructed():
    from danmemo.game.reward_delivery import decoded as decoded, num as num

    original = decoded("achievement")
    episodes = {num(r, 0): r for r in decoded("adventureEpisode")}
    template = next(
        (
            r
            for r in original
            if num(r, 2) == 3 and num(r, 4) == 104 and (num(r, 11) == 1)
        )
    )
    result = []
    for eid in EPISODES:
        ep = episodes.get(eid)
        if not ep or any(
            (num(r, 2) == 3 and r.get("10") == ep.get("1") for r in original)
        ):
            continue
        for difficulty in (1, 2, 3, 4):
            count = sum(
                (
                    num(r, 1) == eid and num(r, 4) == difficulty and (num(r, 5) != 51)
                    for r in decoded("adventureStage")
                )
            )
            if not count:
                continue
            aid = 603900000000 + eid * 10 + difficulty
            rule = dict(template)
            rule.update(
                {
                    "0": aid,
                    "1": f"achievement.reconstructed_{aid}",
                    "7": f"Clear {ep['4']} ({('N', 'H', 'VH', 'EX')[difficulty - 1]})",
                    "9": count,
                    "10": ep["1"],
                    "11": difficulty,
                    "13": num(ep, 6),
                    "14": num(ep, 7),
                    "_originalTemplateId": num(template, 0),
                }
            )
            result.append(rule)
    return result


def rules():
    from danmemo.game.reward_delivery import decoded as decoded

    return decoded("achievement") + reconstructed()


@functools.cache
def catalog_coverage():
    """Original linkage counts, independent of an operator's current schedule."""
    from collections import defaultdict
    from danmemo.game.reward_delivery import decoded as decoded, num as num

    tags = defaultdict(list)
    stage_tags = defaultdict(set)
    for rule in rules():
        if num(rule, 2) == 3 and rule.get("10"):
            tags[rule["10"]].append(rule)
    for stage in decoded("adventureStage"):
        if stage.get("7"):
            stage_tags[num(stage, 1)].add(stage["7"])
    parents = {num(r, 0): r for r in decoded("adventure")}
    result = {}
    for ep in decoded("adventureEpisode"):
        eid = num(ep, 0)
        labels = (
            stage_tags[eid] | {ep.get("1"), parents.get(num(ep, 2), {}).get("1")}
        ) - {None, ""}
        matches = {
            num(r, 0): r
            for tag in labels
            for r in tags[tag]
            if original_windows_overlap(r, ep)
        }
        result[eid] = dict(
            original=sum(("_originalTemplateId" not in r for r in matches.values())),
            reconstructed=sum(("_originalTemplateId" in r for r in matches.values())),
        )
    return result


def append_master(data):
    """Keep original row bytes/relative links; prepend a new root vector."""
    original_rows = master.rows(data)
    existing = {r.number(0, 8) for r in original_rows}
    additions = [r for r in reconstructed() if r["0"] not in existing]
    if not additions:
        return data
    count = len(original_rows) + len(additions)
    prefix = bytearray(24 + 4 + 4 * count)
    prefix += bytes(-len(prefix) % 8)
    struct.pack_into("<I", prefix, 0, 16)
    struct.pack_into("<HHH", prefix, 8, 6, 8, 4)
    struct.pack_into("<iI", prefix, 16, 8, 4)
    struct.pack_into("<I", prefix, 24, count)
    shift = len(prefix)
    result = prefix + data
    for i, row in enumerate(original_rows):
        slot = 28 + i * 4
        struct.pack_into("<I", result, slot, shift + row.offset - slot)
    by_id = {r.number(0, 8): r for r in original_rows}
    for i, rule in enumerate(additions, len(original_rows)):
        source = by_id[rule["_originalTemplateId"]]
        vt = source.offset - struct.unpack_from("<i", data, source.offset)[0]
        vsize, obj_size = struct.unpack_from("<HH", data, vt)
        result += bytes(-len(result) % 8)
        new_vt = len(result)
        result += data[vt : vt + vsize]
        result += bytes(-len(result) % 8)
        new_row = len(result)
        result += data[source.offset : source.offset + obj_size]
        struct.pack_into("<i", result, new_row, new_row - new_vt)
        native = master.Row(result, new_row)
        for field, value in rule.items():
            if not field.isdecimal():
                continue
            field = int(field)
            p = native.pos(field)
            if p is None:
                continue
            if isinstance(value, str):
                result += bytes(-len(result) % 4)
                target = len(result)
                cipher = master.crypt_words(value.encode() + b"\x00", True)
                result += struct.pack("<I", len(cipher)) + cipher + b"\x00"
                struct.pack_into("<I", result, p, target - p)
            elif type(value) is int:
                old = source.pos(field)
                width = next(
                    (
                        w
                        for w in (4, 8)
                        if len(master.crypt_words(data[old : old + w + 4])) == w
                    )
                )
                result[p : p + width + 4] = master.crypt_words(
                    value.to_bytes(width, "little"), True
                )
        slot = 28 + i * 4
        struct.pack_into("<I", result, slot, new_row - slot)
    return result
