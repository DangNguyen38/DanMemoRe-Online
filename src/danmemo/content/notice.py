"""Master-derived notices using recovered publisher CSS and verified copy."""

import functools
import gzip
import html
import json
from pathlib import Path
import time
from danmemo.content import notice_presentation as presentation
from danmemo.content.assets import original_url as original_url


@functools.cache
def catalog():
    data = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "offline_notice_catalog.json.gz").read_bytes()
        )
    )
    from danmemo.content.gacha_titles import display_title as display_title

    for lid, banner in data["banners"].items():
        banner["title"] = display_title(lid, banner["title"])
    return data


def render(lottery_id, endpoint, period, rates):
    data = catalog()
    banner = data["banners"][str(lottery_id)]
    kind = presentation.banner_type(lottery_id, banner)
    if '<table class="gacha-lineup">' in rates:
        from danmemo.content.notice_archive import render as archived_notice

        restored = archived_notice(lottery_id, endpoint, period)
        if restored is not None:
            return restored
    original = data.get("originalTags", {})
    esc = html.escape
    from danmemo.content.gacha_templates import choose as choose

    template = (
        choose(lottery_id, kind, len(banner["featured"]))
        if '<table class="gacha-lineup">' in rates
        else None
    )
    date = lambda stamp: time.gmtime(stamp - 8 * 3600)
    sections = [
        f"""<header class="titleBlock"><div class="box1"><h1 class="title">{esc(banner["title"])}</h1></div><div class="box2"><span class="date">{time.strftime("%m/%d/%Y", date(period["start"]))}</span></div></header>""",
        '<div class="mainBlcok">',
        "<h3>■Event Period</h3><p>"
        + time.strftime("%m/%d(%a) %H:%M", date(period["start"]))
        + " ～ "
        + time.strftime("%m/%d(%a) %H:%M", date(period["end"]))
        + " (PST)</p>",
        "<h3>■Description</h3>",
    ]
    recovered = presentation.description(lottery_id, banner)
    if recovered:
        sections.append(recovered)
    elif banner["featured"]:
        sections.append(f"<p>{esc(banner['title'])} available!")
        labels = [
            f"{data['pcs'][str(pid)]['rarity']}★ [{esc(data['pcs'][str(pid)]['title'])}]{esc(data['pcs'][str(pid)]['name'])}"
            for pid in banner["featured"]
        ]
        availability = {
            "regular": "Regular Units",
            "limited": "Time-Limited Units",
            "hero-festa": "Hero Festa Limited Units",
        }.get(kind, "Featured Units")
        sections.append("<br>" + availability + ": " + ", ".join(labels) + ".</p>")
        from danmemo.game import lottery_ascension as offline_lottery_ascension

        flags = (
            offline_lottery_ascension.catalog()
            .get(lottery_id, {})
            .get("pools", {})
            .get("7", set())
        )
        unlocked = [
            label for pid, label in zip(banner["featured"], labels) if pid in flags
        ]
        if unlocked:
            sections.append(
                "<p>You can draw "
                + ", ".join(unlocked)
                + ' with <span class="ascension-unlocked">Hero Ascension Unlocked!</span></p>'
            )
    else:
        sections.append(
            f"<p>{esc(banner['title'])} available!</p><p>Available units and their draw rates are listed below.</p>"
        )
    if banner["featured"]:
        sections.append(
            '<table class="units argo-table heading"><tr><th>Unit(s)</th></tr>'
        )
    for pid in banner["featured"]:
        pc = data["pcs"][str(pid)]
        sections.append(
            f"""<tr><td><a href="#unit-{pid}"><em class="race-{pc["role"].lower()}">《{pc["role"]}》</em>{"★" * pc["rarity"]}[{esc(pc["title"])}]{esc(pc["name"])}</a></td></tr>"""
        )
    unit_heading = original.get("h3_introduction", "<h2>■Unit Details</h2>")
    unit_heading = unit_heading.replace("<introduction>{{body}}</introduction>", "")
    if template and template["unitHeading"]:
        unit_heading = '<h3 id="id.1">' + esc(template["unitHeading"]) + "</h3>"
    if banner["featured"]:
        sections.append("</table><p>※Tap a unit above for more detail.</p>")
        from danmemo.game.engine import offline_lotteries as offline_lotteries

        schedule = (
            offline_lotteries()["lotteries"].get(str(lottery_id), {}).get("steps", {})
        )
        if schedule:
            display = "none" if not template or template["collapseSteps"] else "block"
            sections.append(
                f"""<div onclick="var e=document.getElementById('menu-conditions');e.style.display=e.style.display=='none'?'block':'none';"><a style="cursor:pointer"><u><b>▼Please see here for each step of the 11 Draw Gacha.▼</b></u></a></div><div id="menu-conditions" style="display:{display};clear:both;"><table class="argo-table heading"><tr><th>Times</th><th>Required Iris</th><th>Guaranteed Picks</th></tr>"""
                + "".join(
                    (
                        f"<tr><th>{esc(str(n))}</th><td>Iris x{step['cost']}</td><td>{step['fixedCount']}</td></tr>"
                        for n, step in sorted(
                            schedule.items(), key=lambda pair: int(pair[0])
                        )
                    )
                )
                + "</table></div>"
            )
        sections.append(unit_heading)
    for position, pid in enumerate(banner["featured"]):
        if position % 2 == 0:
            sections.append('<div class="algo-Grid justify">')
        pc = data["pcs"][str(pid)]
        is_assist = pc["role"] == "Assist"
        attack_type = {
            1: "P.Attack Type",
            2: "M.Attack Type",
            3: "Heal Type",
            4: "Balance Type",
            5: "Defense Type",
        }.get(pc.get("attackType"))
        type_row = (
            f'<tr><td class="unit-type">Unit Type: {attack_type}</td></tr>'
            if not is_assist and attack_type
            else ""
        )
        art_url = next(
            (
                url
                for name in ("all_rectangle.png", "lottery.png", "status_l.png")
                if (url := original_url(f"files/character/lottery/gac_{pid}/{name}"))
            ),
            None,
        )
        art = (
            f'''<img class="art" src="{esc(endpoint + art_url)}" alt="{esc(pc["name"])}">'''
            if art_url
            else '<p class="note">Original artwork is unavailable in this archive.</p>'
        )
        sections.append(
            f'''<article id="unit-{pid}" data-pc="{pid}" class="grid-cell"><table class="argo-table heading stretch stripe unit-card"><tr><th class="unit-title"><em class="race-{pc["role"].lower()}">《{pc["role"]}》</em>{"★" * pc["rarity"]}<br>[{esc(pc["title"])}]{esc(pc["name"])}</th></tr><tr><td class="data-center">{art}</td></tr>{type_row}</table>'''
        )

        def stats(values):
            return (
                '<table class="stats argo-table heading stretch col00-header-nowrap"><tr>'
                + "".join(
                    (
                        "<th>" + s + "</th>"
                        for s in ("HP", "MP", "Str.", "End.", "Dex.", "Agi.", "Mag.")
                    )
                )
                + "</tr><tr>"
                + "".join((f"<td>{v}</td>" for v in values))
                + "</tr></table>"
            )

        sections += [
            f"""<p class="note">※Status when reached Max Limit Break (Lv.{pc["maxLevel"]})</p>""",
            stats(pc["stats"]),
        ]
        if pc["ascendedStats"]:
            sections += [
                '<p class="note">※Maximum Status after upgrading in Hero Ascension.</p>',
                stats(pc["ascendedStats"]),
            ]
        is_assist = pc["role"] == "Assist"
        if is_assist and pc["combat"]:
            sections.append(
                '<table class="assist-skills argo-table heading stretch stripe"><thead><tr><th>Assist Skill</th><th>Effect</th></tr></thead><tbody>'
                + "".join(
                    (
                        f"<tr><td>{esc(s['name'])}</td><td>{esc(s['effect'])}</td></tr>"
                        for s in pc["combat"]
                    )
                )
                + "</tbody></table>"
            )
        if not is_assist and pc["special"]:
            skill = pc["special"]
            sections.append(
                f"""<table class="argo-table heading stretch stripe"><tr><th>Special Arts</th><th>Effect</th></tr><tr><td>{esc(skill["name"])}</td><td>{esc(skill["effect"])}</td></tr></table>"""
            )
        if not is_assist and pc["combat"]:
            sections.append(
                '<table class="argo-table heading stretch stripe"><tr><th>Combat Skill</th><th>Effect</th><th>Consumed MP</th></tr>'
                + "".join(
                    (
                        f"<tr><td>{esc(s['name'])}</td><td>{esc(s['effect'])}</td><td>{s['mp']}</td></tr>"
                        for s in pc["combat"]
                    )
                )
                + "</table>"
            )
        if not is_assist and pc.get("passives"):
            target = f"menu-skills-{pid}"
            sections.append(
                f'''<div onclick="var e=document.getElementById('{target}');e.style.display=e.style.display=='none'?'block':'none';"><a style="cursor:pointer">Skill/Development Ability</a></div><div id="{target}" style="display:none;clear:both;"><table class="argo-table heading stretch stripe"><tr><th>Skill/Development Ability</th><th>Effect</th></tr>'''
                + "".join(
                    (
                        f"<tr><td><b>{esc(s['name'])}</b></td><td>{esc(s['effect'])}</td></tr>"
                        for s in pc["passives"]
                    )
                )
                + "</table></div>"
            )
        sections.append("</article>")
        if position % 2 == 1:
            sections.append("</div>")
    if len(banner["featured"]) % 2:
        sections.append('<div class="grid-cell"></div></div>')
    sections.append(
        '<section class="rates">'
        + original.get("h3_lottery", "{{body}}").replace("{{body}}", rates)
        + "</section>"
    )
    notes_key = {
        "hero-festa": "fes_sublimate_ch_attention",
        "limited": "limit_sublimate_ch_attention",
    }.get(kind, "reg_ch_attention")
    if original.get(notes_key):
        sections.append(original["attention"].replace("{{body}}", original[notes_key]))
    if original.get("all_end"):
        sections.append(original["all_end"])
    style = presentation.stylesheet()
    template_marker = (
        f''' data-reference-notice="{template["lotteryId"]}"''' if template else ""
    )
    page = (
        f'<!doctype html><html><head><meta name="viewport" content="width=1300,user-scalable=no"><meta charset="utf-8"><style>{style}</style></head><body class="detail" data-banner-kind="{kind}"{template_marker}><div class="wrapper"><div class="main"><div class="topShadow"></div>'
        + "".join(sections)
        + "</div></div></div></body></html>"
    )
    from danmemo.content.web_assets import localize_html as localize_html

    return localize_html(presentation.prepare_page(page), endpoint)
