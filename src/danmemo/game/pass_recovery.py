"""Snapshot-server adapter for original fixed-cost Iris recovery callbacks.

Recovered client rules: 2544958 WG consumption, 2544488 timer, 25463b4 RB
calendar, 254169c/25418e8/2541b00 recovery. The durable receipt/reconciliation
is a private-server reconstruction, not recovered publisher server code.
"""

import copy
import functools
import hashlib
import secrets
import time

STAMPS = {
    "baseSP": "baseSPUpdatedAt",
    "pvpSP": "pvpSPUpdatedAt",
    "pvbSP": "pvbSPLastResetAt",
    "serialPvpSP": "serialPvpSPUpdatedAt",
    "graidSP": "graidSPLastResetAt",
}


def project_war(status, now):
    stock, stamp = (status.get("pvpSP", 0), status.get("pvpSPUpdatedAt", now))
    if stock < 10:
        ticks = max(0, now - stamp) // 1800
        if ticks:
            stock = min(10, stock + ticks)
            stamp += ticks * 1800
    return (stock, stamp)


def consume_war(status, now):
    stock, stamp = project_war(status, now)
    if stock < 1:
        raise ValueError("No War Game passes available")
    status.update(pvpSP=stock - 1, pvpSPUpdatedAt=now if stock >= 10 else stamp)


@functools.cache
def consumes():
    from danmemo.master import reader as master

    return {
        r.number(0): {"4": r.text(4), "5": r.number(5, 8), "7": r.number(7)}
        for r in master.rows(master.master_bytes("gamelibConsume"))
    }


