"""Recovered Familia wire/master fields with documented local economy policies.

SQLite receipts form an outbox for the separately persisted player inventory.
Recovery must run before every account action, including ordinary user-data pushes.
See README.md for scope and limitations. for unrecovered server rules.
"""

import copy
import functools
import hashlib
import json
import time
from danmemo.master import reader as master
from danmemo.game.familia import (
    positive as positive,
    role as role,
    system_notice as system_notice,
)

ROUTES = {
    "familia/donate/item",
    "familia/donate/valis",
    "familia/shop/item/buy",
    "familia/shop/item/set",
    "familia/skill/get",
    "familia/skill/growth",
}
MUTATIONS = ROUTES - {"familia/skill/get"}
SCHEMA = "\nCREATE TABLE IF NOT EXISTS familia_economy_receipts(\n    uid INTEGER NOT NULL, receipt TEXT NOT NULL, request TEXT NOT NULL,\n    response TEXT NOT NULL, patch TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 0,\n    PRIMARY KEY(uid,receipt));\nCREATE TABLE IF NOT EXISTS familia_contributions(\n    familia INTEGER NOT NULL, uid INTEGER NOT NULL, points INTEGER NOT NULL,\n    PRIMARY KEY(familia,uid));\nCREATE TABLE IF NOT EXISTS familia_donation_daily(\n    uid INTEGER NOT NULL, day INTEGER NOT NULL, kind TEXT NOT NULL,\n    item INTEGER NOT NULL, amount INTEGER NOT NULL,\n    PRIMARY KEY(uid,day,kind,item));\n"


@functools.cache
def catalogs():
    return {
        "items": {
            r.number(0): (r.number(7), r.number(9))
            for r in master.rows(master.master_bytes("familiaDonationItem"))
        },
        "item_names": {
            r.number(0): r.text(2)
            for r in master.rows(master.master_bytes("familiaDonationItem"))
        },
        "valis": {
            r.number(0): (r.number(1), r.number(2))
            for r in master.rows(master.master_bytes("familiaValisDonation"))
        },
        "shop": {
            r.number(0, 8): (r.number(1), r.number(2, 8), r.number(5))
            for r in master.rows(master.master_bytes("familiaShop"))
        },
        "skills": {
            r.number(0, 8): (r.number(1, 8), r.number(2), r.number(3), r.number(5))
            for r in master.rows(master.master_bytes("familiaSkill"))
        },
        "levels": sorted(
            (
                (r.number(1), r.number(2, 8), r.number(3))
                for r in master.rows(master.master_bytes("familiaLevelStep"))
            )
        ),
    }


def recovery(world, oc):
    """Repeat an interrupted inventory write before any subsequent account action."""
    with world.connect() as db:
        if not db.execute(
            "SELECT 1 FROM familia_economy_receipts WHERE uid=? AND applied=0 LIMIT 1",
            (world.uid,),
        ).fetchone():
            return
        db.execute("BEGIN IMMEDIATE")
        pending = db.execute(
            "SELECT * FROM familia_economy_receipts WHERE uid=? AND applied=0 ORDER BY rowid",
            (world.uid,),
        ).fetchall()
        if not pending:
            return
        with oc.PROGRESS_LOCK:
            state = oc.load_progress()
            guards = state.setdefault("familiaInventoryReconciliation", {})
            for saved in pending:
                patch = json.loads(saved["patch"])
                for table, rows in patch.items():
                    state["tables"][table] = rows
                    state["tokens"][table] = hashlib.md5(
                        oc.msgpack_value(rows)
                    ).hexdigest()
                    key = oc.PROGRESS_KEYS[table]
                    guards[table] = sorted(
                        set(guards.get(table, []))
                        | {
                            r[key]
                            for r in rows
                            if table != "UserCurrency" or r[key] == 205000002
                        }
                    )
                if patch:
                    oc.write_progress(state)
                db.execute(
                    "UPDATE familia_economy_receipts SET applied=1 WHERE uid=? AND receipt=?",
                    (world.uid, saved["receipt"]),
                )


def bounded(value):
    if type(value) is not int or not 0 <= value <= 2147483647:
        raise ValueError("Familia amount exceeds client integer range")
    return value


def debit(tables, table, key, iid, amount):
    row = next((r for r in tables.get(table, []) if r.get(key) == iid), None)
    if row is None or row.get("amount", 0) - row.get("reservedAmount", 0) < amount:
        raise ValueError("Insufficient Familia donation inventory")
    row["amount"] = bounded(row["amount"] - amount)


