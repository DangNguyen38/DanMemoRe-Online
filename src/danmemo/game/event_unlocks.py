"""Original condition-9 stage dependencies, not an event availability override.

ARM64 0x2452e28 uses adventureStage slot 38 as the exact clear prerequisite.
See README.md for scope and limitations.
"""

import functools
from danmemo.master import reader as master


@functools.cache
def prerequisites():
    return {
        r.number(0, 8): r.number(38, 8)
        for r in master.rows(master.master_bytes("adventureStage"))
        if r.number(14) == 9
    }


@functools.cache
def chapter_entries():
    stages = {
        r.number(0, 8): r for r in master.rows(master.master_bytes("adventureStage"))
    }
    result = {}
    for sid, target in prerequisites().items():
        row, previous = (stages[sid], stages.get(target))
        if (
            previous
            and row.number(3) == 1
            and (row.number(4) == 1)
            and (row.number(1, 8) != previous.number(1, 8))
        ):
            result.setdefault(target, []).append(sid)
    return result


def eligible(saved, stage_id):
    target = prerequisites().get(stage_id)
    return target is None or saved.get(target, {}).get("state") == 4


def openings(saved, completed_id):
    if saved.get(completed_id, {}).get("state") != 4:
        return []
    return chapter_entries().get(completed_id, [])
