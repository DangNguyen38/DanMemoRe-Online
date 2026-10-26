"""Account transactions whose native callbacks apply the returned changes locally."""

import functools
import hashlib
import copy
import secrets
import time
from danmemo.master import reader as master
from danmemo.game import resources as resources


@functools.cache
def identities():
    return {
        row.number(0): (
            resources.table_name(name),
            resources.identity_key(resources.table_name(name)),
        )
        for name in resources.NAMES
        for row in master.rows(master.master_bytes(name))
    }


def repair_currencies(oc):
    with oc.PROGRESS_LOCK:
        saved = oc.load_progress()
        rows = saved["tables"].get("UserCurrency", [])
        missing = {
            row.number(0) for row in master.rows(master.master_bytes("currency"))
        } - {r["currencyId"] for r in rows}
        if not missing:
            return
        for iid in sorted(missing):
            resources.add(
                saved["tables"],
                "UserCurrency",
                "currencyId",
                iid,
                0,
                oc.LOCAL_USER_ID,
                int(time.time()),
            )
        saved["tokens"]["UserCurrency"] = hashlib.md5(
            oc.msgpack_value(saved["tables"]["UserCurrency"])
        ).hexdigest()
        oc.write_progress(saved)


@functools.cache
def expedition_stages():
    return {r.number(0) for r in master.rows(master.master_bytes("expeditionStage"))}


def dispatch(oc, request):
    sid = request.get("expeditionStageId")
    if type(sid) is not int or sid not in expedition_stages():
        raise ValueError("Unknown dispatch stage")
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        saved = state["tables"]
        row = next(
            (
                r
                for r in saved.get("UserExpeditionStage", [])
                if r["expeditionStageId"] == sid
            ),
            None,
        )
        party = next(
            (
                r
                for r in saved.get("UserExpeditionParty", [])
                if r["expeditionStageId"] == sid
            ),
            None,
        )
        if row is None or not row.get("completionState") or party is None:
            raise ValueError("Dispatch stage or party is not available")
        if not row.get("dispatchState"):
            now = int(time.time())
            row.update(dispatchState=1, startAt=now, lastTryDateTime=now)
            if not row.get("firstTryDateTime"):
                row["firstTryDateTime"] = now
            state["tokens"]["UserExpeditionStage"] = hashlib.md5(
                oc.msgpack_value(saved["UserExpeditionStage"])
            ).hexdigest()
            oc.write_progress(state)
        return {
            "code": 0,
            "UserExpeditionStage": row,
            "data": {"UserExpeditionStage": [row]},
            "dataTokens": {
                "UserExpeditionStage": state["tokens"]["UserExpeditionStage"]
            },
            "operations": [],
        }


@functools.cache
def expedition_rewards():
    stages = {
        r.number(0): r for r in master.rows(master.master_bytes("expeditionStage"))
    }
    groups = {
        r.number(0): r
        for r in master.rows(master.master_bytes("expeditionRewardGroup"))
    }
    rewards, counts = ({}, {})
    for r in master.rows(master.master_bytes("expeditionReward")):
        rewards.setdefault(r.number(1), []).append(
            (r.number(2), r.number(4), r.number(5))
        )
    for r in master.rows(master.master_bytes("expeditionRewardCount")):
        counts.setdefault(r.number(1), []).append((r.number(2), r.number(3, 8)))
    return (stages, groups, rewards, counts)


def weighted_dispatch(rows):
    total = sum((r[0] for r in rows))
    if not rows or total <= 0:
        raise ValueError("Missing original Dispatch reward weights")
    draw = secrets.randbelow(total)
    for row in rows:
        draw -= row[0]
        if draw < 0:
            return row[1:]


