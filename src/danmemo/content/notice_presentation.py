"""Recovered publisher notice CSS with local rendering adapters."""

from danmemo import paths as _paths
import functools
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin
from html.parser import HTMLParser

ROOT = Path(__file__).parent


@functools.cache
def reference():
    return json.loads(
        (_paths.METADATA / "original_notice_reference.json").read_text("utf-8")
    )


@functools.cache
def stylesheet():
    raw = (_paths.METADATA / "original_notice_style.css").read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference()["style"]["sha256"]:
        raise ValueError("Recovered notice stylesheet differs from source receipt")
    css = re.sub(
        "url\\(([^)]+)\\)",
        lambda m: (
            'url("' + urljoin(reference()["style"]["url"], m[1].strip(" \"'")) + '")'
        ),
        raw.decode("utf-8"),
    )
    return (
        css
        + '\n.wrapper{display:block}\n.loadingMessage{display:none}\n.detail .titleBlock{background-image:none;border-bottom:2px solid #B6AF95}\n.topShadow{background-image:none;box-shadow:inset 0 12px 18px -18px #111;pointer-events:none}\narticle.grid-cell{min-width:0;margin:0;scroll-margin-top:10px}\n.grid-cell .unit-card{width:100%}\n.art{display:block;width:400px;height:400px;max-width:100%;object-fit:contain;margin:auto}\n.unit-title .race-adventurer,.units .race-adventurer{color:#a90e0e}\n.unit-title .race-assist,.units .race-assist{color:#0c40d6}\n.stats th,.stats td{text-align:center}\n.assist-skills{table-layout:fixed}.assist-skills th:first-child{width:25%}\n.assist-skills td{overflow-wrap:break-word}\n.note{color:#a90e0e;font-size:16px;margin:16px 0 8px}\n.mainBlcok .units{width:auto}\n.mainBlcok table.gacha-lineup,.mainBlcok table.gacha-summary{border-collapse:collapse;border:2px solid #B6AF95;margin:1em 0;max-width:100%}\n.gacha-lineup th,.gacha-lineup td,.gacha-summary th,.gacha-summary td{border:2px solid #B6AF95;padding:10px;font-size:24px;text-align:left;vertical-align:top}\n.gacha-lineup th,.gacha-summary th{background:#B6AF95;color:#fff;border-right-color:#fff;border-bottom-color:#fff}\n.gacha-lineup tbody tr:nth-child(odd){background:#F2F2F2}\n.gacha-lineup tbody tr:nth-child(even){background:#E1D7BB}\n.gacha-lineup{width:100%;min-width:780px}.gacha-lineup th{white-space:nowrap}\n.gacha-lineup a{color:inherit;text-decoration:none}\n.gacha-summary{width:auto}.gacha-summary td{background:#F2F2F2}\n.rate-scroll{overflow-x:auto}.rate-summary-heading{font-size:24px;margin:20px 0 0}\n.lineup-heading{color:#308080;font-size:24px;font-weight:normal}\n.lineup-heading a{color:inherit;text-decoration:none}\n.race-adventurer,.ascension-unlocked{color:#a90e0e}\n.race-assist{color:#0c40d6}.unit-rate{white-space:nowrap}.hero-star{text-align:center}\n.rate-provenance{font-size:16px;color:#666}\n.unit-lineup a,.unit-lineup a:visited,.unit-lineup a:hover,.unit-lineup a:active{color:inherit;text-decoration:none}\n[id^="menu-char"]{display:block!important}\n[data-rate-heading] a{cursor:default!important}\n'
    )


@functools.cache
def banner_types():
    return json.loads(
        (_paths.METADATA / "original_notice_types.json").read_text("utf-8")
    )


