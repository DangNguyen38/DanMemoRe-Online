"""Use recovered publisher HTML only while its validated pool signature holds."""

from danmemo import paths as _paths
import functools, gzip, hashlib, html, json, re, time
from html.parser import HTMLParser
from urllib.parse import urlsplit


def signature(banner, flags):

    def canonical(value):
        if isinstance(value, set):
            return sorted(value)
        raise TypeError(type(value).__name__)

    return hashlib.sha256(
        json.dumps(
            dict(banner=banner, flags=flags),
            sort_keys=True,
            separators=(",", ":"),
            default=canonical,
        ).encode()
    ).hexdigest()


def unit_signature(lid):
    from danmemo.content import notice as offline_notice

    data = offline_notice.catalog()
    banner = data["banners"][str(lid)]
    units = {str(pid): data["pcs"][str(pid)] for pid in banner["featured"]}
    return hashlib.sha256(
        json.dumps(
            dict(banner=banner, units=units), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


@functools.cache
def catalog():
    path = _paths.METADATA / "original_notice_pages.json.gz"
    return json.loads(gzip.decompress(path.read_bytes())) if path.exists() else {}


def local_character_art(body, lid, endpoint):
    """Keep recovered art; use matching master art for unavailable notice images."""
    from danmemo.content import notice as offline_notice
    from danmemo.content import web_assets as recovered_web_assets
    from danmemo.content import assets as offline_assets

    data = offline_notice.catalog()
    featured = data["banners"][str(lid)]["featured"]
    compact = lambda text: re.sub("[^a-z0-9]", "", text.lower())
    names = {
        compact(
            "[" + data["pcs"][str(pid)]["title"] + "]" + data["pcs"][str(pid)]["name"]
        ): pid
        for pid in featured
    }
    captions = {}
    for pid in featured:
        captions.setdefault(compact(data["pcs"][str(pid)]["title"]), []).append(pid)

    class Image(HTMLParser):
        def handle_starttag(self, tag, attrs):
            self.attrs = dict(attrs)

    def replace(match):
        tag = match[0]
        parser = Image()
        parser.feed(tag)
        attrs = getattr(parser, "attrs", {})
        src = attrs.get("src", "")
        if recovered_web_assets.resolve(urlsplit(src).path):
            return tag
        pid = names.get(compact(attrs.get("alt", "")))
        if not pid:
            caption = re.match("\\[([^]]+)\\]", attrs.get("alt", ""))
            candidates = captions.get(compact(caption[1]), []) if caption else []
            if len(candidates) == 1:
                pid = candidates[0]
        if not pid:
            if "/noticeLarge/" in urlsplit(src).path:
                path = offline_assets.original_url(
                    f"files/lottery/background/{lid}.png"
                )
                if path:
                    return re.sub(
                        "\\bsrc=([\\\"\\']).*?\\1",
                        lambda m: (
                            'src="'
                            + html.escape(endpoint.rstrip("/") + path, quote=True)
                            + '" data-master-banner="'
                            + str(lid)
                            + '"'
                        ),
                        tag,
                        count=1,
                    )
            return tag
        for name in ("all_rectangle.png", "lottery.png", "status_l.png"):
            path = offline_assets.original_url(
                f"files/character/lottery/gac_{pid}/{name}"
            )
            if path:
                return re.sub(
                    "\\bsrc=([\\\"\\']).*?\\1",
                    lambda m: (
                        'src="'
                        + html.escape(endpoint.rstrip("/") + path, quote=True)
                        + '" data-master-art="'
                        + str(pid)
                        + '"'
                    ),
                    tag,
                    count=1,
                )
        return tag

    return re.sub("<img\\b[^>]*>", replace, body, flags=re.I)


def render(lid, endpoint, period):
    record = catalog().get(str(lid))
    if not record:
        return None
    from danmemo.game import engine as client
    from danmemo.game import lottery_ascension as asc
    from danmemo.content import notice_presentation as presentation

    banner = client.offline_lotteries()["lotteries"].get(str(lid))
    flags = asc.catalog().get(lid, {})
    if banner is None or signature(banner, flags) != record["poolSignature"]:
        return None
    if record.get("unitSignature") and unit_signature(lid) != record["unitSignature"]:
        return None
    body = record["html"]
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != record["sha256"]:
        raise ValueError("Recovered notice HTML differs from source receipt")
    body = re.sub(
        "<script\\b[^>]*\\bsrc=[^>]*>.*?</script>", "", body, flags=re.S | re.I
    )
    body = re.sub(
        "<link\\b[^>]*\\brel=[\\\"\\']stylesheet[\\\"\\'][^>]*>", "", body, flags=re.I
    )
    body = body.replace(
        "</head>", "<style>" + presentation.stylesheet() + "</style></head>"
    )
    from danmemo.content import notice as offline_notice

    kind = presentation.banner_type(lid, offline_notice.catalog()["banners"][str(lid)])
    body = body.replace(
        '<body class="detail">',
        f'<body class="detail" data-original-notice="{lid}" data-banner-kind="{kind}">',
    )
    tz = 9 * 3600 if "(JST)" in body.split("■Description")[0] else -8 * 3600
    label = "JST" if tz > 0 else "PST"
    date = lambda stamp: time.strftime("%m/%d(%a) %H:%M", time.gmtime(stamp + tz))
    live = date(period["start"]) + " ～ " + date(period["end"]) + f" ({label})\n"
    body = re.sub(
        "(<h3[^>]*>■(?:Event Period|Period)</h3>\\s*).*?(?=<h3[^>]*>■Description)",
        lambda m: m[1] + live,
        body,
        count=1,
        flags=re.S,
    )
    body = re.sub(
        "https://api-danmemo(?:-(?:us|eu))?\\.wrightflyer\\.net",
        endpoint.rstrip("/"),
        body,
    )
    body = re.sub("var param = .*?;", "var param = '';", body, count=1)
    body = local_character_art(body, lid, endpoint)
    from danmemo.content.web_assets import localize_html as localize_html

    return localize_html(presentation.prepare_page(body), endpoint)