def dispatch_complete(oc, request):
    sid = request.get("expeditionStageId")
    stages, groups, rewards, counts = expedition_rewards()
    if type(sid) is not int or sid not in stages:
        raise ValueError("Unknown Dispatch stage")
    entries, friendship = (request.get("entryPcs"), request.get("friendshipStates"))
    if (
        not isinstance(entries, list)
        or not entries
        or (not isinstance(friendship, list))
        or any(
            (
                not isinstance(e, dict)
                or any(
                    (
                        type(e.get(k)) is not int or e[k] < 0
                        for k in ("characterId", "level", "status")
                    )
                )
                for e in entries
            )
        )
        or (type(request.get("totalLevel")) is not int)
        or (type(request.get("totalStatus")) is not int)
        or (request["totalLevel"] != sum((e["level"] for e in entries)))
        or (request["totalStatus"] != sum((e["status"] for e in entries)))
    ):
        raise ValueError("Invalid Dispatch party metrics")
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        tables = state["tables"]
        row = next(
            (
                r
                for r in tables.get("UserExpeditionStage", [])
                if r["expeditionStageId"] == sid
            ),
            None,
        )
        if row is None:
            raise ValueError("Dispatch stage is not available")
        key = f"{sid}:{row.get('lastTryDateTime', row.get('startAt', 0))}"
        ledger = state.setdefault("dispatchCompletions", {})
        if key in ledger:
            return dispatch_receipt(state, ledger[key])
        now, stage = (int(time.time()), stages[sid])
        if not row.get("dispatchState") or now < row.get("startAt", now) + stage.number(
            8
        ):
            raise ValueError("Dispatch is not finished")
        updates = []
        labels, levels, _ = cp_master()
        seen = set()
        for entry in friendship:
            if not isinstance(entry, dict) or any(
                (
                    type(entry.get(k)) is not int
                    for k in (
                        "characterId",
                        "friendship",
                        "friendshipLv",
                        "userHeroType",
                    )
                )
            ):
                raise ValueError("Invalid Dispatch friendship state")
            cid, cp, hero = (
                entry["characterId"],
                entry["friendship"],
                entry["userHeroType"],
            )
            character = next(
                (r for r in tables.get("UserCharacter", []) if r["characterId"] == cid),
                None,
            )
            if (
                character is None
                or cid not in labels
                or cid in seen
                or (hero not in (0, 1, 2))
            ):
                raise ValueError("Unknown Dispatch character")
            seen.add(cid)
            field = ("friendship", "friendshipAis", "friendshipLef")[hero]
            if not character.get(field, 0) <= cp <= 2000000000 or entry[
                "friendshipLv"
            ] != max((lv for threshold, lv in levels[labels[cid]] if threshold <= cp)):
                raise ValueError("Invalid Dispatch friendship progression")
            updates.append((character, field, cp))
        status = int(
            request["totalStatus"] >= stage.number(13)
            and request["totalLevel"] >= stage.number(14)
        )
        gid = stage.number(9 if status else 11)
        count = weighted_dispatch(counts.get(gid, []))[0]
        if not 0 <= count <= 10000 or gid not in groups:
            raise ValueError("Invalid original Dispatch draw count")
        gained = {}
        for _ in range(count):
            iid, amount = weighted_dispatch(rewards.get(gid, []))
            if iid not in identities() or amount <= 0:
                raise ValueError("Unsupported original Dispatch reward")
            gained[iid] = gained.get(iid, 0) + amount
        changed = {"UserExpeditionStage"}
        for iid, amount in gained.items():
            table, id_key = identities()[iid]
            resources.add(tables, table, id_key, iid, amount, oc.LOCAL_USER_ID, now)
            changed.add(table)
        for character, field, cp in updates:
            character[field] = cp
            changed.add("UserCharacter")
        info = tables.get("UserInfo", [{}])[0]
        if "playerStatus" in info:
            info["playerStatus"]["totalExp"] = info["playerStatus"].get(
                "totalExp", 0
            ) + groups[gid].number(1)
            changed.add("UserInfo")
        row.update(
            completionState=4,
            dispatchState=0,
            lastCompleteDateTime=now,
            cumulativeClearCount=row.get("cumulativeClearCount", 0) + 1,
        )
        if not row.get("firstCompleteDateTime"):
            row["firstCompleteDateTime"] = now
        for table in changed:
            state["tokens"][table] = hashlib.md5(
                oc.msgpack_value(tables[table])
            ).hexdigest()
        response = dict(
            code=0,
            drawStatus=status,
            UserExpeditionStage=copy.deepcopy(row),
            issueAmount=0,
            friendshipStates=copy.deepcopy(friendship),
            gainedItems=[
                dict(rewardId=iid, amount=n, drawType=0, isFriendshipReward=False)
                for iid, n in gained.items()
            ],
            operations=[],
        )
        ledger[key] = response
        oc.write_progress(state)
        return dispatch_receipt(state, response)


