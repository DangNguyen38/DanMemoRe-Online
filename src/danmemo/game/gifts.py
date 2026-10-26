"""Admin mailbox reconstruction using the original UserGift/receive wire contract."""

import copy
import hashlib
import time
from danmemo.game import resources as resources


def stamp(oc, state):
    rows = state["tables"].get("UserGift", [])
    state["tokens"]["UserGift"] = hashlib.md5(oc.msgpack_value(rows)).hexdigest()


def repair_legacy_enums(oc):
    """Repair only our known admin gifts, preserving receipt and inventory.

    Original UserGift constructors default sender/acquisition enums to 1.
    In particular acquisition enum 0 fails the native startup validator.
    """
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        changed = [
            row
            for row in state["tables"].get("UserGift", [])
            if str(row["id"]) in state.get("adminGiftItems", {})
            and (row.get("senderType") == 0 or row.get("giftAcquiredType") == 0)
        ]
        if not changed:
            return 0
        for row in changed:
            for field in ("senderType", "giftAcquiredType"):
                if row.get(field) == 0:
                    row[field] = 1
        stamp(oc, state)
        oc.write_progress(state)
        record_delivery(oc.LOCAL_USER_ID, changed, action="gift/repair-enums")
        return len(changed)


def record_delivery(uid, rows, action="gift/enqueue"):
    from danmemo.services import journal as test_journal

    test_journal.emit(
        "admin-mail", action, uid=uid, response={"code": 0, "UserGift": rows}
    )


def enqueue(oc, state, uid, table, iid, amount, now=None):
    now = int(time.time()) if now is None else now
    if table == "UserCurrency" and iid == 205000006:
        table = "CasinoMedal"
    virtual = table in ("CasinoMedal", "LottoTicket")
    if not virtual:
        from danmemo.game.transactions import identities as identities

        identity = identities().get(iid)
        if identity is None or identity[0] != table:
            from danmemo.game.login_bonuses import known_item as known_item

            if not known_item(table, iid) and str(iid) not in resources.catalog().get(
                table, {}
            ):
                raise ValueError("Unknown original gift item")
    if type(amount) is not int or not 0 < amount <= 1000000000:
        raise ValueError("Invalid gift amount")
    rows = state["tables"].setdefault("UserGift", [])
    if virtual:
        from danmemo.game import casino_lotto as casino_lotto

        stock, limit = casino_lotto.virtual_gift_stock(state, table, iid)
    else:
        key = {"UserPC": "pcId", "UserCharacterCostume": "costumeId"}.get(
            table
        ) or resources.identity_key(table)
        item = next((r for r in state["tables"].get(table, []) if r[key] == iid), {})
        stock = resources.stock(table, item)
        limit = resources.inventory_limit(table)
    queued = sum(
        (
            r["amount"]
            for r in rows
            if r["state"] == 0 and r["expireAt"] > now and (r["itemId"] == iid)
        )
    )
    if stock + queued + amount > limit:
        raise ValueError("Gift would exceed inventory limit")
    identifier = (
        max(
            state.get("lastAdminGiftId", 0),
            max((r["id"] for r in rows), default=0),
            now * 1000,
        )
        + 1
    )
    gift = dict(
        userId=uid,
        id=identifier,
        senderType=1,
        senderId=0,
        title="Admin test resources",
        message="Resources sent by the server administrator.",
        giftAcquiredType=1,
        startAt=now,
        expireAt=now + 30 * 86400,
        state=0,
        createdAt=now,
        itemId=iid,
        amount=amount,
        equipmentId=0,
        equipmentEvolveCount=0,
        pcLimitBreakCount=0,
        pcHeroSublimateCount=0,
        dynamodbExpireAt=now + 60 * 86400,
        itemLevel=0,
    )
    rows.append(gift)
    state["lastAdminGiftId"] = identifier
    state.setdefault("adminGiftItems", {})[str(identifier)] = table
    stamp(oc, state)
    return gift


