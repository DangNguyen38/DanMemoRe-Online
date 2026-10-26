"""Evidence-based acknowledgement of locally spent, already received stock."""

import functools


def observed_spending(incoming, saved):
    return (
        type(incoming.get("cumulativeAmount")) is int
        and incoming["cumulativeAmount"] >= saved.get("cumulativeAmount", 0) > 0
        and (type(incoming.get("amount", 0)) is int)
        and (0 <= incoming.get("amount", 0) < saved.get("amount", 0))
    )


@functools.cache
def stamina_items():
    from danmemo.master import reader as master

    return {
        r.number(0): (r.number(4), r.number(5))
        for r in master.rows(master.master_bytes("staminaTicket"))
    }


def refill_evidence(state, request):
    """Bound a pending SP increase by same-push native ticket consumption."""
    from danmemo.game.progression import levels as levels

    status = next(iter(state["tables"].get("UserInfo", [])), {}).get("playerStatus", {})
    cap = next((r[2] for r in levels() if r[1] == status.get("level")), 0)
    old = {
        r["staminaTicketId"]: r for r in state["tables"].get("UserStaminaTicket", [])
    }
    amount = 0
    for delta in request.get("deltas", []):
        for row in delta.get("putItems", {}).get("UserStaminaTicket", []):
            iid = row.get("staminaTicketId")
            before = old.get(iid, {})
            rule = stamina_items().get(iid)
            if rule and observed_spending(row, before):
                amount += (before.get("amount", 0) - row.get("amount", 0)) * (
                    rule[1] if rule[0] == 1 else cap if rule[0] == 2 else 0
                )
            old[iid] = row
    return amount