def dispatch_receipt(state, receipt):
    """Refresh old receipts too, without granting their items a second time."""
    response = copy.deepcopy(receipt)
    changed = {"UserExpeditionStage", "UserCharacter", "UserInfo"}
    for reward in receipt["gainedItems"]:
        table, _ = identities()[reward["rewardId"]]
        changed.add(table)
    response["data"] = {
        t: copy.deepcopy(state["tables"][t]) for t in changed if t in state["tables"]
    }
    response["dataTokens"] = {
        t: state["tokens"][t] for t in response["data"] if t in state["tokens"]
    }
    return response


def repair_recipes(oc):
    """Mirror native discovery: default recipe or an acquired required material."""
    with oc.PROGRESS_LOCK:
        saved = oc.load_progress()
        tables = saved["tables"]
        materials = {
            r["equipmentMaterialId"]
            for r in tables.get("UserEquipmentMaterial", [])
            if r.get("amount", 0) > 0
        }
        recipes = tables.setdefault("UserEquipmentRecipe", [])
        known = {r["equipmentRecipeId"] for r in recipes}
        now = int(time.time())
        for r in master.rows(master.master_bytes("equipmentRecipe")):
            rid = r.number(0, 8)
            if rid not in known and materials.intersection(
                (r.number(f, 8) for f in (2, 4, 6))
            ):
                recipes.append(
                    {
                        "userId": oc.LOCAL_USER_ID,
                        "equipmentRecipeId": rid,
                        "state": 1,
                        "unlockedAt": now,
                    }
                )
        if len(recipes) != len(known):
            saved["tokens"]["UserEquipmentRecipe"] = hashlib.md5(
                oc.msgpack_value(recipes)
            ).hexdigest()
            oc.write_progress(saved)
        return len(recipes) - len(known)


def complete_drops(oc, state, request):
    """Promote the original field drops reserved by BattleEnd into usable stock."""
    drops = request.get("adventureGainedItems", [])
    if not isinstance(drops, list):
        raise ValueError("Invalid adventure drops")
    from danmemo.game.event_drop_aliases import aliases as aliases

    mapping = aliases(request.get("adventureStageId"))
    amounts = {}
    reserved = {}
    for drop in drops:
        if (
            not isinstance(drop, dict)
            or type(drop.get("itemId")) is not int
            or type(drop.get("amount")) is not int
            or (not 0 <= drop["amount"] <= 2000000000)
            or (drop["itemId"] not in identities())
        ):
            raise ValueError("Unknown adventure drop")
        iid = mapping.get(drop["itemId"], drop["itemId"])
        amounts[iid] = amounts.get(iid, 0) + drop["amount"]
        reserved[drop["itemId"]] = reserved.get(drop["itemId"], 0) + drop["amount"]
    changed = set()
    for iid, amount in reserved.items():
        table, key = identities()[iid]
        row = next((r for r in state["tables"].get(table, []) if r[key] == iid), None)
        if row is not None:
            row["reservedAmount"] = max(0, row.get("reservedAmount", 0) - amount)
    for iid, amount in amounts.items():
        if not amount:
            continue
        table, key = identities()[iid]
        row = resources.add(
            state["tables"], table, key, iid, amount, oc.LOCAL_USER_ID, int(time.time())
        )
        changed.add(table)
    for table in changed:
        state["tokens"][table] = hashlib.md5(
            oc.msgpack_value(state["tables"][table])
        ).hexdigest()
    return changed


