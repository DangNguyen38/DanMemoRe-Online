"""Operator schedules for recovered mode masters, separate for each server.

Opening an archived schedule does not implement a missing game backend or
remove account story requirements. Timing policies are local test controls.
"""

from danmemo import paths as _paths
import functools
import hashlib
import json
from pathlib import Path
import time
import tempfile
import threading
import struct
import zlib
from danmemo.master import reader as master
from danmemo.storage import atomic as accounts
from danmemo.master.index import columns as columns

WORK = _paths.STATE
LOCK = threading.RLock()
PERMANENT = {"monsterphilia": dict(start=1, end=2114356592)}


def selected_record_buster_events(selected=None):
    """Selected archived episodes mapped to their original Record Buster seasons."""
    if selected is None:
        path = _paths.SCHEDULE
        if not path.exists():
            return {}
        selected = json.loads(path.read_text("utf-8")).get("events", {})
    if not selected:
        return {}
    adventures = dict(columns("adventure", (0, 8), (2, 4)))
    pvb = dict(columns("pvbInformation", (2, 8), (0, 8)))
    stages = {sid for (sid,) in columns("pvbStage", (1, 8))}
    result = {}
    for eid, parent in columns("adventureEpisode", (0, 8), (2, 8)):
        period = selected.get(str(eid))
        if period and adventures.get(parent) == 15 and (pvb.get(parent) in stages):
            result[parent] = dict(period=period, season=pvb[parent])
    return result


def selected_familia_events(selected=None):
    """Archived Rush/Royale seasons that have original stage contracts."""
    if selected is None:
        path = _paths.SCHEDULE
        selected = (
            json.loads(path.read_text("utf-8")).get("events", {})
            if path.exists()
            else {}
        )
    mappings = {}
    for mode, info, stage in (
        ("familia_rush", "guildRaidInformation", "guildRaidStage"),
        ("familia_royale", "gveInformation", "gveStage"),
        ("union_raid", "unionRaidInformation", "unionRaidStage"),
    ):
        width = 4 if mode == "union_raid" else 8
        supported = {sid for (sid,) in columns(stage, (1, width))}
        for season, parent, tag in columns(info, (0, width), (2, 8), (1, "text")):
            if season in supported:
                mappings[parent] = dict(mode=mode, season=season, tag=tag)
    result = {}
    for eid, parent in columns("adventureEpisode", (0, 8), (2, 8)):
        period = selected.get(str(eid))
        if period and parent in mappings:
            result[parent] = dict(mappings[parent], period=period)
    return result


def selected_element_events(selected=None):
    if selected is None:
        path = _paths.SCHEDULE
        selected = (
            json.loads(path.read_text("utf-8")).get("events", {})
            if path.exists()
            else {}
        )
    infos = {
        parent: dict(season=season, tag=tag)
        for parent, season, tag in columns(
            "elementEventInformation", (1, 8), (0, 8), (5, "text")
        )
    }
    result = {}
    for eid, parent in columns("adventureEpisode", (0, 8), (2, 8)):
        period = selected.get(str(eid))
        if period and parent in infos:
            result[parent] = dict(infos[parent], period=period)
    return result


def serialized(fn):

    @functools.wraps(fn)
    def call(*args, **kwargs):
        with LOCK:
            return fn(*args, **kwargs)

    return call


