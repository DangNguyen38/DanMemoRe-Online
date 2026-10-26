"""Stream-style character rate tables using the exact runtime draw pools."""

import html
import json
import math
from danmemo.game.lottery_rates import probabilities as probabilities


def percent(rate):
    places = 3
    while 0 < rate * 100 < 10 ** (-places) and places < 9:
        places += 1
    scale = 10**places
    value = math.floor(rate * 100 * scale + 1e-08) / scale
    return f"{value:.{places}f}".rstrip("0").rstrip(".") + "%"


def render(lottery_id, banner, notices, ascension):
    metadata = notices["pcs"]
    featured = notices["banners"][str(lottery_id)]["featured"]
    order = {pid: index for index, pid in enumerate(featured)}
    original_flags = ascension.get(lottery_id, {})
    selection = banner.get("selection", [])
    selected_ids = {pid for slot in selection for pid in slot["pcs"]}
    selected_preview = []
    for slot in selection:
        if not any((group[0] == slot["weightGroupId"] for group in selected_preview)):
            selected_preview.append((slot["weightGroupId"], slot["weight"]))
    conditional_fixed = [
        [
            weight,
            sorted(
                {
                    pid
                    for slot in selection
                    if slot["weightGroupId"] == group
                    for pid in slot["pcs"]
                }
            ),
        ]
        for group, weight in selected_preview
    ]

    def rows_for(pool, eligible):
        p = probabilities(pool)
        conditional = bool(selection) and (
            pool == banner["pools"]["7"] or pool == conditional_fixed
        )

        def key(pid):
            meta = metadata[str(pid)]
            return (
                pid not in order,
                order.get(pid, 0),
                meta["role"] != "Adventurer",
                -meta["rarity"],
                -p[pid],
                pid,
            )

        rows = []
        for pid in sorted(p, key=key):
            meta = metadata[str(pid)]
            name = f"[{meta['title']}]{meta['name']}"
            label = html.escape(name)
            if pid in order:
                label = f'<a href="#unit-{pid}">{label}</a>'
            role = meta["role"]
            rate_label = percent(p[pid])
            if conditional and pid in selected_ids:
                slot = next((slot for slot in selection if pid in slot["pcs"]))
                slots = sum(
                    (
                        other["weightGroupId"] == slot["weightGroupId"]
                        for other in selection
                    )
                )
                total = math.fsum((weight for weight, _ in pool))
                primary = (
                    probabilities(banner["primaryPool"]).get(pid, 0)
                    if pool == banner["pools"]["7"]
                    else 0
                )
                primary_total = math.fsum(
                    (weight for weight, _ in banner["primaryPool"])
                )
                rate = slot["weight"] / total / slots + primary * primary_total / total
                rate_label = "When selected: " + percent(rate)
            rows.append(
                f'''<tr data-pc="{pid}" data-rate-up="{str(pid in order).lower()}"><td>{meta["rarity"]}★</td><td class="race-{role.lower()}">{role}</td><td>{label}</td><td class="unit-rate">{rate_label}</td><td class="hero-star">{("★" if pid in eligible else "")}</td></tr>'''
            )
        return "".join(rows)

    from danmemo.game.private_purchase import lottery_buttons as lottery_buttons

    buttons = lottery_buttons().get(lottery_id, {})
    normal_labels = []
    if banner.get("ticketId"):
        normal_labels.append("Ticket Gacha (normal picks)")
    else:
        if (
            buttons.get(1, {}).get("picks", 0) > 0
            and buttons[1].get("consumeType") == 3
            and (not banner["fixedCount"])
        ):
            normal_labels.append("Paid 1 Draw Gacha")
        if banner.get("singlePickCount", 0) > 0 and (not banner["fixedCount"]):
            normal_labels.append(f"{banner['singlePickCount']} Draw Gacha")
        picks = banner.get("multiPickCount", 11)
        fixed = banner["multiFixedCount"]
        if picks > 0:
            normal_labels.append(
                f"{picks} Draw Gacha"
                + (
                    f" (1~{picks - fixed}th Draw)"
                    if fixed and picks > fixed
                    else " (normal picks)"
                )
            )
        if not normal_labels:
            normal_labels.append("Gacha (normal picks)")
    normal_title = ", ".join(normal_labels) + " Unit List"
    entries = [
        (
            normal_title,
            banner["pools"]["7"],
            original_flags.get("pools", {}).get("7", set()),
        )
    ]
    if banner["fixedCount"] and banner["pools"].get("25"):
        title = (
            "Ticket fixed picks"
            if banner.get("ticketId")
            else "Single-draw fixed picks"
        )
        entries.append(
            (
                f"{title} ({banner['fixedCount']} guaranteed pick(s)) Unit List",
                banner["pools"]["25"],
                original_flags.get("pools", {}).get("25", set()),
            )
        )
    if banner["multiFixedCount"] and banner["pools"].get("35"):
        picks = banner.get("multiPickCount", 11)
        fixed_label = (
            f"{picks} Draw Gacha ({picks}th Draw)"
            if banner["multiFixedCount"] == 1
            else f"{picks} Draw Gacha (fixed picks)"
        )
        entries.append(
            (
                fixed_label + " Unit List",
                banner["pools"]["35"],
                original_flags.get("pools", {}).get("35", set()),
            )
        )
    for step, config in sorted(banner["steps"].items(), key=lambda pair: int(pair[0])):
        flags = original_flags.get("steps", {}).get(step, {})
        entries.append(
            (
                f"Step {step} ({config['cost']} Iris) Unit List",
                config["normal"],
                flags.get("normal", set()),
            )
        )
        if config["fixedCount"]:
            entries.append(
                (
                    f"Step {step}: {config['fixedCount']} guaranteed pick(s) Unit List",
                    config["fixed"],
                    flags.get("fixed", set()),
                )
            )
    for count, pool in sorted(
        banner["guarantees"].items(), key=lambda pair: int(pair[0])
    ):
        entries.append(
            (
                f"11-draw #{count}: guaranteed pick Unit List",
                pool,
                original_flags.get("guarantees", {}).get(count, set()),
            )
        )
    if selection and banner.get("selectionFixedDraws"):
        draws = ", ".join(map(str, banner["selectionFixedDraws"]))
        entries.append(
            (
                f"11-draw #{draws}: selected guaranteed pick Unit List",
                conditional_fixed,
                set(),
            )
        )
    sections = (
        [
            '<p class="selected-lineup-rates">Select one distinct unit for each lineup slot. Rates marked "When selected" apply only to your chosen units; the eligible unit list is not a pool of simultaneous rate-ups.</p>'
        ]
        if selection
        else []
    )
    groups = {}
    group_indices = {}
    entry_rules = []
    for title, pool, eligible in entries:
        body = rows_for(pool, eligible)
        if body not in groups:
            group_indices[body] = len(groups)
            groups[body] = []
        groups[body].append(title)
        entry_rules.append(group_indices[body])
    for rule, (body, titles) in enumerate(groups.items()):
        title, pool, eligible = next(
            (entry for index, entry in enumerate(entries) if entry_rules[index] == rule)
        )
        p = probabilities(pool)
        totals = tuple(
            (
                (
                    rank,
                    tuple(
                        (
                            math.fsum(
                                (
                                    rate
                                    for pid, rate in p.items()
                                    if metadata[str(pid)]["rarity"] == rank
                                    and metadata[str(pid)]["role"] == role
                                )
                            )
                            for role in ("Adventurer", "Assist")
                        )
                    ),
                )
                for rank in (2, 3, 4)
            )
        )
        label = title.removesuffix(" Unit List")
        sections.append(
            f'<section class="rate-summary"><h4 class="rate-summary-heading">{html.escape(label)}</h4><table class="gacha-summary"><thead><tr><th>Rank</th><th>Adventurer</th><th>Assist</th></tr></thead><tbody>'
            + "".join(
                (
                    f"<tr><th>{rank}★</th><td>{percent(values[0])}</td><td>{percent(values[1])}</td></tr>"
                    for rank, values in totals
                    if sum(values) > 0
                )
            )
            + "</tbody></table>"
            + f'<div class="lineup-heading">{html.escape(title)}</div>'
        )
        sections.append(
            f'<div id="menu-char{rule + 1}" class="rate-scroll" style="display:block;clear:both;"><table class="gacha-lineup"><thead><tr><th>Rank</th><th>Race</th><th>Unit Name</th><th>Rate</th><th>Hero Ascension Unlocked</th></tr></thead><tbody id="gacha-rate-rows-{rule}">'
            + body
            + "</tbody></table></div></section>"
        )
    labels = json.dumps(list(groups.values()), ensure_ascii=True).replace(
        "<", "\\u003c"
    )
    sections.append(
        '<script type="application/json" id="gacha-rate-labels">' + labels + "</script>"
    )
    return "".join(sections)
