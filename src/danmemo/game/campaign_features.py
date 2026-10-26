"""Original election operation 17000 and native Hero Choice save contracts.

Shared vote totals describe only this private server. Snapshot reconciliation is
our adapter policy; it is not the publisher's operation acknowledgement service.
"""

import copy
import functools
import hashlib
import json
import secrets
import time
from danmemo.master import reader as master

ROUTES = {"election/vote"}
SCHEMA = "CREATE TABLE IF NOT EXISTS election_receipts(\n id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER NOT NULL, receipt TEXT NOT NULL,\n request TEXT NOT NULL, election INTEGER NOT NULL, candidate INTEGER NOT NULL,\n votes INTEGER NOT NULL, response TEXT NOT NULL, patch TEXT NOT NULL,\n UNIQUE(uid,receipt));"


@functools.cache
def catalog():
    candidates = {}
    for row in master.rows(master.master_bytes("voteCharacterList")):
        candidates.setdefault(row.number(1, 8), []).append(row.number(0, 8))
    return [
        dict(
            id=r.number(0, 8),
            title=r.text(1),
            start=r.number(2, 8),
            end=r.number(3, 8),
            item=r.number(4),
            initial=r.number(5),
            candidates=candidates.get(r.number(0, 8), []),
        )
        for r in master.rows(master.master_bytes("voteInfo"))
    ]


def active(now=None, server="online"):
    from danmemo.config import modes as modes

    now = int(time.time() if now is None else now)
    period = modes.config(server)["modes"].get("character_vote")
    for event in catalog():
        start, end = (event["start"], event["end"])
        if period and event["id"] == period.get("seasonId"):
            start, end = (period["start"], period["end"])
        elif period:
            continue
        if (not start or start <= now) and (not end or now < end):
            return event
    return None


def prepare(oc):
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        changed = set()
        for event in catalog():
            for table, key, fields in (
                ("UserVoteInfo", "voteInfoId", dict(count=0, defaultItemReceive=False)),
                (
                    "UserVoteItem",
                    "voteItemId",
                    dict(amount=0, cumulativeAmount=0, reservedAmount=0, signature=0),
                ),
            ):
                identifier = event["id"] if table == "UserVoteInfo" else event["item"]
                rows = state["tables"].setdefault(table, [])
                if not any((r.get(key) == identifier for r in rows)):
                    rows.append(
                        dict(userId=oc.LOCAL_USER_ID, **{key: identifier}, **fields)
                    )
                    changed.add(table)
        if oc.repair_feature_gates(state):
            changed.add("UserSystemFlag")
        for table in changed:
            state["tokens"][table] = hashlib.md5(
                oc.msgpack_value(state["tables"][table])
            ).hexdigest()
        if changed:
            oc.write_progress(state)


def reconcile(state, table, row, previous):
    if table == "UserInfo" and row.get("heroType", 0) != previous.get("heroType", 0):
        hero = row.get("heroType", 0)
        if type(hero) is not int or hero not in (0, 1, 2):
            raise ValueError("Invalid Hero Choice")
        flag = {1: 91218012, 2: 91218013}.get(hero)
        flags = {
            r["systemFlagId"]: r.get("flagValue", 0)
            for r in state["tables"].get("UserSystemFlag", [])
        }
        if flag and (not flags.get(flag)):
            raise ValueError("Complete the original Hero Choice unlock quest first")
    key = {"UserVoteInfo": "voteInfoId", "UserVoteItem": "voteItemId"}.get(table)
    if not key:
        return row
    expected = (
        state.get("electionReconciliation", {}).get(table, {}).get(str(row.get(key)))
    )
    if expected:
        fields = (
            ("count", "defaultItemReceive")
            if table == "UserVoteInfo"
            else ("amount", "cumulativeAmount")
        )
        if all((row.get(k) == expected.get(k) for k in fields)):
            state["electionReconciliation"][table].pop(str(row[key]))
        else:
            return copy.deepcopy(expected)
    if (
        table == "UserVoteInfo"
        and previous.get("defaultItemReceive")
        and (not row.get("defaultItemReceive"))
    ):
        row = dict(row, defaultItemReceive=True)
    if table == "UserVoteInfo" and row.get("count", 0) < previous.get("count", 0):
        row = dict(row, count=previous["count"])
    return row