MODES = {
    "character_vote": dict(
        name="Character Election",
        master="voteInfo",
        id=50000000008,
        fields=(2, 3),
        status="Original campaign candidates and tickets; private-server vote totals",
    ),
    "lotto_orario": dict(
        name="Lotto Orario",
        master="takarakujiInfo",
        id=56000000011,
        fields=(2, 3),
        status="Private test rules: ticket collection, draw, result and reward mail; original prize odds unavailable",
    ),
    "war_game": dict(
        name="War Game",
        master="pvpInformation",
        id=10000000179,
        fields=(1, 2, 3, 4),
        status="Đã có backend; cần xác nhận trong game",
    ),
    "ultimate_war_game": dict(
        name="Telskyura Abyss",
        master="serialPvpInformation",
        id=81000000050,
        fields=(3, 4, 5, 6),
        status="Đã có backend; cần xác nhận trong game",
        group="telskyura",
    ),
    "telskyura": dict(
        name="Under Telskyura",
        master="underPvpInformation",
        id=69000000054,
        fields=(8, 9, 10, 11),
        status="Backend test: original wire/master; reconstructed scoring; native replay pending",
        group="telskyura",
    ),
    "record_buster": dict(
        name="Record Buster",
        master="adventure",
        id=671500152,
        fields=(6, 7, 8, 9),
        status="Đã có backend; cần xác nhận trong game",
    ),
    "monsterphilia": dict(
        name="Monsterphilia",
        master="adventure",
        id=671200009,
        fields=(6, 7, 8, 9),
        status="Công thức điểm còn là bản tái dựng",
    ),
    "familia_rush": dict(
        name="Familia Rush",
        master="adventure",
        id=672200043,
        fields=(6, 7, 8, 9),
        status="Còn chờ xác nhận lỗi kết thúc trận",
        online=True,
    ),
    "familia_war_game": dict(
        name="Familia War Game",
        master="guildVsGuildInformation",
        id=65000000042,
        fields=(6, 7, 8),
        status="Chốt vòng, kết quả và thư thưởng theo master; ghép cặp/thời lượng vòng còn là tái dựng",
        online=True,
        group="familia_war",
    ),
    "master_familia_war_game": dict(
        name="Master Familia War Game",
        master="guildVsGuildInformation",
        id=65000000039,
        fields=(6, 7, 8),
        status="Bản test; chuyển vòng và thưởng còn dở",
        online=True,
        group="familia_war",
    ),
    "familia_royale": dict(
        name="True Familia Royale",
        master="adventure",
        id=672000016,
        fields=(6, 7, 8, 9),
        status="Bản test: lưu điểm và xếp hạng; công thức tái dựng, thưởng cuối mùa chưa có",
        online=True,
    ),
    "union_raid": dict(
        name="Union Raid",
        master="adventure",
        id=672800001,
        fields=(6, 7, 8, 9),
        status="Bản test: HP liên minh, log, thưởng hạ boss, skills/chat; chưa xác nhận trong game",
        online=True,
    ),
    "shooting_game": dict(
        name="Shooting Game",
        master="shootingGameInformation",
        id=67000000002,
        fields=(3, 4, 6),
        status="Bản test: lưu điểm và xếp hạng ngày; thưởng xếp hạng chưa có",
    ),
}


@serialized
def config(server):
    if server not in ("online", "offline"):
        raise ValueError("Invalid game server")
    shared_path = _paths.SCHEDULE
    if shared_path.exists():
        shared = json.loads(shared_path.read_text("utf-8"))
        inline = shared.get("modeSchedules", {}).get(server)
        if inline is not None:
            inline["modes"].update(PERMANENT)
            return inline
    path = WORK / "admin" / f"modes-{server}.json"
    if path.exists():
        value = json.loads(path.read_text("utf-8"))
        value["modes"].update(PERMANENT)
        return value
    now = int(time.time())
    keys = ["war_game", "ultimate_war_game", "telskyura", "record_buster"]
    if server == "online":
        keys += ["familia_rush", "familia_war_game"]
    value = {
        "modes": {
            key: dict(
                start=now - 86400,
                end=now + 7 * 86400
                if key in ("familia_war_game", "record_buster")
                else 2114356592,
            )
            for key in keys
        }
    }
    value["modes"].update(PERMANENT)
    accounts.write_json(path, value)
    return value


