"""Authoritative skip SP for the current snapshot-based server adapter.

Native arithmetic: calculateCampaignSP 0x245b750, consumeSP 0x2548adc,
regeneration 0x254859c (180 seconds). This is a server reconstruction, not
recovered server source. It deliberately emits no completion operation 5000:
that would also replay ticket/EXP/stage writes already handled by this server.
"""

import functools
import struct


@functools.cache
def indexes():
    from danmemo.game.reward_delivery import decoded as decoded, num as num

    return (
        {num(r, 0): r for r in decoded("adventureStage")},
        {num(r, 0): r for r in decoded("adventureEpisode")},
    )


@functools.cache
def campaigns():
    from danmemo.game.reward_delivery import decoded as decoded

    return decoded("adventureCampaign")


def skip_cost(state, stage_id, count, now):
    from danmemo.game.reward_delivery import num as num

    stages, episodes = indexes()
    stage = stages.get(stage_id)
    if stage is None:
        raise ValueError("Unknown quest stamina cost")
    cost = num(stage, 6)
    episode_id = num(stage, 1)
    parent = num(episodes.get(episode_id, {}), 2)
    for campaign in campaigns():
        kind = num(campaign, 1)
        if kind not in (2, 3):
            continue
        if num(campaign, 5) > now or (num(campaign, 6) and now >= num(campaign, 6)):
            continue
        scope = num(campaign, 18)
        if not (
            scope == 1
            and num(campaign, 19) == parent
            or (scope == 2 and num(campaign, 2) == episode_id)
            or (scope == 3 and num(campaign, 20) == stage_id)
        ):
            continue
        if num(campaign, 21) and num(campaign, 21) != num(stage, 4):
            continue
        if kind == 2:
            rate = struct.unpack("<f", struct.pack("<I", num(campaign, 23)))[0]
            product = struct.unpack("<f", struct.pack("<f", cost * rate))[0]
            cost = max(0, int(product))
        else:
            saved = next(
                (
                    r
                    for r in state["tables"].get("UserAdventureStage", [])
                    if r["adventureStageId"] == stage_id
                ),
                {},
            )
            if saved.get("state") != 4:
                cost = 0
        break
    return cost * count


def debit_skip(oc, state, stage_id, count, now):
    from danmemo.game.progression import levels as levels
    from danmemo.game.flow_repair import stamp as stamp

    rows = state["tables"].get("UserInfo", [])
    if not rows or not isinstance(rows[0].get("playerStatus"), dict):
        raise ValueError("Missing player stamina status")
    status = rows[0]["playerStatus"]
    cost = skip_cost(state, stage_id, count, now)
    base = status.get("baseSP", 0)
    updated = status.get("baseSPUpdatedAt", now)
    level = status.get("level", 1)
    cap = next((r[2] for r in levels() if r[1] == level), None)
    if cap is None:
        raise ValueError("Unknown player stamina capacity")
    if base < cap:
        ticks = max(0, now - updated) // 180
        if ticks:
            base = min(cap, base + ticks)
            updated += ticks * 180
    if base < cost:
        raise ValueError("Insufficient quest skip stamina")
    if cost and base >= cap:
        updated = now
    status.update(baseSP=base - cost, baseSPUpdatedAt=updated)
    state.setdefault("arenaStatusReconciliation", {})["baseSP"] = updated
    stamp(oc, state, "UserInfo")
    return cost