def recovery(world, oc):
    with world.connect() as db:
        rows = db.execute(
            "SELECT * FROM election_receipts WHERE uid=? ORDER BY id", (world.uid,)
        ).fetchall()
    if not rows:
        return
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        seen = state.setdefault("electionReceipts", [])
        changed = set()
        for receipt in rows:
            if receipt["id"] in seen:
                continue
            for table, row in json.loads(receipt["patch"]).items():
                key = "voteInfoId" if table == "UserVoteInfo" else "voteItemId"
                saved = state["tables"].setdefault(table, [])
                saved[:] = [r for r in saved if r[key] != row[key]] + [row]
                state.setdefault("electionReconciliation", {}).setdefault(table, {})[
                    str(row[key])
                ] = row
                changed.add(table)
            seen.append(receipt["id"])
        if changed:
            for table in changed:
                state["tokens"][table] = hashlib.md5(
                    oc.msgpack_value(state["tables"][table])
                ).hexdigest()
            oc.write_progress(state)


def dispatch(world, request, oc, receipt):
    if (
        set(request) != {"electionId", "voteId", "voteCount"}
        or any((type(request.get(k)) is not int or request[k] <= 0 for k in request))
        or request["voteCount"] > 999999999
    ):
        raise ValueError("Invalid election vote")
    if not isinstance(receipt, str) or not receipt or len(receipt) > 200:
        raise ValueError("Missing vote request identity")
    recovery(world, oc)
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute(
            "SELECT * FROM election_receipts WHERE uid=? AND receipt=?",
            (world.uid, receipt),
        ).fetchone()
        if old:
            if old["request"] != encoded:
                raise ValueError("Conflicting vote retry")
            return json.loads(old["response"])
        event = active(server="online" if hasattr(oc, "ONLINE_SHARED") else "offline")
        if (
            not event
            or event["id"] != request["electionId"]
            or request["voteId"] not in event["candidates"]
        ):
            raise ValueError("Election or candidate is not currently available")
        with oc.PROGRESS_LOCK:
            state = oc.load_progress()
            info = copy.deepcopy(
                next(
                    (
                        r
                        for r in state["tables"].get("UserVoteInfo", [])
                        if r["voteInfoId"] == event["id"]
                    ),
                    {},
                )
            )
            item = copy.deepcopy(
                next(
                    (
                        r
                        for r in state["tables"].get("UserVoteItem", [])
                        if r["voteItemId"] == event["item"]
                    ),
                    {},
                )
            )
            amount = request["voteCount"]
            if (
                info.get("userId") != world.uid
                or item.get("userId") != world.uid
                or item.get("amount", 0) - item.get("reservedAmount", 0) < amount
                or (info.get("count", 0) + amount > 999999999)
            ):
                raise ValueError("Insufficient election tickets")
            totals = dict(
                db.execute(
                    "SELECT candidate,SUM(votes) FROM election_receipts WHERE election=? GROUP BY candidate",
                    (event["id"],),
                )
            )
            total = totals.get(request["voteId"], 0) + amount
            rank = 1 + sum(
                (
                    count > total
                    for candidate, count in totals.items()
                    if candidate != request["voteId"]
                )
            )
            item["amount"] -= amount
            info["count"] += amount
            signature = secrets.randbelow(2147483646) + 1
            item["signature"] = signature
            cursor = db.execute(
                "INSERT INTO election_receipts(uid,receipt,request,election,candidate,votes,response,patch) VALUES(?,?,?,?,?,?,?,?)",
                (
                    world.uid,
                    receipt,
                    encoded,
                    event["id"],
                    request["voteId"],
                    amount,
                    "{}",
                    json.dumps({"UserVoteInfo": info, "UserVoteItem": item}),
                ),
            )
            operation = dict(
                id=cursor.lastrowid,
                type=17000,
                token=secrets.token_hex(16),
                signature=signature,
                parameters=dict(
                    electionId=event["id"],
                    consumedItem=dict(itemId=event["item"], amount=amount),
                ),
            )
            response = dict(code=0, rank=rank, count=total, operations=[operation])
            db.execute(
                "UPDATE election_receipts SET response=? WHERE id=?",
                (json.dumps(response), cursor.lastrowid),
            )
    recovery(world, oc)
    return response