def receive(oc, request):
    ids = request.get("giftIds")
    if (
        request.get("userId", oc.LOCAL_USER_ID) != oc.LOCAL_USER_ID
        or not isinstance(ids, list)
        or (not ids)
        or (len(ids) > 1000)
        or any((type(i) is not int for i in ids))
        or (len(set(ids)) != len(ids))
    ):
        raise ValueError("Invalid gift selection")
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        rows = {r["id"]: r for r in state["tables"].get("UserGift", [])}
        if any(
            (
                i not in rows or str(i) not in state.get("adminGiftItems", {})
                for i in ids
            )
        ):
            raise ValueError("Unknown admin gift")
        now = int(time.time())
        pending = [rows[i] for i in ids if rows[i]["state"] != 1]
        if any(
            (
                r["state"] != 0 or not r["startAt"] <= now < r["expireAt"]
                for r in pending
            )
        ):
            raise ValueError("Gift is not available")
        state = copy.deepcopy(state)
        rows = {r["id"]: r for r in state["tables"].get("UserGift", [])}
        pending = [rows[i] for i in ids if rows[i]["state"] != 1]
        inventory = state["tables"]
        for gift in pending:
            if (
                gift["itemId"] == 205000006
                and state["adminGiftItems"][str(gift["id"])] == "UserCurrency"
            ):
                state["adminGiftItems"][str(gift["id"])] = "CasinoMedal"
        changed = set()
        received = []
        received_pcs = []
        received_costumes = []
        issue_medals = 0
        for gift in pending:
            table = state["adminGiftItems"][str(gift["id"])]
            if table in ("UserPC", "UserCharacterCostume"):
                if gift["amount"] != 1:
                    raise ValueError("Invalid unique login gift quantity")
                iid = gift["itemId"]
                if table == "UserCharacterCostume":
                    rows = inventory.setdefault(table, [])
                    costume = next((r for r in rows if r["costumeId"] == iid), None)
                    if costume is None:
                        rows.append(
                            dict(
                                userId=oc.LOCAL_USER_ID,
                                costumeId=iid,
                                costumeState=2,
                                receivedAt=now,
                                isReceived=1,
                            )
                        )
                    else:
                        costume.update(
                            costumeState=max(2, costume.get("costumeState", 1)),
                            isReceived=1,
                        )
                    received_costumes.append(iid)
                else:
                    from danmemo.game import engine as client

                    job, hp, mp, bond = client.offline_lotteries()["pcs"][str(iid)]
                    if any(
                        (r["pcId"] == iid for r in inventory.setdefault("UserPC", []))
                    ):
                        resources.add(
                            inventory,
                            "UserLimitBreakItem",
                            "limitBreakItemId",
                            bond,
                            1,
                            oc.LOCAL_USER_ID,
                            now,
                        )
                        changed.add("UserLimitBreakItem")
                        state.setdefault("giftInventoryReconciliation", {}).setdefault(
                            "UserLimitBreakItem", []
                        ).append(bond)
                        received.append(dict(itemId=bond, amount=1))
                    else:
                        pc = client.user_pc(iid, job, hp, mp)
                        pc["userId"] = oc.LOCAL_USER_ID
                        inventory["UserPC"].append(pc)
                        received_pcs.append(
                            dict(pcId=iid, pcLimitBreakCount=0, pcHeroSublimateCount=0)
                        )
                changed.add(table)
                continue
            if table in ("CasinoMedal", "LottoTicket"):
                from danmemo.game import casino_lotto as casino_lotto

                state["tables"] = inventory
                casino_lotto.ensure_tables(oc, state)
                stock, limit = casino_lotto.virtual_gift_stock(
                    state, table, gift["itemId"]
                )
                if stock + gift["amount"] > limit:
                    raise ValueError("Gift would exceed Casino/Lotto limit")
                if table == "CasinoMedal":
                    casino = inventory["UserCasinoState"][0]
                    casino["medalAmount"] += gift["amount"]
                    casino["cumulativeMedalAmount"] += gift["amount"]
                    state["casinoGiftReconciliation"] = {
                        k: casino[k] for k in ("medalAmount", "cumulativeMedalAmount")
                    }
                    changed.add("UserCasinoState")
                    issue_medals += gift["amount"]
                else:
                    awards = state.setdefault("lottoTicketAwards", {})
                    key = str(gift["itemId"])
                    awards[key] = awards.get(key, 0) + gift["amount"]
                    casino_lotto.ensure_tables(oc, state)
                    changed.add("UserTakarakujiInfo")
                    received.append(dict(itemId=gift["itemId"], amount=gift["amount"]))
                continue
            key = resources.identity_key(table)
            old = next(
                (r for r in inventory.get(table, []) if r[key] == gift["itemId"]), {}
            )
            old_stock = resources.stock(table, old)
            if old_stock + gift["amount"] > resources.inventory_limit(table):
                raise ValueError("Gift would exceed inventory limit")
            item = resources.add(
                inventory,
                table,
                key,
                gift["itemId"],
                gift["amount"],
                oc.LOCAL_USER_ID,
                now,
            )
            stock = resources.stock(table, item)
            if stock > resources.inventory_limit(table):
                raise ValueError("Gift would exceed inventory limit")
            changed.add(table)
            received.append(dict(itemId=gift["itemId"], amount=gift["amount"]))
        for table in changed:
            state["tables"][table] = inventory[table]
            state["tokens"][table] = hashlib.md5(
                oc.msgpack_value(inventory[table])
            ).hexdigest()
        for gift in pending:
            table = state["adminGiftItems"][str(gift["id"])]
            if table in (
                "CasinoMedal",
                "LottoTicket",
                "UserPC",
                "UserCharacterCostume",
            ):
                continue
            pending_ids = state.setdefault(
                "giftInventoryReconciliation", {}
            ).setdefault(table, [])
            if gift["itemId"] not in pending_ids:
                pending_ids.append(gift["itemId"])
        for gift in pending:
            gift["state"] = 1
        stamp(oc, state)
        oc.write_progress(state)
        return dict(
            code=0,
            receivedItems=received,
            UserGift=[r for r in state["tables"]["UserGift"] if r["id"] in ids],
            receivedEquipments=[],
            receivedExPCs=received_pcs,
            receivedCostumes=received_costumes,
            UserCommunicationCharacter=[],
            UserUnionRaidMedal=[],
            issueCasinoMedal=issue_medals,
            data={},
            dataTokens={},
            operations=[],
        )