@functools.cache
def seasons(key):
    if key == "war_game":
        return [
            dict(
                id=r.number(0, 8),
                name="Season "
                + str(
                    r.number(0, 8) % 1000
                    if r.number(0, 8) > 10000000000
                    else r.number(0, 8)
                ),
            )
            for r in master.rows(master.master_bytes("pvpInformation"))
        ]
    if key == "ultimate_war_game":
        return [
            dict(id=r.number(0, 8), name=r.text(2))
            for r in master.rows(master.master_bytes("serialPvpInformation"))
        ]
    if key == "telskyura":
        return [
            dict(id=r.number(0, 8), name=r.text(1))
            for r in master.rows(master.master_bytes("underPvpInformation"))
        ]
    if key == "lotto_orario":
        return [
            dict(id=r.number(0, 8), name=r.text(1))
            for r in master.rows(master.master_bytes("takarakujiInfo"))
        ]
    if key == "character_vote":
        return [
            dict(id=r.number(0, 8), name=r.text(1))
            for r in master.rows(master.master_bytes("voteInfo"))
            if any(
                (
                    (
                        master.ROOT
                        / "contents"
                        / str(phase)
                        / "files/icon/vote"
                        / str(r.number(0, 8))
                        / "title.png"
                    ).is_file()
                    for phase in range(1, 9)
                )
            )
        ]
    if key == "familia_war_game":
        return [
            dict(id=r.number(0, 8), name=r.text(2) or f"FWG S{r.number(0, 8) % 1000}")
            for r in master.rows(master.master_bytes("guildVsGuildInformation"))
            if r.number(12) != 1
        ]
    if key == "master_familia_war_game":
        return [
            dict(
                id=r.number(0, 8),
                name=r.text(2) or f"Master of FWG S{r.number(0, 8) % 1000}",
            )
            for r in master.rows(master.master_bytes("guildVsGuildInformation"))
            if r.number(12) == 1
        ]
    if key == "shooting_game":
        return [
            dict(
                id=r.number(0, 8),
                name=r.text(1) or f"Shooting Game S{r.number(0, 8) % 1000}",
            )
            for r in master.rows(master.master_bytes("shootingGameInformation"))
        ]
    if key == "record_buster":
        return [
            dict(
                id=r.number(2, 8),
                name=r.text(4) or f"Record Buster S{r.number(0, 8) % 1000}",
            )
            for r in master.rows(master.master_bytes("pvbInformation"))
        ]
    if key == "familia_rush":
        return [
            dict(
                id=r.number(2, 8),
                name=r.text(3) or f"Familia Rush S{r.number(0, 8) % 1000}",
            )
            for r in master.rows(master.master_bytes("guildRaidInformation"))
        ]
    if key == "familia_royale":
        return [
            dict(
                id=r.number(2, 8),
                name=r.text(3)
                if "True" in r.text(3)
                else f"Familia Royale (S{r.number(2, 8) % 100}): {r.text(3)}",
            )
            for r in master.rows(master.master_bytes("gveInformation"))
            if r.number(2, 8) >= 672000001
        ]
    if key == "union_raid":
        return [
            dict(id=r.number(2, 8), name=r.text(3) or "Grand Familia Battle S1")
            for r in master.rows(master.master_bytes("unionRaidInformation"))
        ]
    return []


def selected_season(key, period=None):
    identifier = (period or {}).get("seasonId", MODES[key]["id"])
    if seasons(key) and (
        type(identifier) is not int or identifier not in {r["id"] for r in seasons(key)}
    ):
        raise ValueError("Unknown archived mode season")
    return identifier


def under_release_deferred(settings, now=None):
    """Whether the selected Under display window needs the pre-Abyss Arena slot."""
    under = settings.get("telskyura")
    if not under or under.get("closed"):
        return False
    now = int(time.time()) if now is None else now
    display_end = under.get(
        "displayEnd", under.get("timestampFields", {}).get("11", under["end"])
    )
    if not 1 < under["start"] <= now < display_end:
        return False
    serial = settings.get("ultimate_war_game")
    return not (
        serial
        and selected_season("ultimate_war_game", serial) == 81000000001
        and (serial["start"] <= now < serial["end"])
    )


def under_release_overlay(serial_data, settings):
    """Honor the original Arena's first-Abyss release gate for archived Under.

    0x1b83ce4..ce8 reaches Under only when first serial start > now.
    A past start hides Under even if that Abyss season has already ended.
    Defer the marker during the selected Under window; keep its interval empty
    so it cannot become a playable or displayed extra Abyss season.
    """
    first = next(
        (r for r in master.rows(serial_data) if r.number(0, 8) == 81000000001), None
    )
    if first is None:
        return
    show_under = under_release_deferred(settings)
    serial = settings.get("ultimate_war_game")
    if (
        serial
        and selected_season("ultimate_war_game", serial) == 81000000001
        and (not show_under)
    ):
        return
    release = 2147483645 if show_under else 1675303200
    for field, value in ((3, release), (4, release), (5, release), (6, release)):
        first.set_number(field, value)


def selected_arena_display(key, period, now):
    """Keep the operator's last selected season visible after battle closes.

    This is a requested private-server display policy. Battle start/end stay
    unchanged; the original masters have separate display-end fields.
    """
    if (
        key not in ("war_game", "record_buster")
        or not period
        or period.get("start", 0) <= 1
        or (period.get("end", 0) <= 2)
    ):
        return period
    field = "4" if key == "war_game" else "9"
    display = period.get("displayEnd", period.get("timestampFields", {}).get(field, 0))
    if period["end"] > now and display:
        return period
    result = dict(
        period,
        displayEnd=min(
            2147483646, max(display, period["end"] + 30 * 86400, now + 30 * 86400)
        ),
    )
    if "timestampFields" in result:
        result["timestampFields"] = dict(
            result["timestampFields"], **{field: result["displayEnd"]}
        )
    return result