@functools.cache
def shop_products():
    return {
        r.number(0): {
            "costType": r.number(4),
            "costItemId": r.number(5, 8),
            "cost": r.number(6),
            "limit": r.number(8),
            "kind": r.number(9),
            "itemId": r.number(10, 8),
            "amount": r.number(11),
        }
        for r in master.rows(master.master_bytes("shopProduct"))
    }


@functools.cache
def shop_packs():
    packs = {}
    for r in master.rows(master.master_bytes("shopPackContent")):
        packs.setdefault(r.number(5), []).append(
            (r.number(2), r.number(3), r.number(4))
        )
    return packs


def buy(oc, request, request_id):
    pid = request.get("shopProductId")
    if type(pid) is not int or pid not in shop_products():
        raise ValueError("Unknown shop product")
    product = shop_products()[pid]
    if product["costType"] not in (0, 1, 2):
        raise ValueError("This product requires paid Iris or a local exchange")
    packed = product["kind"] == 2 or (product["kind"] == 4 and product["costType"] == 1)
    contents = shop_packs().get(product["itemId"]) if packed else None
    if contents is None:
        contents = [(product.get("kind", 0), product["itemId"], product["amount"])]
    gained = {}
    gained_costumes = []
    for kind, iid, amount in contents:
        if amount <= 0:
            raise ValueError("Unsupported original shop reward")
        if kind == 3 and iid == 0:
            continue
        if kind == 0:
            if iid not in identities():
                raise ValueError("Unsupported original shop reward")
            gained[iid] = gained.get(iid, 0) + amount * (
                product["amount"] if packed else 1
            )
        elif kind == 4:
            gained_costumes.append(iid)
        else:
            raise ValueError("Unsupported original shop reward")
    with oc.PROGRESS_LOCK:
        saved = oc.load_progress()
        ledger = saved.setdefault("shopTransactions", {})
        key = str(request_id) if request_id else None
        if key and key in ledger:
            if ledger[key]["request"] != request:
                raise ValueError("Shop transaction identity reused")
            result = dict(ledger[key]["response"])
            if len(result.get("operations", [])) == 1:
                result["operation"] = copy.deepcopy(result["operations"][0])
            result["data"] = {t: saved["tables"][t] for t in result["data"]}
            result["dataTokens"] = {t: saved["tokens"][t] for t in result["data"]}
            return result
        tables = saved["tables"]
        cost = product["cost"]
        changed = {"UserShopProduct"}
        if product["costType"] == 2:
            cost_item_id = product["costItemId"]
            key_items = tables.setdefault("UserKeyItem", [])
            item_row = next(
                (r for r in key_items if r.get("keyItemId") == cost_item_id), None
            )
            current_amount = item_row.get("amount", 0) if item_row else 0
            if current_amount < cost:
                raise ValueError("Insufficient exchange items")
            item_row["amount"] -= cost
            changed.add("UserKeyItem")
        else:
            currency = next(
                (
                    r
                    for r in tables.get("UserCurrency", [])
                    if r["currencyId"] == 205000001
                ),
                None,
            )
            free_amount = (
                currency["amount"] - currency.get("reservedAmount", 0)
                if currency
                else 0
            )
            wallet = saved.setdefault("privateIrisWallet", {"paid": 0})
            paid_amount = wallet.get("paid", 0)
            if product["costType"] == 1:
                if paid_amount < cost:
                    raise ValueError("Insufficient paid Iris")
                paid_spent, free_spent = (cost, 0)
                wallet["paid"] -= cost
            else:
                if free_amount + paid_amount < cost:
                    raise ValueError("Insufficient Iris")
                free_spent = min(free_amount, cost)
                paid_spent = cost - free_spent
                if free_spent:
                    currency["amount"] -= free_spent
                wallet["paid"] -= paid_spent
            changed.add("UserCurrency")
        purchases = tables.setdefault("UserShopProduct", [])
        purchase = next((r for r in purchases if r["shopProductId"] == pid), None)
        count = (
            purchase.get("count", purchase.get("purchaseCount", 0)) if purchase else 0
        )
        if product["limit"] and count >= product["limit"]:
            raise ValueError("Shop purchase limit reached")
        now = int(time.time())
        if purchase is None:
            purchase = {
                "userId": oc.LOCAL_USER_ID,
                "shopProductId": pid,
                "count": 0,
                "purchaseCount": 0,
                "readState": 3,
            }
            purchases.append(purchase)
        purchase.update(
            count=count + 1, purchaseCount=count + 1, lastExchangedAt=now, readState=3
        )
        for iid, amount in gained.items():
            table, id_key = identities()[iid]
            resources.add(tables, table, id_key, iid, amount, oc.LOCAL_USER_ID, now)
            changed.add(table)
        for cid in gained_costumes:
            costumes = tables.setdefault("UserCharacterCostume", [])
            row = next((r for r in costumes if r.get("costumeId") == cid), None)
            if row is None:
                costumes.append(
                    {
                        "userId": oc.LOCAL_USER_ID,
                        "costumeId": cid,
                        "costumeState": 2,
                        "receivedAt": now,
                        "isReceived": 1,
                    }
                )
            else:
                row.update(
                    costumeState=max(2, row.get("costumeState", 1)), isReceived=1
                )
            changed.add("UserCharacterCostume")
        for table in changed:
            saved["tokens"][table] = hashlib.md5(
                oc.msgpack_value(tables[table])
            ).hexdigest()
        parameters = {
            "shopProductId": pid,
            "count": count + 1,
            "lastExchangedAt": now,
            "boughtItems": [{"itemId": iid, "amount": n} for iid, n in gained.items()],
            "boughtCostumes": list(gained_costumes),
            "boughtEquipments": [],
        }
        operation = dict(
            id=secrets.randbelow(2147483646) + 1,
            type=9000,
            token=secrets.token_hex(16),
            signature=secrets.randbelow(2147483646) + 1,
            parameters=copy.deepcopy(parameters),
        )
        data = {t: copy.deepcopy(tables[t]) for t in changed if t == "UserCurrency"}
        result = dict(
            code=0,
            **parameters,
            data=data,
            dataTokens={t: saved["tokens"][t] for t in data},
            operations=[operation],
            operation=copy.deepcopy(operation),
        )
        if product["costType"] in (0, 1):
            saved.setdefault("privatePurchaseHistory", []).append(
                dict(
                    transaction_type=1,
                    date=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                    reason=f"Shop {pid}",
                    balance_change_charge=-paid_spent,
                    balance_change_free=-free_spent,
                    price=0,
                    formatted_price="",
                    expire_charge="",
                    expire_free="",
                )
            )
        if key:
            ledger[key] = {"request": request, "response": result}
        oc.write_progress(saved)
        return result