def daily(db, uid, now, kind, iid, amount, limit):
    day = (now + 9 * 3600) // 86400
    row = db.execute(
        "SELECT amount FROM familia_donation_daily WHERE uid=? AND day=? AND kind=? AND item=?",
        (uid, day, kind, iid),
    ).fetchone()
    count = (row["amount"] if row else 0) + amount
    if count > limit:
        raise ValueError("Familia daily donation limit reached")
    db.execute(
        "INSERT OR REPLACE INTO familia_donation_daily VALUES(?,?,?,?,?)",
        (uid, day, kind, iid, count),
    )


def level_awards(info):
    reached = [
        (level, award)
        for level, threshold, award in catalogs()["levels"]
        if threshold <= info.get("contributionPoint", 0)
    ]
    previous = info.get("level", 1)
    info["skillPoint"] = bounded(
        info.get("skillPoint", 0)
        + sum((award for level, award in reached if level > previous))
    )
    info["level"] = max((level for level, _ in reached), default=1)


def dispatch(world, action, request, oc=None, receipt=None):
    if action not in ROUTES:
        return None
    if oc is not None:
        recovery(world, oc)
    if action in MUTATIONS and (not isinstance(receipt, str) or not receipt):
        raise ValueError("Persistent Familia transaction receipt required")
    signature = json.dumps([action, request], sort_keys=True, separators=(",", ":"))
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if action in MUTATIONS:
            saved = db.execute(
                "SELECT * FROM familia_economy_receipts WHERE uid=? AND receipt=?",
                (world.uid, receipt),
            ).fetchone()
            if saved:
                if saved["request"] != signature:
                    raise ValueError(
                        "Familia transaction receipt reused for another request"
                    )
                replay = json.loads(saved["response"])
                if (
                    action == "familia/donate/item"
                    and "gained_contribution_point" not in replay
                ):
                    points_each, _ = catalogs()["items"][request["itemId"]]
                    replay["gained_contribution_point"] = (
                        points_each * request["amount"]
                    )
                    db.execute(
                        "UPDATE familia_economy_receipts SET response=? WHERE uid=? AND receipt=?",
                        (json.dumps(replay), world.uid, receipt),
                    )
                if replay.get("data") and oc is not None:
                    current = oc.load_progress()
                    replay["data"] = {
                        table: current["tables"].get(table, [])
                        for table in replay["data"]
                    }
                    replay["dataTokens"] = {
                        table: current["tokens"].get(table, "")
                        for table in replay["data"]
                    }
                return replay
        member = db.execute(
            "SELECT * FROM members WHERE uid=?", (world.uid,)
        ).fetchone()
        if not member:
            raise ValueError("Familia membership required")
        fid = member["familia"]
        if "familiaId" in request and positive(request["familiaId"]) != fid:
            raise ValueError("Cannot transact with another Familia")
        family = db.execute("SELECT * FROM familias WHERE id=?", (fid,)).fetchone()
        info = json.loads(family["info"])
        response = dict(code=0, result=0)
        patch = {}
        now = int(time.time())
        notices = []
        if action.startswith("familia/donate/"):
            if oc is None:
                raise ValueError("Familia donation requires an account backend")
            tables = copy.deepcopy(oc.load_progress()["tables"])
            points = 0
            if action.endswith("/item"):
                iid, amount = (
                    positive(request.get("itemId")),
                    positive(request.get("amount")),
                )
                points_each, limit = catalogs()["items"][iid]
                if amount > limit:
                    raise ValueError(
                        "Familia item donation quantity exceeds request limit"
                    )
                debit(
                    tables,
                    "UserFamiliaDonationItem",
                    "familiaDonationItemId",
                    iid,
                    amount,
                )
                notices.append(
                    (
                        40550036,
                        [
                            world.profile(db, world.uid)["name"],
                            catalogs()["item_names"][iid],
                            amount,
                        ],
                    )
                )
                points = points_each * amount
                response["gained_contribution_point"] = points
                patch["UserFamiliaDonationItem"] = tables["UserFamiliaDonationItem"]
            else:
                amounts = request.get("amounts")
                if not isinstance(amounts, list) or not 1 <= len(amounts) <= 3:
                    raise ValueError("Expected Familia Valis denomination rows")
                seen, total = (set(), 0)
                for entry in amounts:
                    if not isinstance(entry, dict):
                        raise ValueError("Invalid Familia Valis denomination")
                    iid, amount = (
                        positive(entry.get("donateValisId")),
                        positive(entry.get("amount")),
                    )
                    if iid in seen:
                        raise ValueError("Repeated Familia Valis denomination")
                    seen.add(iid)
                    cost, limit = catalogs()["valis"][iid]
                    daily(db, world.uid, now, "valis", iid, amount, limit)
                    total += cost * amount
                    points += cost * amount // 100
                total = bounded(total)
                debit(tables, "UserCurrency", "currencyId", 205000002, total)
                patch["UserCurrency"] = tables["UserCurrency"]
                info["valis"] = bounded(info.get("valis", 0) + total)
                notices.append(
                    (40550035, [world.profile(db, world.uid)["name"], total])
                )
            info["contributionPoint"] = bounded(
                info.get("contributionPoint", 0) + points
            )
            prior = db.execute(
                "SELECT points FROM familia_contributions WHERE familia=? AND uid=?",
                (fid, world.uid),
            ).fetchone()
            contribution = bounded((prior["points"] if prior else 0) + points)
            db.execute(
                "INSERT OR REPLACE INTO familia_contributions VALUES(?,?,?)",
                (fid, world.uid, contribution),
            )
            old_level = info.get("level", 1)
            level_awards(info)
            if info["level"] > old_level:
                notices.append((40550037, [info["level"]]))
            response.update(
                familiaContributionPoint=info["contributionPoint"],
                memberContributionPoint=contribution,
            )
        elif action.startswith("familia/shop/"):
            if role(db, world.uid, fid) not in (1, 2):
                raise ValueError("Familia shop management permission required")
            iid = positive(request.get("familiaShopId"))
            kind, ref, cost = catalogs()["shop"][iid]
            owned = info.setdefault("purchasedProduct", [])
            if action.endswith("/buy"):
                if iid not in owned:
                    if info.get("valis", 0) < cost:
                        raise ValueError("Insufficient Familia treasury")
                    info["valis"] = bounded(info["valis"] - cost)
                    owned.append(iid)
                response.update(
                    valis=info.get("valis", 0), purchasedProduct=sorted(owned)
                )
            else:
                if iid not in owned or kind not in (2, 3):
                    raise ValueError("Familia decoration must be owned")
                info["bgmId" if kind == 2 else "backgroundImageId"] = ref
                response["familiaShopId"] = iid
        else:
            owned = info.setdefault("familiaSkillIds", [])
            if action.endswith("/growth"):
                if role(db, world.uid, fid) not in (1, 2):
                    raise ValueError("Familia skill management permission required")
                iid = positive(request.get("familiaSkillId"))
                group, kind, level, cost = catalogs()["skills"][iid]
                current = [
                    x for x in owned if catalogs()["skills"][x][:2] == (group, kind)
                ]
                current_level = max(
                    (catalogs()["skills"][x][2] for x in current), default=0
                )
                if level != current_level + 1 or info.get("skillPoint", 0) < cost:
                    raise ValueError(
                        "Invalid Familia skill level or insufficient points"
                    )
                info["skillPoint"] -= cost
                owned[:] = [x for x in owned if x not in current] + [iid]
                response["familiaSkillId"] = iid
            response.update(
                familiaSkillIds=sorted(owned), skillPoint=info.get("skillPoint", 0)
            )
        if action in MUTATIONS:
            for message_id, parameters in notices:
                system_notice(
                    db,
                    fid,
                    world.uid,
                    world.profile(db, world.uid),
                    message_id,
                    parameters,
                    now,
                )
            db.execute("UPDATE familias SET info=? WHERE id=?", (json.dumps(info), fid))
            if patch:
                response.update(
                    data=patch,
                    dataTokens={
                        table: hashlib.md5(oc.msgpack_value(rows)).hexdigest()
                        for table, rows in patch.items()
                    },
                    operations=[],
                )
            db.execute(
                "INSERT INTO familia_economy_receipts(uid,receipt,request,response,patch) VALUES(?,?,?,?,?)",
                (
                    world.uid,
                    receipt,
                    signature,
                    json.dumps(response),
                    json.dumps(patch),
                ),
            )
    if oc is not None:
        recovery(world, oc)
    return response
