"""Original per-pool master flags; award semantics are an offline reconstruction.

lotteryCharacter field 4 is retained per selected pool, never inferred from
Hero Festa banner branding or from the unconditional lotteryPickup marker.
"""

import functools
from danmemo.master import reader as master


@functools.cache
def catalog():
    supported = {
        r.number(0)
        for r in master.rows(master.master_bytes("pc"))
        if r.number(9) == 1 and r.number(79) > 0 and (r.number(90) > 0)
    }
    groups = {}
    for row in master.rows(master.master_bytes("lotteryCharacter")):
        if row.number(4) == 1 and row.number(2) in supported:
            groups.setdefault(row.number(1), set()).add(row.number(2))
    steps = {}
    for row in master.rows(master.master_bytes("lotteryStepUpStep")):
        steps.setdefault(row.number(1), {})[str(row.number(5))] = {
            "normal": groups.get(row.number(2), set()),
            "fixed": groups.get(row.number(3), set()),
        }
    guarantees = {}
    for row in master.rows(master.master_bytes("lotteryStep")):
        if row.number(3):
            guarantees.setdefault(row.number(1), {})[str(row.number(2))] = groups.get(
                row.number(3), set()
            )
    return {
        row.number(0): {
            "pools": {
                str(field): groups.get(row.number(field), set())
                for field in (7, 25, 35)
            },
            "steps": steps.get(row.number(43), {}),
            "guarantees": guarantees.get(row.number(0), {}),
        }
        for row in master.rows(master.master_bytes("lottery"))
    }


def selected(lottery_id, button, step_index, progress_count):
    banner = catalog().get(lottery_id, {})
    pools = banner.get("pools", {})
    normal = pools.get("7", set())
    fixed = pools.get("35" if button == 3 else "25", set())
    if button == 3 and banner.get("steps"):
        step = banner["steps"].get(str(step_index), {})
        normal, fixed = (step.get("normal", set()), step.get("fixed", set()))
    if button == 3 and str(progress_count + 1) in banner.get("guarantees", {}):
        fixed = banner["guarantees"][str(progress_count + 1)]
    return (normal, fixed)
