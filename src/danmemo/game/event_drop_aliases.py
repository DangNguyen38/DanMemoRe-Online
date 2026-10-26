"""Archive compatibility for reused field assets pointing at rerun currencies.

The episode's original shop and event-box masters select the usable currency
IDs. Matching a foreign drop requires an unambiguous identical name AND
description within that episode's currencies. This is an offline repair,
not recovered publisher server code or a change to drop quantities/rates.
"""

import functools


@functools.cache
def indexes():
    from danmemo.master import reader as master

    read = lambda name: master.rows(master.master_bytes(name))
    return (
        {r.number(0, 8): {"1": r.number(1, 8)} for r in read("adventureStage")},
        {
            r.number(0, 8): {"15": r.text(15), "23": r.text(23)}
            for r in read("adventureEpisode")
        },
        {r.text(1): {str(f): r.text(f) for f in (6, 7, 8)} for r in read("shop")},
        {r.text(2): {"3": r.text(3)} for r in read("eventBox")},
        {
            r.number(0): {"1": r.text(1), "2": r.text(2), "3": r.text(3)}
            for r in read("eventItem")
        },
    )


@functools.lru_cache(maxsize=256)
def aliases(stage_id):
    from danmemo.game.reward_delivery import num as num

    stages, episodes, shops, boxes, items = indexes()
    stage = stages.get(stage_id, {})
    episode = episodes.get(num(stage, 1), {})
    shop = shops.get(episode.get("15"), {})
    box = boxes.get(episode.get("23"), {})
    labels = {shop.get(str(f)) for f in (6, 7, 8)} | {box.get("3")}
    expected = {iid: row for iid, row in items.items() if row.get("1") in labels}
    result = {}
    for iid, row in items.items():
        if iid in expected:
            continue
        matches = [
            target
            for target, other in expected.items()
            if row.get("2")
            and (row.get("2"), row.get("3", "")) == (other.get("2"), other.get("3", ""))
        ]
        if len(matches) == 1:
            result[iid] = matches[0]
    return result
