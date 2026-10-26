"""Choose a recovered layout without borrowing another lottery's game rules."""

import functools
import re
import hashlib


def profile(banner, paid=False):
    return dict(
        ticket=bool(banner.get("ticketId")),
        selection=bool(banner.get("selection")),
        paid=paid,
        steps=bool(banner.get("steps")),
        single=banner.get("singlePickCount", 0),
        multi=banner.get("multiPickCount", 0),
        fixed=banner.get("fixedCount", 0),
        multiFixed=banner.get("multiFixedCount", 0),
        guarantees=len(banner.get("guarantees", {})),
    )


def distance(target, source, kind="unknown", source_kind="unknown"):
    return (
        target["ticket"] != source["ticket"],
        target["selection"] != source["selection"],
        kind != "unknown" and kind != source_kind,
        target["paid"] != source["paid"],
        target["steps"] != source["steps"],
        abs(target["single"] - source["single"])
        + abs(target["multi"] - source["multi"]),
        abs(target["fixed"] - source["fixed"])
        + abs(target["multiFixed"] - source["multiFixed"]),
        abs(target["guarantees"] - source["guarantees"]),
    )


@functools.cache
def references():
    from danmemo.content import notice_archive as offline_notice_archive
    from danmemo.content import notice as offline_notice
    from danmemo.game import engine as offline_client
    from danmemo.game import private_purchase as private_purchase
    from danmemo.content.notice_presentation import banner_types as banner_types

    lotteries = offline_client.offline_lotteries()["lotteries"]
    buttons = private_purchase.lottery_buttons()
    metadata = offline_notice.catalog()["banners"]
    types = banner_types()
    result = {}
    for lid, row in offline_notice_archive.catalog().items():
        if lid not in lotteries:
            continue
        body = row["html"]
        config = lotteries[lid]
        if hashlib.sha256(body.encode()).hexdigest() != row["sha256"]:
            continue
        heading = re.search(
            "<(h[23])\\b[^>]*\\bid=[\"\\']id\\.1[\"\\'][^>]*>(.*?)</\\1>",
            body,
            re.S | re.I,
        )
        heading_text = re.sub("<[^>]*>", "", heading[2]) if heading else None
        menu = re.search(
            "<div\\b[^>]*\\bid=[\"\\']menu-conditions[\"\\'][^>]*>", body, re.I
        )
        collapse = bool(menu and re.search("display\\s*:\\s*none", menu[0], re.I))
        paid = buttons.get(int(lid), {}).get(2, {}).get("consumeType") == 3
        result[int(lid)] = dict(
            profile=profile(config, paid),
            kind=types.get(lid, {}).get("kind", "unknown"),
            source=row["source"],
            featured=len(metadata[lid]["featured"]),
            collapseSteps=collapse,
            unitHeading=heading_text,
        )
    return result


@functools.cache
def choose(lottery_id, kind, featured):
    from danmemo.game import engine as offline_client
    from danmemo.game import private_purchase as private_purchase

    config = offline_client.offline_lotteries()["lotteries"].get(str(lottery_id))
    if not config:
        return None
    candidates = references()
    if not candidates:
        return None
    buttons = private_purchase.lottery_buttons().get(lottery_id, {})
    paid = buttons.get(2, {}).get("consumeType") == 3
    target = profile(config, paid)
    lid, row = min(
        candidates.items(),
        key=lambda pair: (
            distance(target, pair[1]["profile"], kind, pair[1]["kind"]),
            abs(featured - pair[1]["featured"]),
            abs(lottery_id - pair[0]),
            pair[0],
        ),
    )
    return dict(row, lotteryId=lid)
