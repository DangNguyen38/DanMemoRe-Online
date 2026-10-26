"""Recovered Monsterphilia completion reward channel and master prizes.

Original native 0x246e370 reads rewardRanks; 0x246c6dc grants gainedItems.
Once-per-new-rank eligibility is a documented local reconstruction: the former
server is unavailable. Preserve imported clear history and old mail receipts.
"""

import copy
import functools
import hashlib
import struct
import time
from danmemo.master import reader as master
from danmemo.game import resources as resources
from danmemo.game.transactions import identities as identities


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


@functools.cache
def definitions():
    labels = {
        r.text(2 if name == "memoriaExpItem" else 1): r.number(0)
        for name in resources.NAMES
        for r in master.rows(master.master_bytes(name))
    }
    base = next(
        (
            r
            for r in master.rows(master.master_bytes("adventureStageScoreBase"))
            if r.number(1) == 1
        )
    )
    result = {}
    for row in master.rows(master.master_bytes("adventureStageScore")):
        coefficient = struct.unpack("<f", row.number(3).to_bytes(4, "little"))[0]
        result[row.number(1, 8)] = [
            dict(
                rank=rank,
                threshold=int(f32(coefficient * f32(base.number(threshold)))),
                itemId=labels.get(row.text(label)),
                amount=row.number(amount),
                kind=row.number(label - 1),
            )
            for rank, threshold, label, amount in [
                ("s", 2, 5, 6),
                ("a", 3, 8, 9),
                ("b", 4, 11, 12),
                ("c", 5, 14, 15),
            ]
        ]
    return result


def award(client, state, stage, score, response):
    sid = stage["adventureStageId"]
    prior = next(
        (
            r
            for r in state["tables"].get("UserAdventureStage", [])
            if r["adventureStageId"] == sid
        ),
        {},
    )
    saved = state.setdefault("monsterRankRewardReceipts", {}).setdefault(str(sid), {})
    amounts = {}
    earned = []
    now = int(time.time())
    for rule in definitions()[sid]:
        rank = rule["rank"]
        old = state.get("gameRewardMailReceipts", {}).get(f"monster-rank:{sid}:{rank}")
        if old and (not old.get("voidReason")):
            saved.setdefault(
                rank, dict(source="previous-mail", giftIds=old.get("giftIds", []))
            )
        elif (
            prior.get("firstCompleteDateTime")
            and prior.get("highScore", 0) >= rule["threshold"]
        ):
            saved.setdefault(rank, dict(source="imported-clear-history"))
        if rank in saved or score < rule["threshold"]:
            continue
        iid = rule["itemId"]
        amount = rule["amount"]
        if rule["kind"] not in (0, 1) or iid not in identities() or amount <= 0:
            raise ValueError("Unsupported original Monsterphilia rank prize")
        saved[rank] = dict(
            source="stage-completion", itemId=iid, amount=amount, awardedAt=now
        )
        amounts[iid] = amounts.get(iid, 0) + amount
        earned.append(rank)
    changed = set()
    for iid, amount in amounts.items():
        table, key = identities()[iid]
        resources.add(
            state["tables"], table, key, iid, amount, client.LOCAL_USER_ID, now
        )
        changed.add(table)
    for table in changed:
        state["tokens"][table] = hashlib.md5(
            client.msgpack_value(state["tables"][table])
        ).hexdigest()
    response["rewardRanks"] = earned
    response["gainedItems"] = [
        dict(itemId=iid, amount=amount) for iid, amount in sorted(amounts.items())
    ]
    return changed


def retry_inventory(state, response):
    tables = {identities()[r["itemId"]][0] for r in response.get("gainedItems", [])}
    for table in tables:
        response["data"][table] = copy.deepcopy(state["tables"][table])
        response["dataTokens"][table] = state["tokens"][table]
    response["gainedItems"] = []
    response["rewardRanks"] = []
