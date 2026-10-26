"""Archived event-box pools and account-local draws matching the native protocol."""

import functools
import hashlib
import html
import secrets
import time
from danmemo.master import reader as master


@functools.cache
def catalog():
    inventories = {}
    for name in (
        "currency",
        "statusBoardItem",
        "expItem",
        "staminaTicket",
        "gachaTicket",
        "equipmentMaterial",
        "keyItem",
        "voteItem",
        "cpItem",
        "eventItem",
    ):
        key = name + "Id"
        for row in master.rows(master.master_bytes(name)):
            inventories[row.text(1)] = (
                "User" + name[0].upper() + name[1:],
                key,
                row.number(0),
            )
    boxes = {}
    for row in master.rows(master.master_bytes("eventBox")):
        boxes[row.number(0)] = {
            "code": row.text(2),
            "cost": row.number(4),
            "currency": inventories[row.text(3)],
            "steps": {},
        }
    by_code = {b["code"]: b for b in boxes.values()}
    steps = {}
    for row in master.rows(master.master_bytes("eventBoxStep")):
        step = {
            "id": row.number(0),
            "index": row.number(3),
            "rateIncreaseLimit": row.number(5),
            "rateIncrease": row.number(6),
            "contents": [],
        }
        by_code[row.text(2)]["steps"][step["id"]] = step
        steps[row.text(1)] = step
    for row in master.rows(master.master_bytes("eventBoxContent")):
        steps[row.text(1)]["contents"].append(
            {
                "id": row.number(0),
                "resource": inventories[row.text(3)],
                "amount": row.number(4),
                "limited": bool(row.number(5)),
                "limit": row.number(6),
                "resetPrize": bool(row.number(7)),
                "weight": row.number(8),
            }
        )
    return boxes


@functools.cache
def event_resources():
    items = {
        r.text(1): r.number(0) for r in master.rows(master.master_bytes("eventItem"))
    }
    shops = {
        r.text(1): [r.text(i) for i in (6, 7, 8)]
        for r in master.rows(master.master_bytes("shop"))
    }
    boxes = {r.text(2): r.text(3) for r in master.rows(master.master_bytes("eventBox"))}
    result = {}
    for episode in master.rows(master.master_bytes("adventureEpisode")):
        codes = shops.get(episode.text(15), []) + [boxes.get(episode.text(23), "")]
        result[episode.number(0, 8)] = sorted(
            {items[code] for code in codes if code in items}
        )
    return result


def repair_resources(oc):
    """Initialize linked event currencies before native item icons resolve them."""
    required = {
        iid
        for eid in oc.schedule_module().config()["events"]
        for iid in event_resources().get(int(eid), [])
    }
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        inventory = state["tables"].setdefault("UserEventItem", [])
        existing = {r["eventItemId"] for r in inventory}
        missing = required - existing
        for iid in sorted(missing):
            inventory.append(
                {
                    "userId": oc.LOCAL_USER_ID,
                    "eventItemId": iid,
                    "amount": 0,
                    "reservedAmount": 0,
                    "cumulativeAmount": 0,
                    "signature": 0,
                    "bookEntryState": 1,
                }
            )
        changed = {"UserEventItem"} if missing else set()
        for row in state["tables"].get("UserEventBoxStep", []):
            if type(row.get("boost")) is int and row["boost"] in (0, 1):
                row["boost"] = bool(row["boost"])
                changed.add("UserEventBoxStep")
        if changed:
            for table in changed:
                state["tokens"][table] = hashlib.md5(
                    oc.msgpack_value(state["tables"][table])
                ).hexdigest()
            oc.write_progress(state)
        return len(missing)