@functools.cache
def cp_master():
    labels = {
        r.number(0): r.text(1) for r in master.rows(master.master_bytes("character"))
    }
    levels = {}
    for r in master.rows(master.master_bytes("characterFriendshipLevelStep")):
        levels.setdefault(r.text(1), []).append((r.number(3), r.number(2)))
    for steps in levels.values():
        steps.sort()
    rewards = {}
    for r in master.rows(master.master_bytes("characterFriendshipReward")):
        kind = r.number(5)
        if kind in (0, 1, 5, 6, 15):
            iid = 205000001 if kind == 5 else 205000002 if kind == 6 else r.number(7)
            rewards.setdefault(r.text(1), []).append((r.number(2), iid, r.number(6)))
    return (labels, levels, rewards)


def friendship(oc, request, request_id):
    states, iid, amount = (
        request.get("friendshipStates"),
        request.get("itemId"),
        request.get("itemAmount"),
    )
    if iid is None and amount is None:
        if (
            not isinstance(states, list)
            or not states
            or any(
                (
                    not isinstance(s, dict)
                    or any(
                        (
                            type(s.get(k)) is not int
                            for k in (
                                "characterId",
                                "friendship",
                                "friendshipLv",
                                "userHeroType",
                            )
                        )
                    )
                    for s in states
                )
            )
        ):
            raise ValueError("Invalid conversation friendship state")
        return {
            "code": 0,
            "friendshipStates": states,
            "issueAmount": 0,
            "gainedItems": [],
            "data": {},
            "dataTokens": {},
            "operations": [],
        }
    if (
        not isinstance(states, list)
        or not states
        or type(iid) is not int
        or (type(amount) is not int)
        or (amount <= 0)
        or (iid not in {int(k) for k in resources.catalog()["UserCpItem"]})
    ):
        raise ValueError("Invalid CP present")
    labels, levels, rewards = cp_master()
    with oc.PROGRESS_LOCK:
        saved = oc.load_progress()
        ledger = saved.setdefault("friendshipTransactions", {})
        key = str(request_id) if request_id else None
        if key and key in ledger:
            if ledger[key]["request"] != request:
                raise ValueError("CP transaction identity reused")
            response = dict(ledger[key]["response"])
            response["data"] = {
                table: saved["tables"][table] for table in response["data"]
            }
            response["dataTokens"] = {
                table: saved["tokens"][table] for table in response["data"]
            }
            return response
        tables = saved["tables"]
        stock = next(
            (r for r in tables.get("UserCpItem", []) if r["cpItemId"] == iid), None
        )
        if stock is None or stock["amount"] < amount:
            raise ValueError("Insufficient presents")
        updates, gained = ([], {})
        seen = set()
        for entry in states:
            if not isinstance(entry, dict) or any(
                (
                    type(entry.get(k)) is not int
                    for k in (
                        "characterId",
                        "friendship",
                        "friendshipLv",
                        "userHeroType",
                    )
                )
            ):
                raise ValueError("Invalid friendship state")
            cid, cp, hero = (
                entry["characterId"],
                entry["friendship"],
                entry["userHeroType"],
            )
            row = next(
                (r for r in tables.get("UserCharacter", []) if r["characterId"] == cid),
                None,
            )
            if (
                row is None
                or cid not in labels
                or hero not in (0, 1, 2)
                or (cid in seen)
            ):
                raise ValueError("Unknown or repeated friendship character")
            seen.add(cid)
            field = ("friendship", "friendshipAis", "friendshipLef")[hero]
            old = row.get(field, 0)
            steps = levels[labels[cid]]
            level = lambda value: max(
                (lv for threshold, lv in steps if threshold <= value)
            )
            if cp < old or cp > 2000000000 or entry["friendshipLv"] != level(cp):
                raise ValueError("Invalid friendship progression")
            for lv, reward_id, count in rewards.get(labels[cid], []):
                if level(old) < lv <= level(cp):
                    gained[reward_id] = gained.get(reward_id, 0) + count
            updates.append((row, field, cp))
        if any((reward_id not in identities() for reward_id in gained)):
            raise ValueError("Unknown original CP reward")
        stock["amount"] -= amount
        changed = {"UserCpItem", "UserCharacter"}
        for row, field, cp in updates:
            row[field] = cp
        now = int(time.time())
        for reward_id, count in gained.items():
            table, id_key = identities()[reward_id]
            resources.add(
                tables, table, id_key, reward_id, count, oc.LOCAL_USER_ID, now
            )
            changed.add(table)
        for table in changed:
            saved["tokens"][table] = hashlib.md5(
                oc.msgpack_value(tables[table])
            ).hexdigest()
        result = {
            "code": 0,
            "friendshipStates": states,
            "issueAmount": 0,
            "gainedItems": [{"itemId": rid, "amount": n} for rid, n in gained.items()],
            "data": {table: tables[table] for table in changed},
            "dataTokens": {table: saved["tokens"][table] for table in changed},
            "operations": [],
        }
        if key:
            ledger[key] = {"request": request, "response": result}
        oc.write_progress(saved)
        return result
