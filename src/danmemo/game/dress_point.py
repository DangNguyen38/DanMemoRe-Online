"""Outfit Refinement contract recovered from the original native repository.

Native 0x25797dc sends pcId/befDpLv/aftDpLv; 0x25786e4 adds
currentDP + battlePlayCount + brush points, capped at 2,000,000,000.
The success callbacks apply PC/brush changes locally, so those post-change
tables must not be returned before the callback and applied twice.
"""

import copy
import functools
import hashlib
import time
from danmemo.master import reader as master
from danmemo.game import resources as resources


@functools.cache
def catalog():
    steps = tuple(
        sorted(
            (
                dict(
                    id=r.number(0),
                    level=r.number(1),
                    points=r.number(2),
                    label=r.text(4),
                    amount=r.number(5),
                )
                for r in master.rows(master.master_bytes("pcDPLevelStep"))
            ),
            key=lambda r: r["level"],
        )
    )
    brushes = {
        r.number(0): r.number(5) for r in master.rows(master.master_bytes("dpItem"))
    }
    currencies = {
        r.text(1): r.number(0) for r in master.rows(master.master_bytes("currency"))
    }
    return (steps, brushes, currencies)


def level(points):
    return max((r["level"] for r in catalog()[0] if r["points"] <= points))


def complete(oc, request, request_id=None):
    entries = request.get("dpStates")
    iid = request.get("itemId", 0)
    amount = request.get("itemAmount", 0)
    steps, brushes, currencies = catalog()
    if (
        not isinstance(entries, list)
        or len(entries) > 12
        or type(iid) is not int
        or (type(amount) is not int)
        or (amount < 0)
        or (amount > 20000000)
        or ((iid == 0) != (amount == 0))
        or (iid and iid not in brushes)
        or (amount and len(entries) > 1)
        or (not entries and (not amount))
    ):
        raise ValueError("Invalid Outfit Refinement request")
    with oc.PROGRESS_LOCK:
        state = copy.deepcopy(oc.load_progress())
        tables = state["tables"]
        ledger = state.setdefault("dressPointTransactions", {})
        key = str(request_id) if request_id else None
        if key and key in ledger:
            prior = ledger[key]
            if prior["request"] != request:
                raise ValueError("Outfit transaction identity reused")
            response = copy.deepcopy(prior["response"])
            response["data"] = {t: tables.get(t, []) for t in response["data"]}
            response["dataTokens"] = {t: state["tokens"][t] for t in response["data"]}
            return response
        stock = None
        if amount:
            stock = next(
                (r for r in tables.get("UserDpItem", []) if r.get("dpItemId") == iid),
                None,
            )
            if stock is None or stock.get("amount", 0) < amount:
                raise ValueError("Insufficient Perseus Brushes")
        paid = state.setdefault("dressPointRewardLevels", {})
        seen = set()
        updates = []
        gained = {}
        received = []
        for entry in entries:
            if not isinstance(entry, dict) or any(
                (type(entry.get(k)) is not int for k in ("pcId", "befDpLv", "aftDpLv"))
            ):
                raise ValueError("Invalid Outfit level state")
            pid = entry["pcId"]
            before = entry["befDpLv"]
            after = entry["aftDpLv"]
            pc = next((r for r in tables.get("UserPC", []) if r["pcId"] == pid), None)
            if pc is None or pid in seen:
                raise ValueError("Unknown or repeated Outfit character")
            seen.add(pid)
            old = pc.get("currentDP", 0)
            pending = pc.get("battlePlayCount", 0)
            if (
                type(old) is not int
                or type(pending) is not int
                or min(old, pending) < 0
            ):
                raise ValueError("Invalid saved Outfit points")
            total = min(2000000000, old + pending + brushes.get(iid, 0) * amount)
            previous = max(level(old), paid.get(str(pid), 1))
            if not amount and 1 <= before < after <= previous:
                continue
            if before != level(old) or after != level(total) or after <= before:
                raise ValueError(
                    "Outfit levels do not match recorded participation points"
                )
            for step in steps:
                if previous < step["level"] <= after and step["amount"] > 0:
                    currency = currencies.get(step["label"])
                    if currency is None:
                        raise ValueError("Unknown original Outfit reward")
                    gained[currency] = gained.get(currency, 0) + step["amount"]
                    received.append(step["id"])
            updates.append((pc, total, after))
        changed = set()
        for pc, total, after in updates:
            pc.update(currentDP=total, battlePlayCount=0)
            paid[str(pc["pcId"])] = max(paid.get(str(pc["pcId"]), 1), after)
            changed.add("UserPC")
        if stock is not None:
            stock["amount"] -= amount
            changed.add("UserDpItem")
        now = int(time.time())
        for currency, count in gained.items():
            resources.add(
                tables,
                "UserCurrency",
                "currencyId",
                currency,
                count,
                oc.LOCAL_USER_ID,
                now,
            )
            changed.add("UserCurrency")
        for table in changed:
            state["tokens"][table] = hashlib.md5(
                oc.msgpack_value(tables[table])
            ).hexdigest()
        visible = {"UserCurrency"} if gained else set()
        response = dict(
            code=0,
            issueAmount=sum(gained.values()),
            receivedDPRewardIds=received,
            dpStates=entries,
            itemId=iid,
            itemAmount=amount,
            gainedItems=[dict(itemId=i, amount=n) for i, n in gained.items()],
            data={t: tables[t] for t in visible},
            dataTokens={t: state["tokens"][t] for t in visible},
            operations=[],
        )
        if key:
            ledger[key] = dict(request=request, response=response)
        oc.write_progress(state)
        return response
