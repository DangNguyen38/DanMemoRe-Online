"""Recover clears from accepted receipts for archived scripted encounters.

Native common completion 0x246d494 calls 0x2451924, which sets state4
unconditionally. isEnemyAnnihilated is not an isWin/clear flag. The small
legacy repair is restricted to the original marker pair shared by Fianna's
Deirdre encounter; no semantic names are invented for master columns49/56.
No inventory, rewards, EXP, stamina or historical daily missions are replayed.
"""

import functools
import hashlib
import time
from danmemo.master import reader as master


@functools.cache
def legacy_candidates():
    return frozenset(
        (
            r.number(0, 8)
            for r in master.rows(master.master_bytes("adventureStage"))
            if r.number(5) == 12 and r.number(49) == 1 and (r.number(56) == 1)
        )
    )


def repair_legacy(oc, state, now=None):
    if state.get("scriptedClearRecoveryVersion") == 1:
        return False
    now = int(time.time()) if now is None else int(now)
    state["scriptedClearRecoveryVersion"] = 1
    rows = state["tables"].get("UserAdventureStage", [])
    saved = {r["adventureStageId"]: r for r in rows}
    accepted = {}
    for key, receipt in state.get("completed", {}).items():
        if (
            not isinstance(receipt, dict)
            or receipt.get("code") != 0
            or receipt.get("skipCount")
        ):
            continue
        for root in receipt.get("UserAdventureStage", []):
            sid = root.get("adventureStageId")
            if (
                sid not in legacy_candidates()
                or not key.startswith(str(sid) + ":")
                or root.get("state") not in (1, 2, 3)
                or (root.get("cumulativeClearCount", 0) != 0)
            ):
                continue
            row = saved.get(sid)
            if (
                row
                and row.get("state") != 4
                and (row.get("cumulativeClearCount", 0) == 0)
            ):
                accepted.setdefault(sid, []).append(receipt)
    for sid, receipts in accepted.items():
        row = saved[sid]
        row.update(
            state=4,
            cumulativeClearCount=1,
            firstCompleteDateTime=row.get("firstCompleteDateTime") or now,
            lastCompleteDateTime=row.get("lastCompleteDateTime") or now,
        )
        tasks = {t for r in receipts for t in r.get("adventureStageTaskIds", [])}
        row["tasks"] = [
            dict(t, state=2) if t["adventureStageTaskId"] in tasks else t
            for t in row.get("tasks", [])
        ]
    if not accepted:
        return False
    for sid in accepted:
        rows = oc.unlock_stage_successor(rows, sid)
    state["tables"]["UserAdventureStage"] = rows
    state["tokens"]["UserAdventureStage"] = hashlib.md5(
        oc.msgpack_value(rows)
    ).hexdigest()
    state["scriptedClearRecovery"] = dict(confirmedAt=now, stages=sorted(accepted))
    return True
