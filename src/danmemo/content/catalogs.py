"""Online-only event schedule overlay, preserving the offline asset catalog."""

from danmemo.storage.atomic import write_json as atomic_write_json

import functools
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
import time
from urllib.parse import unquote, urlsplit
import urllib.request
from danmemo.game import engine as game
from danmemo.master import reader as master

CONTENT_ORIGIN = None


def rush_schedules(now):
    """Reopen the complete original S43 adventure/episode/stage hierarchy."""
    season = 58200000043
    stages = [
        r.number(5, 8)
        for r in master.rows(master.master_bytes("guildRaidStage"))
        if r.number(1, 8) == season
    ]
    stage_rows = [
        r
        for r in master.rows(master.master_bytes("adventureStage"))
        if r.number(0, 8) in stages
    ]
    episodes = {r.number(1, 8) for r in stage_rows}
    period = {"start": int(now) - 86400, "end": 2114356592}
    return {
        "adventureEpisode": {str(i): period for i in episodes},
        "adventureStage": {str(i): period for i in stages},
    }


def catalogs():
    from danmemo.config import modes as modes

    settings = modes.config("online")
    admin = game.schedule_module()
    shared = admin.CONFIG.read_text("utf-8") if admin.CONFIG.exists() else ""
    offline = game.WORK / "admin/modes-offline.json"
    from danmemo.game.daily_windows import (
        catalog_cycle as catalog_cycle,
        mission_month as mission_month,
    )

    signature = (
        json.dumps(settings, sort_keys=True)
        + shared
        + (offline.read_text("utf-8") if offline.exists() else "")
        + ":daily:"
        + str(catalog_cycle())
        + ":monthly:"
        + mission_month(time.time())
        + ":under:"
        + str(modes.under_release_deferred(settings["modes"]))
    )
    return _catalogs(signature, CONTENT_ORIGIN)


@functools.lru_cache(maxsize=8)
def _catalogs(signature, content_origin=None):
    from danmemo.config import modes as modes

    admin = game.schedule_module()
    admin.overlays.cache_clear()
    game.local_project_manifest.cache_clear()
    game.local_version_manifest.cache_clear()
    prepared_version = None
    if content_origin:
        with urllib.request.urlopen(
            content_origin + "/content/1/project.manifest", timeout=30
        ) as source:
            base = json.load(source)
        with urllib.request.urlopen(
            content_origin + "/content/1/version.manifest", timeout=30
        ) as source:
            prepared_version = json.load(source)
        archived = json.loads(
            (game.CONTENTS / "1/project.manifest").read_text("utf-8")
        )["assets"]
        sources = {}
        for key, item in base["assets"].items():
            if not key.startswith("master/divide_enc/") or item["md5"] == archived.get(
                key, {}
            ).get("md5"):
                continue
            target = (
                game.WORK / "online/master" / ("content-source-" + item["md5"] + ".enc")
            )
            if (
                not target.is_file()
                or hashlib.md5(target.read_bytes()).hexdigest() != item["md5"]
            ):
                with urllib.request.urlopen(
                    content_origin + "/content/" + item["path"], timeout=30
                ) as source:
                    body = source.read()
                if hashlib.md5(body).hexdigest() != item["md5"]:
                    raise ValueError("Content master does not match prepared manifest")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
            sources[key] = target
    else:
        base = json.loads(game.local_project_manifest("1").read_text("utf-8"))
        sources = admin.overlays()
    paths = {}
    digests = []
    for key, target in modes.scheduled_overlays("online", sources).items():
        body = target.read_bytes()
        digest = hashlib.md5(body).hexdigest()
        item = base["assets"][key]
        item.update(
            md5=digest,
            size=len(body),
            path=str(PurePosixPath(item["path"]).parent / (digest + ".enc")),
        )
        paths.update(
            {"/api/" + item["path"]: target, "/content/" + item["path"]: target}
        )
        digests.append(digest)
    previous = base["version"]
    base["version"] = hashlib.md5((previous + "".join(digests)).encode()).hexdigest()
    for key in ("remoteManifestUrl", "remoteVersionUrl"):
        if key in base:
            base[key] = base[key].replace(previous, base["version"])
    project = game.generated_manifest("online.project.manifest.1", base)
    version = (
        prepared_version
        if prepared_version is not None
        else json.loads(game.local_version_manifest("1").read_text("utf-8"))
    )
    previous = version["version"]
    version["version"] = base["version"]
    for key in ("remoteManifestUrl", "remoteVersionUrl"):
        version[key] = version[key].replace(previous, base["version"])
    version_path = game.generated_manifest("online.version.manifest.1", version)
    index = game.WORK / "online/master" / f"asset-paths-{base['version']}.json"
    if not index.exists():
        atomic_write_json(index, {key: str(path) for key, path in paths.items()})
    historical_paths.cache_clear()
    return (project, version_path, paths)


@functools.cache
def historical_paths():
    """Finish in-flight downloads even after the operator changes a schedule."""
    result = {}
    for index in (game.WORK / "online/master").glob("asset-paths-*.json"):
        for key, value in json.loads(index.read_text("utf-8")).items():
            path = Path(value).resolve()
            if path.is_relative_to(game.WORK.resolve()) and path.is_file():
                result[key] = path
    return result


def persisted_content_path(request):
    """Read already-published online master URLs without a live game gateway."""
    path = unquote(urlsplit(request).path)
    if not path.startswith(
        ("/api/contents/master/divide_enc/", "/content/contents/master/divide_enc/")
    ):
        return None
    indexes = game.WORK / "online/master"
    signature = tuple(
        (
            (str(p), p.stat().st_mtime_ns, p.stat().st_size)
            for p in sorted(indexes.glob("asset-paths-*.json"))
        )
    )
    result = _persisted_paths(signature, str(game.WORK.resolve())).get(path)
    return result if result is not None and result.is_file() else None


@functools.lru_cache(maxsize=4)
def _persisted_paths(signature, root):
    result = {}
    for filename, _, _ in signature:
        for key, value in json.loads(Path(filename).read_text("utf-8")).items():
            target = Path(value).resolve()
            if target.is_relative_to(Path(root)) and target.is_file():
                result[key] = target
    return result


def content_path(request):
    path = unquote(urlsplit(request).path)
    name = PurePosixPath(path).name
    if (
        path.startswith(("/api/asset/manifest/", "/content/asset/manifest/"))
        and name == "version.manifest.1"
        or path == "/content/1/version.manifest"
    ):
        return catalogs()[1]
    parts = PurePosixPath(path).parts
    if (
        path.startswith(("/api/manifests/us/pkm/", "/content/manifests/us/pkm/"))
        and len(parts) > 1
        and (parts[-2] == "project.manifest.1")
        or path == "/content/1/project.manifest"
    ):
        return catalogs()[0]
    if path.startswith(
        ("/api/contents/master/divide_enc/", "/content/contents/master/divide_enc/")
    ):
        return catalogs()[2].get(path) or historical_paths().get(path)
    return None
