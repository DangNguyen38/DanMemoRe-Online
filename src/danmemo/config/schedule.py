"""File-based event scheduling and native catalog overlays."""

from danmemo import paths as _paths
import gzip
import functools
import hashlib
import json
import time
from danmemo.master import reader as master
from danmemo.content.assets import original_url as original_url
from danmemo.game import resources as resources
from danmemo.game import gifts as gifts

ROOT = _paths.ROOT
WORK = _paths.STATE
CONFIG = _paths.SCHEDULE
_daily_catalog_cycle = None
DEFAULT_BANNERS = (
    171002049,
    171002249,
    171000732,
    171000733,
    171002059,
    171002156,
    171002215,
)
DEFAULT_EVENTS = (
    7090010701,
    7090010801,
    7090011401,
    7090012001,
    7090120226,
    7090120227,
    7090120228,
    7090120231,
)


def config():
    if not CONFIG.is_file():
        raise FileNotFoundError(f"Missing schedule {CONFIG}; run danmemo init")
    value = json.loads(CONFIG.read_text("utf-8"))
    for category in ("banners", "events"):
        if not isinstance(value.get(category), dict):
            raise ValueError(f"Invalid {category} schedule")
    return value


def available(category, identifier, now=None):
    period = config()[category].get(str(identifier))
    now = int(time.time()) if now is None else now
    return bool(period and period["start"] <= now <= period["end"])


@functools.cache
def ticket_banner_schedules():
    """Reopen the latest recovered archive for each ticket identity.

    Ticket entry points are inventory-driven, unlike scheduled Iris banners.
    Repeated historical versions of the same ticket must not all be listed.
    This is a local archive availability policy, not the retired server policy.
    """
    value = json.loads(
        gzip.decompress(
            (_paths.METADATA / "offline_lottery_catalog.json.gz").read_bytes()
        )
    )
    extra = _paths.METADATA / "offline_ticket_catalog.json.gz"
    if extra.exists():
        value["lotteries"].update(
            json.loads(gzip.decompress(extra.read_bytes()))["lotteries"]
        )
    latest = {}
    for lid, banner in value["lotteries"].items():
        tid = banner.get("ticketId", 0)
        if tid:
            latest[tid] = max(latest.get(tid, 0), int(lid))
    return {str(lid): {"start": 1, "end": 2114356592} for lid in latest.values()}


@functools.cache
def overlays():
    settings = config()
    result = {}
    for name, identifier, fields in (
        ("pvpInformation", 10000000179, (1, 2, 3, 4)),
        ("serialPvpInformation", 81000000050, (3, 4, 5, 6)),
        ("underPvpInformation", 69000000054, (8, 9, 10, 11)),
        ("adventure", 671500152, (6, 7, 8, 9)),
    ):
        output = WORK / "admin" / "master" / f"{name}-arena-test-v2.enc"
        if not output.exists():
            master.arena_overlay(name, identifier, fields, output)
        result[f"master/divide_enc/{name}.enc"] = output
    recipe_schedules = {
        str(r.number(0, 8)): {"start": 1, "end": 2114356592}
        for r in master.rows(master.master_bytes("equipmentRecipe"))
        if r.pos(11) or r.pos(12)
    }
    recipe_digest = hashlib.sha256(
        json.dumps(recipe_schedules, sort_keys=True).encode()
    ).hexdigest()[:16]
    recipe_path = WORK / "admin" / "master" / f"equipmentRecipe-{recipe_digest}.enc"
    if not recipe_path.exists():
        master.schedule_overlay("equipmentRecipe", recipe_schedules, recipe_path)
    result["master/divide_enc/equipmentRecipe.enc"] = recipe_path
    linked = {"shop": {}, "eventBox": {}, "adventureStage": {}, "shopProduct": {}}
    episodes = {
        str(r.number(0, 8)): r
        for r in master.rows(master.master_bytes("adventureEpisode"))
    }
    shops = {
        r.text(1): str(r.number(0)) for r in master.rows(master.master_bytes("shop"))
    }
    for row in master.rows(master.master_bytes("shop")):
        if row.number(3) == 4:
            linked["shop"][str(row.number(0))] = {"start": 1, "end": 2114356592}
    boxes = {
        r.text(2): str(r.number(0))
        for r in master.rows(master.master_bytes("eventBox"))
    }
    for stage in master.rows(master.master_bytes("adventureStage")):
        period = settings["events"].get(str(stage.number(1, 8)))
        if period and stage.pos(12) and stage.pos(13):
            linked["adventureStage"][str(stage.number(0, 8))] = period
    for eid, period in settings["events"].items():
        episode = episodes[eid]
        for name, lookup, field in (("shop", shops, 15), ("eventBox", boxes, 23)):
            identifier = lookup.get(episode.text(field))
            if identifier:
                previous = linked[name].get(identifier, period)
                linked[name][identifier] = {
                    "start": min(previous["start"], period["start"]),
                    "end": max(previous["end"], period["end"]),
                }
    for product in master.rows(master.master_bytes("shopProduct")):
        period = linked["shop"].get(str(product.number(14)))
        if period and product.pos(12) and product.pos(13):
            linked["shopProduct"][str(product.number(0))] = period
    for category, name in (
        ("banners", "lottery"),
        ("events", "adventureEpisode"),
        ("events", "shop"),
        ("events", "eventBox"),
        ("events", "adventureStage"),
        ("events", "shopProduct"),
    ):
        selected = settings[category]
        schedules = (
            linked[name]
            if name in linked
            else {
                str(row["id"]): selected.get(
                    str(row["id"]), {"start": 2114356593, "end": 2114356594}
                )
                for row in catalog()[category]
            }
        )
        if category == "banners":
            tickets = {str(row["id"]): row["ticketId"] for row in catalog()["banners"]}
            explicit_tickets = {tickets[lid] for lid in selected if tickets.get(lid)}
            for lid, period in ticket_banner_schedules().items():
                if lid not in selected and tickets.get(lid) not in explicit_tickets:
                    schedules[lid] = period
            for row in master.rows(master.master_bytes(name)):
                if row.pos(4) or row.pos(5):
                    lid = str(row.number(0))
                    schedules.setdefault(lid, {"start": 2114356593, "end": 2114356594})
                    if (
                        not row.pos(4)
                        and row.pos(5)
                        and (lid not in selected)
                        and (schedules[lid]["start"] == 2114356593)
                    ):
                        schedules[lid] = {"start": 1, "end": 2}
            starter = next(
                (
                    r
                    for r in master.rows(master.master_bytes(name))
                    if r.number(0) == 171001765
                )
            )
            schedules["171001765"] = {"start": starter.number(4, 8), "end": 2114356592}
        digest = hashlib.sha256(
            ("event-linked-v6" + json.dumps(schedules, sort_keys=True)).encode()
        ).hexdigest()[:16]
        output = WORK / "admin" / "master" / f"{name}-{digest}.enc"
        if not output.exists():
            master.schedule_overlay(name, schedules, output)
        result[f"master/divide_enc/{name}.enc"] = output
    from danmemo.config import modes as admin_game_modes

    result.update(admin_game_modes.scheduled_overlays("offline", result))
    return result


