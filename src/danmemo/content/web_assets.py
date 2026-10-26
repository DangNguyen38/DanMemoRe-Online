"""Serve byte-verified original web assets by their archived CDN paths."""

from danmemo import paths as _paths
import functools
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = _paths.ROOT


@functools.cache
def catalog():
    result = {}
    rows = []
    for name in ("master-literal-recovery.json", "web-archive-recovery.json"):
        receipt = _paths.STATE / "asset-recovery" / name
        if receipt.is_file():
            rows.extend(json.loads(receipt.read_text("utf-8")))
    receipt = _paths.METADATA / "recovered_notice_assets.json"
    if receipt.is_file():
        rows.extend(json.loads(receipt.read_text("utf-8")))
    for row in rows:
        if row.get("status") != "archived" or not row.get("file"):
            continue
        path = (_paths.ASSETS / "web" / Path(row["file"]).name).resolve()
        if _paths.ASSETS not in path.parents or not path.is_file():
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != row.get("sha256"):
            continue
        parsed = urlsplit(row["url"])
        if parsed.netloc == "cdn-danmemo.akamaized.net":
            if (
                parsed.path in result
                and result[parsed.path].read_bytes() != path.read_bytes()
            ):
                raise ValueError(
                    "Conflicting preserved web asset versions: " + parsed.path
                )
            result[parsed.path] = path
    return result


def resolve(path):
    for prefix in ("/content", "/api"):
        if path.startswith(prefix + "/"):
            return catalog().get(path[len(prefix) :])
    return catalog().get(path)


def localize_html(body, endpoint):
    for path in catalog():
        body = body.replace(
            "https://cdn-danmemo.akamaized.net" + path,
            endpoint.rstrip("/") + "/content" + path,
        )
    return body