def contents(step, saved, draw_count=0):
    """Apply this round's master rule after each draw, not each draw request."""
    picked = {
        r["eventBoxContentId"]: r["pickedAmount"]
        for r in saved
        if r["eventBoxStepId"] == step["id"]
    }
    weights = [
        (
            c,
            c["weight"]
            if not c["limited"] or picked.get(c["id"], 0) < c["limit"]
            else 0,
        )
        for c in step["contents"]
    ]
    total = sum((w for _, w in weights))
    increase = (
        min(max(0, draw_count), step.get("rateIncreaseLimit", 0))
        * step.get("rateIncrease", 0)
        / 10000
    )
    if total and increase:
        probabilities = [(c, w / total) for c, w in weights]
        finite = [(c, p) for c, p in probabilities if c["limited"] and p > 0]
        infinite = sum((p for c, p in probabilities if not c["limited"]))
        addition = min(increase, infinite / len(finite)) if finite else 0
        weights = [
            (
                c,
                p + addition
                if c["limited"] and p > 0
                else p * max(0, 1 - addition * len(finite) / infinite)
                if infinite and (not c["limited"])
                else p,
            )
            for c, p in probabilities
        ]
        total = sum((w for _, w in weights))
    return (
        weights,
        [
            {
                "contentId": c["id"],
                "rate": w / total if total else 0.0,
                "rateVariationState": 2,
            }
            for c, w in weights
        ],
    )


def draws_for_step(step, saved, step_rows):
    row = next((r for r in step_rows if r["eventBoxStepId"] == step["id"]), {})
    return max(
        0,
        row.get("drawCount", 0),
        sum(
            (
                r.get("pickedAmount", 0)
                for r in saved
                if r["eventBoxStepId"] == step["id"]
            )
        ),
    )