def observe(state, incoming, now):
    """Recognize the native save once, including its independently reset RB day."""
    for receipt in state.get("irisRecoveryReceipts", {}).values():
        field = receipt.get("field")
        if (
            not field
            or receipt.get("observed")
            or receipt.get("settled")
            or (incoming.get(field) != receipt["amount"])
        ):
            continue
        key = STAMPS[field]
        stamp = incoming.get(key)
        exact = stamp == receipt["stamp"]
        rb = (
            field == "pvbSP"
            and type(stamp) is int
            and (receipt["beforeStamp"] <= stamp <= now)
            and ((stamp + 32400) // 86400 == (receipt["stamp"] + 32400) // 86400)
        )
        if exact or rb:
            receipt["observed"] = True
            state.get("arenaStatusReconciliation", {}).pop(field, None)


def target(oc, state, consume_id, now):
    status = state["tables"]["UserInfo"][0]["playerStatus"]
    if consume_id == 81041001:
        from danmemo.game.progression import levels as levels

        cap = next((r[2] for r in levels() if r[1] == status["level"]))
        stock = status["baseSP"]
        stamp = status.get("baseSPUpdatedAt", now)
        if stock < cap:
            ticks = max(0, now - stamp) // 180
            stock = min(cap, stock + ticks)
            stamp += ticks * 180
        if stock + cap > 2147483647:
            raise ValueError("Stamina capacity exceeded")
        return (10200, "baseSP", stock + cap, stamp)
    if consume_id == 81051001:
        stock, stamp = project_war(status, now)
        if stock >= 15:
            raise ValueError("War Game passes already full")
        return (10400, "pvpSP", min(15, stock + 5), stamp)
    if consume_id == 81061001:
        stock = status["pvbSP"]
        stamp = status.get("pvbSPLastResetAt", now)
        if now > stamp and (now + 32400) // 86400 != (stamp + 32400) // 86400:
            stock = max(5, stock)
            stamp = now
        if stock >= 10:
            raise ValueError("Record Buster passes already full")
        return (10500, "pvbSP", min(10, stock + 5), stamp)
    if consume_id == 81071001:
        from danmemo.config import modes as modes
        from danmemo.game import arena as arena

        key = "ultimate_war_game"
        setting = modes.config(
            "online" if getattr(oc, "ONLINE_SHARED", None) else "offline"
        )["modes"].get(key)
        if not setting or not setting.get("start", 0) <= now < setting.get("end", 0):
            raise ValueError("Telskyura Abyss is closed")
        info = arena.season("serialPvpInformation", modes.selected_season(key, setting))
        rule = arena.season("serialPvpRule", info.number(9, 8))
        natural, cap, interval = (rule.number(2), rule.number(3), rule.number(4))
        if min(natural, cap, interval) <= 0:
            raise ValueError("Missing original Abyss pass rule")
        stock = status["serialPvpSP"]
        stamp = status.get("serialPvpSPUpdatedAt", now)
        if stamp < setting["start"]:
            stock = rule.number(1)
            stamp = setting["start"]
        ticks = max(0, now - stamp) // interval
        if ticks:
            stock = min(natural, stock + ticks)
            stamp += ticks * interval
        if stock >= cap:
            raise ValueError("Telskyura Abyss passes already full")
        return (
            10600,
            "serialPvpSP",
            min(cap, stock + consumes()[consume_id]["7"]),
            stamp,
        )
    if consume_id == 81021001:
        return (10100, None, None, None)
    raise ValueError("Unsupported original Iris consume contract")


def recover(oc, request, request_id):
    if set(request) != {"consumeId"} or type(request["consumeId"]) is not int:
        raise ValueError("Invalid fixed-cost Iris recovery request")
    spec = consumes().get(request["consumeId"], {})
    cost = spec.get("5")
    if type(cost) is not int or cost <= 0:
        raise ValueError("Missing original Iris price")
    if not request_id:
        raise ValueError("Missing recovery transaction identity")
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        ledger = state.setdefault("irisRecoveryReceipts", {})
        aliases = state.setdefault("irisRecoveryRequestAliases", {})
        reconciled = False
        status = state["tables"]["UserInfo"][0]["playerStatus"]
        for receipt in ledger.values():
            field = receipt.get("field")
            if (
                field
                and field in status
                and (not receipt.get("observed"))
                and (not receipt.get("settled"))
                and (field not in state.get("arenaStatusReconciliation", {}))
            ):
                receipt["settled"] = "authoritative_snapshot_advanced"
                reconciled = True

        def reply(receipt):
            result = copy.deepcopy(receipt["response"])
            if receipt.get("observed") or receipt.get("settled"):
                result.pop("operation", None)
                result["operations"] = []
            elif len(result.get("operations", [])) == 1:
                result["operation"] = copy.deepcopy(result["operations"][0])
            result["data"] = {
                "UserCurrency": copy.deepcopy(state["tables"].get("UserCurrency", []))
            }
            result["dataTokens"] = {"UserCurrency": state["tokens"]["UserCurrency"]}
            return result

        previous = ledger.get(aliases.get(str(request_id), str(request_id)))
        if previous:
            if previous["request"] != request:
                raise ValueError("Conflicting Iris recovery retry")
            if reconciled:
                oc.write_progress(state)
            return reply(previous)
        pending = next(
            (
                (key, r)
                for key, r in ledger.items()
                if r.get("field")
                and r["request"] == request
                and (not r.get("observed"))
                and (not r.get("settled"))
            ),
            None,
        )
        if pending:
            key, receipt = pending
            if receipt["request"] != request:
                raise ValueError("Conflicting pending Iris recovery")
            aliases[str(request_id)] = key
            oc.write_progress(state)
            return reply(receipt)
        now = int(time.time())
        op, field, amount, stamp = target(oc, state, request["consumeId"], now)
        before_stamp = (
            state["tables"]["UserInfo"][0]["playerStatus"].get(STAMPS.get(field), stamp)
            if field
            else None
        )
        currency = next(
            (
                r
                for r in state["tables"].get("UserCurrency", [])
                if r["currencyId"] == 205000001
            ),
            None,
        )
        free = (
            max(0, currency.get("amount", 0) - currency.get("reservedAmount", 0))
            if currency
            else 0
        )
        wallet = state.setdefault("privateIrisWallet", {"paid": 0})
        paid = wallet.get("paid", 0)
        if free + paid < cost:
            raise ValueError("Insufficient Iris")
        spent_free = min(free, cost)
        spent_paid = cost - spent_free
        if spent_free:
            currency["amount"] -= spent_free
        wallet["paid"] -= spent_paid
        if field:
            status = state["tables"]["UserInfo"][0]["playerStatus"]
            status[field] = amount
            status[STAMPS[field]] = stamp
            state.setdefault("arenaStatusReconciliation", {})[field] = stamp
            state["tokens"]["UserInfo"] = hashlib.md5(
                oc.msgpack_value(state["tables"]["UserInfo"])
            ).hexdigest()
        state["tokens"]["UserCurrency"] = hashlib.md5(
            oc.msgpack_value(state["tables"].get("UserCurrency", []))
        ).hexdigest()
        operation = dict(
            id=secrets.randbelow(2147483646) + 1,
            type=op,
            token=secrets.token_hex(16),
            signature=secrets.randbelow(2147483646) + 1,
            parameters={},
        )
        response = dict(
            code=0,
            operation=copy.deepcopy(operation),
            operations=[operation],
            data={
                "UserCurrency": copy.deepcopy(state["tables"].get("UserCurrency", []))
            },
            dataTokens={"UserCurrency": state["tokens"]["UserCurrency"]},
        )
        ledger[str(request_id)] = dict(
            request=copy.deepcopy(request),
            response=copy.deepcopy(response),
            at=now,
            field=field,
            amount=amount,
            stamp=stamp,
            beforeStamp=before_stamp,
            observed=False,
        )
        state.setdefault("privatePurchaseHistory", []).append(
            dict(
                transaction_type=1,
                date=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                reason=spec["4"].strip(),
                balance_change_charge=-spent_paid,
                balance_change_free=-spent_free,
                price=0,
                formatted_price="",
                expire_charge="",
                expire_free="",
            )
        )
        oc.write_progress(state)
        return response