def banner_type(lottery_id, banner):
    record = banner_types().get(str(lottery_id))
    if record:
        if (
            record["title"] != banner["title"]
            or record["featured"] != banner["featured"]
        ):
            return "unknown"
        if record["kind"] != "unknown":
            return record["kind"]
    from danmemo.content import notice as offline_notice

    canonical = offline_notice.catalog()["banners"].get(str(lottery_id))
    if (
        not canonical
        or canonical["title"] != banner["title"]
        or canonical["featured"] != banner["featured"]
    ):
        return "unknown"
    unit_kinds = {}
    for source in banner_types().values():
        if source["kind"] not in ("regular", "limited", "hero-festa"):
            continue
        for pid in source["featured"]:
            unit_kinds.setdefault(pid, set()).add(source["kind"])
    kinds = set()
    for pid in banner["featured"]:
        evidence = unit_kinds.get(pid, set())
        if len(evidence) != 1:
            return "unknown"
        kinds.update(evidence)
    return next(iter(kinds)) if len(kinds) == 1 else "unknown"


def prepare_page(body):
    """Apply requested presentation overrides to preserved and generated HTML."""
    period = re.search(
        "<h[1-6]\\b[^>]*>[^<]*\\b(?:Event\\s+Period|Period)\\b[^<]*</h[1-6]>",
        body,
        re.I,
    )
    if period:
        header = body[: period.start()]
        header = re.sub(
            "<div\\b(?=[^>]*\\bclass=[\"\\'][^\"\\']*\\bkeyvalueBlcok\\b[^\"\\']*[\"\\'])[^>]*>\\s*<img\\b[^>]*>\\s*</div>",
            "",
            header,
            flags=re.S | re.I,
        )
        body = re.sub("<img\\b[^>]*>", "", header, flags=re.I) + body[period.start() :]

    class Attributes(HTMLParser):
        def handle_starttag(self, tag, attrs):
            self.attrs = dict(attrs)

    def menu(match):
        tag = match[0]
        parser = Attributes()
        parser.feed(tag)
        attrs = getattr(parser, "attrs", {})
        if re.fullmatch("menu-char\\d+", attrs.get("id", "")):
            if "style" in attrs:
                style = re.sub("display\\s*:[^;]+;?", "", attrs["style"], flags=re.I)
                tag = re.sub(
                    "\\bstyle=([\\\"\\']).*?\\1",
                    lambda m: 'style="display:block;' + style + '"',
                    tag,
                    count=1,
                    flags=re.S,
                )
            else:
                tag = tag[:-1] + ' style="display:block;">'
        if re.search("menu-char\\d+", attrs.get("onclick", "")):
            tag = re.sub("\\s+onclick=([\\\"\\']).*?\\1", "", tag, count=1, flags=re.S)
            tag = tag[:-1] + ' data-rate-heading="true">'
        return tag

    body = re.sub("<(?:div|a|span|p)\\b[^>]*>", menu, body, flags=re.I)

    def units(match):
        table = match[0]
        heading = re.search("<th\\b[^>]*>(.*?)</th>", table, re.S | re.I)
        if not heading or re.sub("<[^>]+>", "", heading[1]).strip() != "Unit(s)":
            return table
        tag = re.match("<table\\b[^>]*>", table, re.I)[0]
        if re.search("\\bunit-lineup\\b", tag):
            return table
        if re.search("\\bclass=", tag):
            updated = re.sub(
                "\\bclass=([\\\"\\'])(.*?)\\1",
                lambda m: "class=" + m[1] + m[2] + " unit-lineup" + m[1],
                tag,
                count=1,
            )
        else:
            updated = tag[:-1] + ' class="unit-lineup">'
        return updated + table[len(tag) :]

    return re.sub("<table\\b[^>]*>.*?</table>", units, body, flags=re.S | re.I)


def description(lottery_id, banner):
    item = reference()["banners"].get(str(lottery_id))
    if (
        item
        and item["title"] == banner["title"]
        and (item["featured"] == banner["featured"])
    ):
        return item["description"]
    return None