def response(oc, action, request, request_id=None):
    box_id = request.get("eventBoxId")
    if type(box_id) is not int or box_id not in catalog():
        raise ValueError("Unknown event box")
    box = catalog()[box_id]
    linked = [
        r
        for r in master.rows(master.master_bytes("adventureEpisode"))
        if r.text(23) == box["code"]
    ]
    if not any(
        (oc.schedule_module().available("events", r.number(0, 8)) for r in linked)
    ):
        raise ValueError("Event box is outside its scheduled period")
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        ledger = state.setdefault("eventBoxDraws", {})
        ledger_key = (
            f"{box_id}:{state.get('eventBoxSession', 'initial')}:{request_id}"
            if request_id
            else None
        )
        if action == "draw" and ledger_key in ledger:
            cached = ledger[ledger_key]
            if cached["request"] != request:
                raise ValueError("Event box request ID reused with different data")
            return cached["response"]
        tables = state["tables"]
        initialized = "UserEventBoxContent" not in tables
        user_boxes = tables.setdefault("UserEventBox", [])
        user_box = next((r for r in user_boxes if r["eventBoxId"] == box_id), None)
        first = min(box["steps"], key=lambda sid: box["steps"][sid]["index"])
        if user_box is None:
            user_box = {
                "userId": oc.LOCAL_USER_ID,
                "eventBoxId": box_id,
                "currentStepId": first,
                "totalDrawCount": 0,
            }
            user_boxes.append(user_box)
            initialized = True
        step_id = request.get("eventBoxStepId", user_box["currentStepId"])
        if type(step_id) is not int or step_id not in box["steps"]:
            raise ValueError("Invalid event box step")
        if action == "draw" and step_id != user_box["currentStepId"]:
            raise ValueError("Cannot draw from an inactive event box step")
        step = box["steps"][step_id]
        saved = tables.setdefault("UserEventBoxContent", [])
        step_rows = tables.setdefault("UserEventBoxStep", [])
        draw_count = draws_for_step(step, saved, step_rows)
        result = {
            "code": 0,
            "currentStepContents": contents(step, saved, draw_count)[1],
        }
        if action == "draw":
            count = request.get("drawCount")
            if type(count) is not int or not 1 <= count <= 100:
                raise ValueError("Invalid event box draw count")
            table, key, item_id = box["currency"]
            inventory = tables.setdefault(table, [])
            currency = next((r for r in inventory if r[key] == item_id), None)
            cost = box["cost"] * count
            if (
                currency is None
                or currency["amount"] - currency.get("reservedAmount", 0) < cost
            ):
                raise ValueError("Insufficient event box currency")
            currency["amount"] -= cost
            chosen, changed = (
                [],
                {table, "UserEventBox", "UserEventBoxContent", "UserEventBoxStep"},
            )
            rng = secrets.SystemRandom()
            for _ in range(count):
                weights, _ = contents(step, saved, draw_count)
                if not sum((w for _, w in weights)):
                    raise ValueError("Event box is exhausted")
                prize = rng.choices(
                    [c for c, _ in weights], weights=[w for _, w in weights]
                )[0]
                row = next(
                    (r for r in saved if r["eventBoxContentId"] == prize["id"]), None
                )
                if row is None:
                    row = {
                        "userId": oc.LOCAL_USER_ID,
                        "eventBoxContentId": prize["id"],
                        "eventBoxStepId": step_id,
                        "pickedAmount": 0,
                        "lappedCount": 0,
                        "rateVariationState": 2,
                    }
                    saved.append(row)
                row["pickedAmount"] += 1
                draw_count += 1
                reward_table, reward_key, reward_id = prize["resource"]
                rewards = tables.setdefault(reward_table, [])
                reward = next((r for r in rewards if r[reward_key] == reward_id), None)
                if reward is None:
                    reward = {
                        "userId": oc.LOCAL_USER_ID,
                        reward_key: reward_id,
                        "amount": 0,
                        "reservedAmount": 0,
                        "cumulativeAmount": 0,
                        "signature": 0,
                        "bookEntryState": 1,
                    }
                    rewards.append(reward)
                if reward_table == "UserGachaTicket":
                    dates = reward.setdefault(
                        "dateList",
                        [
                            {"aquiredAt": int(time.time())}
                            for _ in range(reward.get("amount", 0))
                        ],
                    )
                    dates.extend(
                        (
                            {"aquiredAt": int(time.time())}
                            for _ in range(prize["amount"])
                        )
                    )
                    reward.pop("amount", None)
                    reward.pop("reservedAmount", None)
                else:
                    reward["amount"] += prize["amount"]
                reward["cumulativeAmount"] = (
                    reward.get("cumulativeAmount", 0) + prize["amount"]
                )
                changed.add(reward_table)
                chosen.append(
                    {
                        "eventBoxContentId": prize["id"],
                        "itemId": reward_id,
                        "amount": prize["amount"],
                    }
                )
            step_row = next(
                (r for r in step_rows if r["eventBoxStepId"] == step_id), None
            )
            if step_row is None:
                step_row = {
                    "userId": oc.LOCAL_USER_ID,
                    "eventBoxStepId": step_id,
                    "drawCount": 0,
                    "lappedCount": 0,
                    "boost": False,
                }
                step_rows.append(step_row)
            step_row["drawCount"] = draw_count
            step_row["boost"] = bool(
                step.get("rateIncreaseLimit")
                and step.get("rateIncrease")
                and draw_count
            )
            user_box["totalDrawCount"] += cost
            result.update(
                {
                    "chosenItems": chosen,
                    "consumedItem": {"itemId": item_id, "amount": cost},
                    "UserEventBoxContent": [
                        r for r in saved if r["eventBoxStepId"] == step_id
                    ],
                    "UserEventBoxStep": [step_row],
                    "currentStepContents": contents(step, saved, draw_count)[1],
                }
            )
            for name in changed:
                state["tokens"][name] = hashlib.md5(
                    oc.msgpack_value(tables[name])
                ).hexdigest()
            if ledger_key:
                ledger[ledger_key] = {"request": request, "response": result}
        if action == "draw" or initialized:
            oc.write_progress(state)
        return result