def _rewrite(data, key, period):
    spec = MODES[key]
    identifier = selected_season(key, period)
    rows = master.rows(data)
    now = int(time.time())
    if seasons(key) and spec["master"] != "adventure":
        season_ids = {s["id"] for s in seasons(key)}
        for other in rows:
            if other.number(0, 8) in season_ids and other.number(0, 8) != identifier:
                orig_end = (
                    other.number(spec["fields"][1], 8) if len(spec["fields"]) > 1 else 0
                )
                if orig_end > now:
                    for field in spec["fields"]:
                        if other.pos(field):
                            other.set_number(
                                field, 1 if field in spec["fields"][::2] else 2
                            )
    row = next((r for r in rows if r.number(0, 8) == identifier), None)
    if row is None:
        return
    start, end = (period["start"], period["end"]) if period else (1, 2)
    for field in spec["fields"]:
        if row.pos(field):
            row.set_number(field, start if field == spec["fields"][0] else end)
    if key in (
        "war_game",
        "ultimate_war_game",
        "telskyura",
        "record_buster",
        "monsterphilia",
        "familia_rush",
        "familia_royale",
        "union_raid",
    ):
        for field in spec["fields"][2::2]:
            if row.pos(field):
                row.set_number(field, start)
    if key == "war_game" and (period or {}).get("displayEnd"):
        if row.pos(4):
            row.set_number(4, period["displayEnd"])
    if key == "record_buster" and (period or {}).get("displayEnd"):
        if row.pos(9):
            row.set_number(9, period["displayEnd"])
    if key == "ultimate_war_game" and (period or {}).get("displayEnd"):
        if row.pos(6):
            row.set_number(6, period["displayEnd"])
    if key == "telskyura" and (period or {}).get("displayEnd"):
        if row.pos(11):
            row.set_number(11, period["displayEnd"])
    if key in ("familia_war_game", "master_familia_war_game"):
        for field, v in (
            (9, max(1, start - 86400)),
            (10, max(2, start - 1)),
            (11, (period or {}).get("battleStart", start)),
            (8, (period or {}).get("displayEnd", end)),
        ):
            if row.pos(field):
                row.set_number(field, v)
    allowed = set(spec["fields"]) | (
        {9, 10, 11} if key in ("familia_war_game", "master_familia_war_game") else set()
    )
    for field, value in (period or {}).get("timestampFields", {}).items():
        if int(field) in allowed and row.pos(int(field)):
            row.set_number(int(field), value)


def lotto_messages(data, period):
    """Rewrite the four original fixed-date strings; keep all other messages."""
    if not period:
        return

    def pst(stamp):
        return time.strftime("%m/%d %H:%M", time.gmtime(stamp - 8 * 3600))

    draw = period.get("drawAt", min(period["end"] - 1, period["start"] + 300))
    values = {
        40500005: f"{pst(period['start'])} - {pst(draw)} PST",
        40500006: pst(draw) + " PST",
        40500007: "Collect Lotto Orario tickets to participate in the drawing.",
        40500008: "Check your results after the drawing. Prizes are delivered to Gift Box.",
    }
    replace_messages(data, values)


def replace_messages(data, values):
    for row in master.rows(data):
        text = values.get(row.number(0))
        if text is None:
            continue
        encrypted = master.crypt_words(text.encode("utf-8"), True)
        data.extend(bytes(-len(data) % 4))
        target = len(data)
        data.extend(struct.pack("<I", len(encrypted)) + encrypted + b"\x00")
        struct.pack_into("<I", data, row.pos(3), target - row.pos(3))


