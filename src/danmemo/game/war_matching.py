"""Private-server WG selection policy; not the recovered publisher algorithm.

Uses original season/grade identities and actual published season scores.
Uninterpreted pvpMatchingCondition columns are deliberately not used as rules.
"""

import hashlib

REMATCH_GAP = 3


def select(candidates, *, user_id, season, score, grade_id, battle_count, recent=()):
    from danmemo.game import arena as arena

    if not candidates:
        raise ValueError("No other player has published a defense party")
    orders = {r.number(0, 8): r.number(9) for r in arena.grades(season)}
    own_order = orders[grade_id]
    recent = list(recent)[:REMATCH_GAP]
    pool = [r for r in candidates if r["pvpPlayerInfo"]["userId"] not in recent]
    if not pool:
        oldest = max((recent.index(r["pvpPlayerInfo"]["userId"]) for r in candidates))
        pool = [
            r
            for r in candidates
            if recent.index(r["pvpPlayerInfo"]["userId"]) == oldest
        ]

    def distance(r):
        info = r["pvpPlayerInfo"]
        return abs(orders[info["gradeId"]] - own_order)

    closest = min(map(distance, pool))
    pool = [r for r in pool if distance(r) == closest]

    def lottery(r):
        key = f"{user_id}:{season}:{battle_count}:{r['pvpPlayerInfo']['userId']}"
        return hashlib.sha256(key.encode("ascii")).digest()

    pool = sorted(
        pool, key=lambda r: (abs(r["pvpPlayerInfo"]["score"] - score), lottery(r))
    )[:3]
    return min(pool, key=lottery)


def record_opponent(saved, user_id):
    previous = saved.get("lastAdversaryIds", [])
    saved["lastAdversaryIds"] = ([user_id] + previous)[:REMATCH_GAP]