def notice(oc, request):
    box_id = request.get("eventBoxId")
    if type(box_id) is not int or box_id not in catalog():
        raise ValueError("Unknown event box notice")
    box = catalog()[box_id]
    row = next(
        (
            r
            for r in master.rows(master.master_bytes("eventBox"))
            if r.number(0) == box_id
        )
    )
    periods = [
        oc.schedule_module().config()["events"][str(r.number(0, 8))]
        for r in master.rows(master.master_bytes("adventureEpisode"))
        if r.text(23) == box["code"]
        and str(r.number(0, 8)) in oc.schedule_module().config()["events"]
    ]
    if not periods:
        raise ValueError("Event box is not scheduled")
    period = {
        "start": min((p["start"] for p in periods)),
        "end": max((p["end"] for p in periods)),
    }
    labels = {}
    for table, key, _ in {box["currency"]} | {
        c["resource"] for step in box["steps"].values() for c in step["contents"]
    }:
        name = table[4:5].lower() + table[5:]
        for item in master.rows(master.master_bytes(name)):
            labels[table, item.number(0)] = item.text(2)
    currency_table, _, currency_id = box["currency"]
    esc = html.escape
    date = lambda stamp: time.strftime("%m/%d/%Y %H:%M", time.gmtime(stamp))
    sections = [
        f"<header>{esc(row.text(1))}</header>",
        "<h2>■Event Period</h2>",
        f"<p>{date(period['start'])} ～ {date(period['end'])} (UTC)</p>",
        "<h2>■Description</h2>",
        f"<p>Each draw costs {box['cost']} {esc(labels[currency_table, currency_id])}. Gacha Settings sets the maximum number of draws at once (10, 50 or 100). The actual number is limited by your available event items.</p>",
        "<p>Special prizes have limited stock. Reset Items becomes available after the reset prizes for the current round have been collected.</p>",
    ]
    tables = oc.load_progress()["tables"]
    saved = tables.get("UserEventBoxContent", [])
    for step in sorted(box["steps"].values(), key=lambda s: s["index"]):
        rates = {
            r["contentId"]: r["rate"]
            for r in contents(
                step,
                saved,
                draws_for_step(step, saved, tables.get("UserEventBoxStep", [])),
            )[1]
        }
        picked = {
            r["eventBoxContentId"]: r["pickedAmount"]
            for r in saved
            if r["eventBoxStepId"] == step["id"]
        }
        sections += [
            f"<h2>■Lineup for Round {step['index']}</h2>",
            f"<p>Each draw increases the rate of each remaining limited item by {step['rateIncrease'] / 100:.4f} percentage points, up to {step['rateIncreaseLimit']} draws in this round. Unlimited item rates decrease. The increase resets when moving to the next round.</p>"
            if step.get("rateIncreaseLimit") and step.get("rateIncrease")
            else "<p>This round has no per-draw rate increase.</p>",
            "<table><tr><th>Item</th><th>Amount</th><th>Current rate</th><th>Stock left</th><th>Reset prize</th></tr>",
        ]
        for item in step["contents"]:
            table, _, item_id = item["resource"]
            left = (
                str(max(0, item["limit"] - picked.get(item["id"], 0)))
                if item["limited"]
                else "Unlimited"
            )
            sections.append(
                f"<tr><td>{esc(labels[table, item_id])}</td><td>{item['amount']}</td><td>{rates[item['id']] * 100:.4f}%</td><td>{left}</td><td>{('Yes' if item['resetPrize'] else '—')}</td></tr>"
            )
        sections.append("</table>")
    page = (
        '<!doctype html><html><head><meta charset="utf-8"><style>body{margin:24px;background:#eee;color:#111;font:22px Arial,sans-serif}header{color:#706628;font-weight:bold;border-bottom:2px solid #aaa58a;padding:18px 0}h2{font-size:24px;margin:28px 0 8px}table{border-collapse:collapse;width:100%}td,th{border:2px solid #bbb9a5;padding:10px;text-align:left}th{background:#aaa58a;color:white}tr:nth-child(odd){background:#d0c9a8}</style></head><body>'
        + "".join(sections)
        + "</body></html>"
    )
    return {"code": 0, "html": page}