@functools.lru_cache(maxsize=8)
def overlays(server, signature):
    payload = json.loads(signature)
    from danmemo.game.daily_windows import mission_month as mission_month

    monthly_cycle = payload.get("monthlyWindowCycle", mission_month(time.time()))
    settings = payload["modes"]
    sources = payload.get("sources", {})

    def source(name):
        path = sources.get(f"master/divide_enc/{name}.enc")
        return bytearray(
            zlib.decompress(master.aes_bytes(Path(path).read_bytes()))
            if path
            else master.master_bytes(name)
        )

    blobs = {}
    from danmemo.game import login_bonuses as login_bonuses

    blobs["loginBonus"] = login_bonuses.overlay(
        dict(payload, events=payload.get("events", {})), int(time.time())
    )
    archived_familia = {
        int(parent): details
        for parent, details in payload.get("archiveFamilia", {}).items()
    }
    archived_records = {
        int(parent): details
        for parent, details in payload.get("archiveBusters", {}).items()
    }
    now = int(time.time())
    closed_modes = dict(payload.get("closedModes") or {})
    if not closed_modes:
        epoch = payload.get("historicalEpoch") or now
        from danmemo.config.archive import (
            closed_arena_modes as closed_arena_modes,
            shifted_closed as shifted_closed,
        )

        active_items = [
            dict(key=k, id=selected_season(k, p))
            for k, p in settings.items()
            if p and (not p.get("closed")) and (p.get("end", 0) > now)
        ]
        found_closed = closed_arena_modes(epoch, active_items)
        closed_modes = {
            k: dict(
                id=item["id"],
                parentId=item.get("parentId"),
                period=shifted_closed(item, epoch, now),
            )
            for k, item in found_closed.items()
        }
    closed_record = payload.get("closedRecordBuster") or closed_modes.get(
        "record_buster"
    )
    if closed_record and (not archived_records) and (not settings.get("record_buster")):
        archived_records.setdefault(
            closed_record["parentId"], dict(period=closed_record["period"])
        )
    if server != "online":
        archived_familia = {}
    settings = dict(settings)
    if archived_records and MODES["record_buster"]["id"] not in archived_records:
        settings["record_buster"] = None
    for key, spec in MODES.items():
        if (
            any((r["mode"] == key for r in archived_familia.values()))
            and spec["id"] not in archived_familia
        ):
            settings[key] = None
    for key, spec in MODES.items():
        if spec["master"] not in blobs:
            blobs[spec["master"]] = (
                source(spec["master"])
                if spec["master"] == "adventure"
                else bytearray(master.master_bytes(spec["master"]))
            )
        data = blobs[spec["master"]]
        archive_override = any((r["mode"] == key for r in archived_familia.values()))
        period = (
            None
            if archive_override and spec["id"] not in archived_familia
            else settings.get(key)
        )
        use_closed = period is None or (
            key not in ("war_game", "record_buster")
            and (period.get("closed") or period.get("end", 0) <= now)
        )
        if use_closed and key in closed_modes and (spec["master"] != "adventure"):
            period = closed_modes[key]["period"]
        elif key in ("war_game", "record_buster"):
            period = selected_arena_display(key, period, now)
        if (
            key == "record_buster"
            and period
            and (not period.get("closed"))
            and (period.get("end", 0) > now + 30 * 86400)
        ):
            period = dict(
                period,
                end=period["start"] + 7 * 86400,
                displayEnd=period["start"] + 14 * 86400,
            )
        _rewrite(data, key, period)
    under_release_overlay(blobs["serialPvpInformation"], settings)
    from danmemo.game.fwg_scout_schedule import messages as scout_messages

    scout_text = scout_messages(settings, now)
    if settings.get("lotto_orario") or scout_text:
        messages = source("message")
        lotto_messages(messages, settings.get("lotto_orario"))
        replace_messages(messages, scout_text)
        blobs["message"] = messages
    archived_busters = dict(archived_records)
    archived_busters.update(archived_familia)
    archived_element = {
        int(parent): details
        for parent, details in payload.get("archiveElement", {}).items()
    }
    archived_busters.update(archived_element)
    if archived_busters:
        for row in master.rows(blobs["adventure"]):
            selected = archived_busters.get(row.number(0, 8))
            if not selected:
                continue
            period = selected["period"]
            grace = max(0, row.number(9, 8) - row.number(7, 8))
            for field, value in (
                (6, period["start"]),
                (7, period["end"]),
                (8, period["start"]),
                (9, min(2147483646, period["end"] + grace)),
            ):
                if row.pos(field):
                    row.set_number(field, value)
            for field in (6, 7, 8, 9):
                value = period.get("timestampFields", {}).get(str(field))
                if value is not None and row.pos(field):
                    row.set_number(field, value)
    adventure_ids = {
        selected_season(key, settings.get(key)): settings.get(key)
        for key, spec in MODES.items()
        if spec["master"] == "adventure"
    }
    adventure_ids.update(
        {parent: details["period"] for parent, details in archived_busters.items()}
    )
    episodes = source("adventureEpisode")
    selected = {}
    for row in master.rows(episodes):
        parent = row.number(2, 8)
        if parent in adventure_ids:
            period = adventure_ids[parent] or dict(start=1, end=2)
            selected[row.number(0, 8)] = period
            grace = (
                max(0, row.number(24, 8) - row.number(7, 8)) if row.pos(24) else None
            )
            for field, v in ((6, period["start"]), (7, period["end"])):
                if row.pos(field):
                    row.set_number(field, v)
            if grace is not None:
                row.set_number(24, min(2147483646, period["end"] + grace))
    stages = source("adventureStage")
    for row in master.rows(stages):
        if row.number(1, 8) in selected:
            for field, v in (
                (12, selected[row.number(1, 8)]["start"]),
                (13, selected[row.number(1, 8)]["end"]),
            ):
                if row.pos(field):
                    row.set_number(field, v)
    blobs.update(adventureEpisode=episodes, adventureStage=stages)
    if archived_element:
        periods_by_info = {r["season"]: r["period"] for r in archived_element.values()}
        element_stages = source("elementEventStage")
        for row in master.rows(element_stages):
            period = periods_by_info.get(row.number(1, 8))
            if period:
                for field, value in ((5, period["start"]), (6, period["end"])):
                    if row.pos(field):
                        row.set_number(field, value)
        blobs["elementEventStage"] = element_stages
    shooting = settings.get("shooting_game") or dict(start=1, end=2)
    shooting_id = selected_season("shooting_game", shooting)
    for name, fields in (
        ("shootingGameRule", (3, 4)),
        ("shootingGameDailyRankingReward", (2, 3)),
    ):
        data = source(name)
        for row in master.rows(data):
            if row.number(1, 8) == shooting_id:
                for field, v in zip(fields, (shooting["start"], shooting["end"])):
                    if row.pos(field):
                        row.set_number(field, v)
        blobs[name] = data
    banners = source("pveBannerInformation")
    period = settings.get("monsterphilia") or dict(start=1, end=2)
    for row in master.rows(banners):
        if row.number(1, 8) == MODES["monsterphilia"]["id"]:
            for field, v in ((2, period["start"]), (3, period["end"])):
                if row.pos(field):
                    row.set_number(field, v)
    blobs["pveBannerInformation"] = banners
    war_tags = {}
    information = master.rows(source("guildVsGuildInformation"))
    for key in ("familia_war_game", "master_familia_war_game"):
        sid = selected_season(key, settings.get(key))
        row = next((r for r in information if r.number(0, 8) == sid), None)
        if row:
            war_tags[row.text(1)] = settings.get(key) or dict(start=1, end=2)
    from danmemo.game.event_missions import append_master as append_master

    achievements = append_master(source("achievement"))
    for row in master.rows(achievements):
        period = war_tags.get(row.text(10))
        if row.number(4) == 2706 and period:
            for field, v in ((13, period["start"]), (14, period["end"])):
                if row.pos(field):
                    row.set_number(field, v)
    from danmemo.game.reward_delivery import (
        periods as reward_periods,
        decoded as reward_master,
    )

    active_periods = dict(reward_periods(server))
    if payload.get("historicalMissions"):
        from danmemo.config.historical_mission_schedule import (
            active_periods as hist_active_periods,
        )

        active_periods.update(
            hist_active_periods(
                settings, payload.get("events", {}), payload["historicalMissions"]
            )
        )
    original_achievement_dates = {
        r["0"]: r.get("13", 0) for r in reward_master("achievement")
    }
    from danmemo.game.daily_windows import login_window as login_window

    for row in master.rows(achievements):
        if row.number(2) == 1 and row.number(4) == 602 and (row.number(20) == 1):
            rule = {"13": row.number(13, 8), "14": row.number(14, 8)}
            start, end = login_window(rule, int(time.time()))
            row.set_number(13, start)
            row.set_number(14, end)
        period = active_periods.get(row.text(10)) if row.number(2) == 3 else None
        if "__event_label__:" + row.text(10) in active_periods:
            period = active_periods.get("__achievement__:" + str(row.number(0, 8)))
        if row.number(4) in (901, 902):
            period = active_periods.get(
                "__war_start__:"
                + str(original_achievement_dates.get(row.number(0, 8), 0)),
                period,
            )
        if row.number(2) == 9:
            period = active_periods.get(
                "__union__:" + str(row.number(11) or 413000001), period
            )
        if period:
            for field, v in ((13, period["start"]), (14, period["end"])):
                if row.pos(field):
                    row.set_number(field, v)
    blobs["achievement"] = achievements
    from danmemo.game.reward_delivery import _rule_ranges as _rule_ranges, num as num

    effective = {
        num(rule, 0): (start, end)
        for rule, start, end in _rule_ranges(json.dumps(active_periods, sort_keys=True))
        if num(rule, 2) in (3, 8)
    }
    for row in master.rows(achievements):
        window = effective.get(row.number(0, 8))
        if window:
            for field, stamp in zip((13, 14), window):
                if row.pos(field):
                    row.set_number(field, stamp)
    for row in master.rows(achievements):
        lotto_period = active_periods.get(
            "__campaign_achievement__:" + str(row.number(0, 8)),
            active_periods.get("__lotto_achievement__:" + str(row.number(0, 8))),
        )
        if lotto_period:
            row.set_number(13, lotto_period["start"])
            row.set_number(14, lotto_period["end"])
    monthly = source("monthlyAchievement")
    month_rows = master.rows(monthly)
    latest = max((r.number(3) for r in month_rows))
    current = int(monthly_cycle.replace("-", ""))
    historical_epoch = payload.get("historicalEpoch")
    target_month = (
        int(mission_month(historical_epoch).replace("-", ""))
        if historical_epoch
        else latest
    )
    target_groups = {
        r.number(2): r.number(1) for r in month_rows if r.number(3) == target_month
    }
    for row in month_rows:
        if row.number(3) == (latest if current > latest else current):
            if current > latest:
                row.set_number(3, current, 4)
            tier = row.number(2)
            if tier in target_groups:
                row.set_number(1, target_groups[tier], 4)
    blobs["monthlyAchievement"] = monthly
    familia_achievements = source("familiaAchievement")
    royale = settings.get("familia_royale")
    royale_parent = selected_season("familia_royale", royale) if royale else None
    royale_periods = {
        row.number(0, 8): royale
        for row in master.rows(source("gveInformation"))
        if row.number(2, 8) == royale_parent
    }
    royale_periods.update(
        {
            r["season"]: r["period"]
            for r in archived_familia.values()
            if r["mode"] == "familia_royale"
        }
    )
    for row in master.rows(familia_achievements):
        royale = royale_periods.get(row.number(19, 8))
        if royale:
            for field, value in ((11, royale["start"]), (12, royale["end"])):
                if row.pos(field):
                    row.set_number(field, value)
    blobs["familiaAchievement"] = familia_achievements
    result = {}
    digest = hashlib.sha256(
        ("mode-catalog-v24-mode-mission-windows" + str(current) + signature).encode()
    ).hexdigest()[:16]
    for name, data in blobs.items():
        path = WORK / "admin" / "master" / f"{name}-modes-{server}-{digest}.enc"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
                output.write(master.aes_bytes(zlib.compress(data), True))
                temporary = Path(output.name)
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        result[f"master/divide_enc/{name}.enc"] = path
    return result


def scheduled_overlays(server, sources=None):
    from danmemo.game.daily_windows import (
        catalog_cycle as catalog_cycle,
        mission_month as mission_month,
    )
    from danmemo.game.login_bonuses import settings as login_settings

    bonus_settings = login_settings(server)
    settings = config(server)
    return overlays(
        server,
        json.dumps(
            dict(
                settings,
                archiveBusters=selected_record_buster_events(),
                underReleaseDeferred=under_release_deferred(settings["modes"]),
                loginBonuses=bonus_settings.get("loginBonuses", {}),
                historicalAppliedAt=bonus_settings.get("historicalAppliedAt", 0),
                events=bonus_settings.get("events", {}),
                dailyWindowCycle=catalog_cycle(),
                monthlyWindowCycle=mission_month(time.time()),
                archiveFamilia=selected_familia_events(),
                archiveElement=selected_element_events(),
                sources={
                    k: str(v)
                    for k, v in (sources or {}).items()
                    if k.startswith("master/divide_enc/")
                },
            ),
            sort_keys=True,
        ),
    )