def ensure_daily_catalog(oc):
    global _daily_catalog_cycle
    from danmemo.game.daily_windows import (
        catalog_cycle as catalog_cycle,
        mission_month as mission_month,
    )
    from danmemo.config import modes as modes

    cycle = (
        catalog_cycle(),
        mission_month(time.time()),
        modes.under_release_deferred(modes.config("offline")["modes"]),
    )
    if _daily_catalog_cycle is not None and cycle != _daily_catalog_cycle:
        refresh(oc, content_changed=False)
    _daily_catalog_cycle = cycle


def refresh(oc, content_changed=True):
    if content_changed:
        catalog.cache_clear()
    overlays.cache_clear()
    oc.local_project_manifest.cache_clear()
    oc.local_version_manifest.cache_clear()
    oc.asset_paths.cache_clear()
    if content_changed:
        overlays()


def repair_events(oc):
    with oc.PROGRESS_LOCK:
        saved = oc.load_progress()
        stages = saved["tables"].setdefault("UserAdventureStage", [])
        by_id = {r["adventureStageId"]: r for r in stages}
        changed = False
        catalog_stages = oc.stage_catalog()["stages"]
        for ep in config()["events"]:
            grouped = {}
            for sid, data in catalog_stages.items():
                if str(data[0]) == ep:
                    grouped.setdefault(data[1], []).append((data[2], int(sid), data))
            for difficulty, group in grouped.items():
                group.sort()
                for order, sid, data in group:
                    if sid not in by_id:
                        row = {
                            "userId": oc.LOCAL_USER_ID,
                            "adventureStageId": sid,
                            "state": 1 if difficulty == 1 and sid == group[0][1] else 0,
                            "tasks": [
                                {"adventureStageTaskId": tid, "state": 0}
                                for tid in data[4]
                            ],
                            "ticketOpenedAt": 0,
                            "ticketClosedAt": 0,
                            "usedTicketId": 0,
                            "firstTryDateTime": 0,
                            "firstCompleteDateTime": 0,
                            "lastTryDateTime": 0,
                            "lastCompleteDateTime": 0,
                            "token": "",
                            "cumulativeClearCount": 0,
                            "currentScore": 0,
                            "highScore": 0,
                            "dailyClearCount": 0,
                        }
                        stages.append(row)
                        by_id[sid] = row
                        changed = True
                first = by_id[group[0][1]]
                if difficulty == 1 and first["state"] == 0:
                    first["state"] = 1
                    changed = True
        if not changed:
            return
        saved["tokens"]["UserAdventureStage"] = hashlib.md5(
            oc.msgpack_value(stages)
        ).hexdigest()
        oc.write_progress(saved)


@functools.cache
def catalog():
    pools = json.loads(
        gzip.decompress(
            (_paths.METADATA / "offline_lottery_catalog.json.gz").read_bytes()
        )
    )["lotteries"]
    return {
        "banners": [
            {
                "id": r.number(0),
                "ticketId": pools.get(str(r.number(0)), {}).get("ticketId", 0),
            }
            for r in master.rows(master.master_bytes("lottery"))
        ],
        "events": [
            {"id": r.number(0, 8)}
            for r in master.rows(master.master_bytes("adventureEpisode"))
        ],
    }
