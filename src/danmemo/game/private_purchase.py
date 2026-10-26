"""Free private-server Iris products, following upstream's SDK response shape.

No payment provider is contacted. Paid-Iris stock is server owned and separate
from UserCurrency's free Iris, as in the original billing/client contract.
"""

import copy
import functools
import hashlib
import json
import re
import time
from urllib.parse import parse_qs, unquote


@functools.cache
def lottery_buttons():
    from danmemo.master import reader as master

    return {
        row.number(0): {
            button: dict(
                picks=row.number(slot),
                consumeType=row.number(slot + 1),
                cost=row.number(slot + 2),
                limit=row.number(slot + 4),
                daily=bool(
                    row.number(14 if button == 1 else 44 if button == 2 else 45)
                ),
            )
            for button, slot in ((1, 9), (2, 20), (3, 30))
        }
        for row in master.rows(master.master_bytes("lottery"))
    }


def products():
    return [
        dict(
            product_id=f"com.us.danmemo.gem{amount}",
            name=f"{amount} Iris",
            price=0,
            description="Private server purchase",
            thumbnail_url=f"gem{amount}.png",
            charge_gem=amount,
            free_gem=0,
            bonus_gem=0,
            total_gem=amount,
            campaign_type=0,
            campaign_mode=0,
            priority=index,
            consumable=1,
            formatted_price="Free (Private)",
            currency_code="USD",
            country_code="US",
            product_type=0,
        )
        for index, amount in enumerate((10, 50, 110, 300, 640, 1400))
    ]


def unlocked():
    from danmemo.config import schedule as admin

    return (
        admin.config().get("privateFeatures", {}).get("irisPurchasesUnlocked") is True
    )


def update_settings(request):
    from danmemo.config import schedule as admin
    from danmemo.storage import atomic as accounts
    from danmemo.config import modes as modes

    enabled = request.get("irisPurchasesUnlocked")
    if type(enabled) is not bool:
        raise ValueError("Expected an explicit Iris purchase lock setting")
    with modes.LOCK:
        settings = admin.config()
        backup = (
            admin.CONFIG.parent / "backups" / f"purchase-settings-{time.time_ns()}.json"
        )
        accounts.write_json(backup, settings)
        settings.setdefault("privateFeatures", {})["irisPurchasesUnlocked"] = enabled
        accounts.write_json(admin.CONFIG, settings)
    return dict(
        message="Iris purchases unlocked (free private test)."
        if enabled
        else "Iris purchases locked.",
        irisPurchasesUnlocked=enabled,
    )


def payment_token(header):
    if not isinstance(header, str):
        raise ValueError("Missing purchase identity")
    values = parse_qs(unquote(header), keep_blank_values=True).get(
        "paymentApiToken", []
    )
    if len(values) != 1 or not re.fullmatch("[0-9A-F]{32}", values[0]):
        raise ValueError("Invalid purchase identity")
    return values[0]


def balance(state):
    free = sum(
        (
            r.get("amount", 0)
            for r in state["tables"].get("UserCurrency", [])
            if r["currencyId"] == 205000001
        )
    )
    paid = state.get("privateIrisWallet", {}).get("paid", 0)
    return dict(
        balance_charge_gem=paid, balance_free_gem=free, balance_total_gem=paid + free
    )


def dispatch(oc, method, path, request=None, token=None, query=None):
    request = request or {}
    query = query or {}
    if path == "/billing/v1.0/payment/productlist" and method == "GET":
        return dict(
            result="OK",
            entry=dict(products=products() if unlocked() else [], welcome=0),
        )
    if path == "/billing/v1.0/payment/purchase/alert/setting" and method == "GET":
        return dict(result="OK", entry=dict(purchase_alert=False, threshold_amount=0))
    if (
        path == "/billing/v1.0/payment/purchase/alert/setting/update"
        and method == "POST"
    ):
        return dict(result="OK")
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        if path == "/billing/v1.0/payment/balance" and method == "GET":
            return dict(result="OK", entry=balance(state))
        if path == "/billing/v1.0/payment/history" and method == "GET":
            try:
                offset = int(query.get("offset", ["0"])[0])
                limit = int(query.get("limit", ["20"])[0])
            except (ValueError, TypeError):
                raise ValueError("Invalid purchase history page")
            if offset < 0 or not 1 <= limit <= 100:
                raise ValueError("Invalid purchase history page")
            rows = sorted(
                state.get("privatePurchaseHistory", []),
                key=lambda r: r["date"],
                reverse=True,
            )
            if hasattr(oc, "iris_history"):
                rows += oc.iris_history(0, 1000000)["entry"]["transactions"]
                rows.sort(key=lambda r: r["date"], reverse=True)
            return dict(
                result="OK",
                entry=dict(
                    offset=offset,
                    limit=limit,
                    has_next=offset + limit < len(rows),
                    transactions=rows[offset : offset + limit],
                ),
            )
        if method != "POST" or path != "/billing/v1.0/payment/purchase":
            raise ValueError("Unsupported Purchase route")
        if not unlocked():
            raise ValueError("Iris purchases are locked by the administrator")
        if not isinstance(token, str) or not re.fullmatch("[0-9A-F]{32}", token):
            raise ValueError("Missing purchase identity")
        product = next(
            (p for p in products() if p["product_id"] == request.get("product_id")),
            None,
        )
        if (
            not product
            or set(request)
            != {
                "product_id",
                "country_code",
                "currency_code",
                "translated_name",
                "formatted_price",
                "price",
            }
            or any(
                (
                    request.get(key) != product[key]
                    for key in ("country_code", "currency_code", "formatted_price")
                )
            )
            or (request.get("translated_name") != product["name"])
            or isinstance(request.get("price"), bool)
            or (str(request.get("price")) not in ("0", "0.0"))
        ):
            raise ValueError("Invalid private Iris product")
        key = token + ":" + product["product_id"]
        encoded = json.dumps(request, sort_keys=True)
        previous = state.setdefault("privatePurchaseReceipts", {}).get(key)
        if previous:
            if previous["request"] != encoded:
                raise ValueError("Conflicting purchase retry")
            return previous["response"]
        wallet = state.setdefault("privateIrisWallet", dict(paid=0))
        if wallet["paid"] + product["charge_gem"] > 999999999:
            raise ValueError("Paid Iris limit reached")
        wallet["paid"] += product["charge_gem"]
        response = dict(
            result="OK",
            entry=dict(
                purchase_id="danmemore-" + hashlib.md5(key.encode()).hexdigest()[:24],
                need_caution_for_minors=False,
            ),
            product_id=product["product_id"],
        )
        state["privatePurchaseReceipts"][key] = dict(request=encoded, response=response)
        state.setdefault("privatePurchaseHistory", []).append(
            dict(
                transaction_type=2,
                date=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                reason="Private purchase: " + product["name"],
                balance_change_charge=product["charge_gem"],
                balance_change_free=0,
                price=0,
                formatted_price=product["formatted_price"],
                expire_charge="",
                expire_free="",
            )
        )
        oc.write_progress(state)
        return copy.deepcopy(response)
