"""Archived login calendars and native pending-presentation records.

Native layouts/PLAYED=1/PENDING=2 and mailbox presentation are recovered.
The daily scheduler, title-based manual-event binding and fallback returning
campaign are private-server policies; see the implementation report.
"""

import copy
import functools
import hashlib
import json
import re
import time
from danmemo.master import reader as master
from danmemo.game import resources as resources
from danmemo.game.daily_windows import mission_day as mission_day


@functools.cache
def catalog():
    return tuple(
        (
            dict(
                id=r.number(0),
                key=r.text(1),
                title=r.text(2),
                kind=r.number(3),
                loop=bool(r.number(4)),
                days=r.number(5),
                start=r.number(6, 8),
                end=r.number(7, 8),
                exclude=r.number(9),
                absence=r.number(11),
                duration=r.number(12),
            )
            for r in master.rows(master.master_bytes("loginBonus"))
        )
    )


@functools.cache
def rewards():
    result = {}
    for r in master.rows(master.master_bytes("loginBonusContent")):
        result.setdefault(r.text(1), {})[r.number(3), r.number(2)] = dict(
            key=r.text(4), amount=r.number(5), kind=r.number(6), id=r.number(0)
        )
    return result


@functools.cache
def identities():
    result = {}
    for name in (*resources.NAMES, "voteItem", "skipTicket"):
        table = resources.table_name(name)
        for r in master.rows(master.master_bytes(name)):
            result[r.text(2 if name == "memoriaExpItem" else 1)] = (table, r.number(0))
    result.update(
        {
            r.text(8): ("UserPC", r.number(0))
            for r in master.rows(master.master_bytes("pc"))
        }
    )
    result.update(
        {
            r.text(13): ("UserCharacterCostume", r.number(0))
            for r in master.rows(master.master_bytes("characterCostume"))
        }
    )
    return result


def unresolved_rewards():
    return {
        r["key"]
        for calendar in rewards().values()
        for r in calendar.values()
        if r["key"] not in identities()
    }


@functools.cache
def _known_items():
    return frozenset(identities().values())


def known_item(table, iid):
    return (table, iid) in _known_items()


def snapshot(server):
    config = settings(server)
    now = int(time.time())
    selected = periods(config, now)
    return [
        dict(
            r,
            period=selected.get(r["id"]),
            historicalManaged=bool(config.get("historicalEpoch") and r["kind"] == 2),
            hasArtwork=any(
                (
                    (
                        master.ROOT
                        / "contents"
                        / str(p)
                        / "files/login_bonus"
                        / f"{r['id']}.png"
                    ).is_file()
                    for p in range(1, 9)
                )
            ),
        )
        for r in catalog()
    ]


def update(request):
    from danmemo.config import schedule as offline_admin
    from danmemo.storage import atomic as offline_accounts

    bid = request.get("id")
    if type(bid) is not int or not any(
        (r["id"] == bid and r["kind"] in (2, 3) for r in catalog())
    ):
        raise ValueError("Select an original campaign login bonus")
    period = None
    if request.get("enabled", True):
        start, end = (request.get("start"), request.get("end"))
        if (
            type(start) is not int
            or type(end) is not int
            or (not 0 < start < end < 2147483647)
        ):
            raise ValueError("Invalid login bonus period")
        period = dict(start=start, end=end)
    value = offline_admin.config()
    value.setdefault("loginBonuses", {})[str(bid)] = period
    backup = (
        offline_admin.CONFIG.parent / "backups/login-bonuses" / f"{time.time_ns()}.json"
    )
    offline_accounts.write_json(
        backup,
        value
        if not offline_admin.CONFIG.exists()
        else json.loads(offline_admin.CONFIG.read_text("utf-8")),
    )
    offline_accounts.write_json(offline_admin.CONFIG, value)
    return dict(
        message="Đã lưu lịch Login Bonus. Về màn hình khởi động để tải lịch và nhận quà."
    )


def settings(server):
    from danmemo.config import schedule as offline_admin
    from danmemo.config import modes as admin_game_modes

    shared = offline_admin.config()
    result = dict(
        admin_game_modes.config(server),
        events=shared.get("events", {}),
        loginBonuses=shared.get("loginBonuses", {}),
    )
    history = shared.get("historicalSchedule", {})
    if result.get("historicalEpoch") and history.get("server") == server:
        result["historicalAppliedAt"] = history.get("appliedAt", 0)
    return result


def _name(value):
    value = re.sub("login\\s*bonus|\\(JP\\)|ログインボーナス", "", value, flags=re.I)
    return re.sub("[^\\w]", "", value).casefold()


@functools.cache
def event_bindings():
    """Exact normalized archived titles AND overlapping original windows only."""
    parents = {r.number(0, 8): r for r in master.rows(master.master_bytes("adventure"))}
    result = {}
    for ep in master.rows(master.master_bytes("adventureEpisode")):
        parent = parents.get(ep.number(2, 8))
        names = {_name(ep.text(4))}
        if parent:
            names.add(_name(parent.text(4)))
        for bonus in catalog():
            if bonus["kind"] != 2 or _name(bonus["title"]) not in names:
                continue
            start, end = (ep.number(6, 8), ep.number(7, 8))
            if (not start or not bonus["end"] or start < bonus["end"]) and (
                not end or bonus["start"] < end
            ):
                result.setdefault(str(ep.number(0, 8)), set()).add(bonus["id"])
    return result


def periods(config, now):
    epoch = config.get("historicalEpoch") or now
    anchor = config.get("historicalAppliedAt") or now
    shift = anchor - epoch if config.get("historicalEpoch") else 0
    result = {}
    for r in catalog():
        clock = epoch if r["kind"] == 2 else now
        offset = shift if r["kind"] == 2 else 0
        if (
            r["kind"] in (0, 2)
            and (not r["start"] or r["start"] <= clock)
            and (not r["end"] or clock < r["end"])
        ):
            result[r["id"]] = dict(
                start=r["start"] + offset if r["start"] else 0,
                end=r["end"] + offset if r["end"] else 0,
            )
    if not config.get("historicalEpoch"):
        for eid, period in config.get("events", {}).items():
            for bid in event_bindings().get(eid, ()):
                result[bid] = dict(period)
    kinds = {r["id"]: r["kind"] for r in catalog()}
    for bid, period in config.get("loginBonuses", {}).items():
        if config.get("historicalEpoch") and kinds.get(int(bid)) == 2:
            continue
        if period:
            result[int(bid)] = dict(period)
        else:
            result.pop(int(bid), None)
    returning = [
        r
        for r in catalog()
        if r["kind"] == 3 and r["start"] <= now and (r["end"] > r["start"])
    ]
    if returning and str(returning[-1]["id"]) not in config.get("loginBonuses", {}):
        result[returning[-1]["id"]] = dict(start=0, end=0)
    return result


def overlay(config, now):
    data = bytearray(master.master_bytes("loginBonus"))
    selected = periods(config, now)
    for row in master.rows(data):
        period = selected.get(row.number(0))
        if not period and config.get("historicalEpoch") and (row.number(3) == 2):
            period = dict(start=2114356593, end=2114356594)
        if period:
            for f, name in ((6, "start"), (7, "end")):
                if row.pos(f):
                    row.set_number(f, period.get(name, 0))
    return data


def _stamp(oc, state, tables):
    for table in tables:
        state["tokens"][table] = hashlib.md5(
            oc.msgpack_value(state["tables"][table])
        ).hexdigest()


def prepare(oc, now=None):
    from danmemo.game import gifts as gifts

    now = int(time.time() if now is None else now)
    config = settings("online" if hasattr(oc, "ONLINE_SHARED") else "offline")
    selected = periods(config, now)
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        tables = state["tables"]
        changed = set()
        status = next(iter(tables.get("UserStatus", [])), {})
        ledger = state.get("loginBonusLedger", {})
        rows = {r["loginBonusId"]: r for r in tables.get("UserLoginBonus", [])}
        progress = {
            r["loginBonusId"]: r for r in tables.get("UserLoginBonusProgress", [])
        }
        created = status.get("createdAt", 0)
        info = next(iter(tables.get("UserInfo", [])), {})
        if (
            not status
            and (not state.get("dailyLogin"))
            and (info.get("playerStatus", {}).get("level", 1) <= 1)
            and (
                not any(
                    (r.get("state") == 4 for r in tables.get("UserAdventureStage", []))
                )
            )
        ):
            created = now
        beginner = next((r for r in catalog() if r["kind"] == 1))
        is_beginner = bool(
            created and 0 <= mission_day(now) - mission_day(created) < beginner["days"]
        )
        last_login = status.get("lastLoginedAt") or status.get("lastLoginedDateAt", 0)
        for rule in catalog():
            bid = rule["id"]
            period = selected.get(bid)
            if rule["kind"] == 1:
                if not is_beginner:
                    continue
                period = dict(
                    start=created,
                    end=(mission_day(created) + rule["days"]) * 86400 - 32400,
                )
            if (
                not period
                or period.get("start", 0) > now
                or (period.get("end", 0) and now >= period["end"])
            ):
                continue
            if (
                rule["kind"] == 0
                and is_beginner
                and (rule["exclude"] == beginner["id"])
            ):
                continue
            previous = rows.get(bid)
            if rule["kind"] == 3:
                duration = rule["duration"] or rule["days"]
                if previous and previous.get("closedAt", 0) > now:
                    period = dict(start=previous["openedAt"], end=previous["closedAt"])
                elif last_login and now - last_login >= max(1, rule["absence"]) * 86400:
                    if ledger.get(str(bid), {}).get("returnFrom") == last_login:
                        continue
                    period = dict(start=now, end=now + duration * 86400)
                    previous = None
                else:
                    continue
            previous = previous or dict(
                userId=oc.LOCAL_USER_ID,
                loginBonusId=bid,
                lastReceivedAt=0,
                receiptCount=0,
                lappedCount=1,
                userLoginBonusPlayableState=1,
                openedAt=period.get("start") or now,
                closedAt=period.get("end", 0),
            )
            if previous["lastReceivedAt"] and mission_day(
                previous["lastReceivedAt"]
            ) >= mission_day(now):
                continue
            count = previous["receiptCount"]
            lap = max(1, previous["lappedCount"])
            if count >= rule["days"]:
                if not rule["loop"]:
                    continue
                count = 0
                lap += 1
            reward = rewards().get(rule["key"], {}).get(
                (lap, count + 1)
            ) or rewards().get(rule["key"], {}).get((1, count + 1))
            if not reward or reward["key"] not in identities():
                continue
            table, iid = identities()[reward["key"]]
            if not changed:
                state = copy.deepcopy(state)
                tables = state["tables"]
                rows = {r["loginBonusId"]: r for r in tables.get("UserLoginBonus", [])}
                progress = {
                    r["loginBonusId"]: r
                    for r in tables.get("UserLoginBonusProgress", [])
                }
                ledger = state.setdefault("loginBonusLedger", {})
            row = dict(previous)
            gift = gifts.enqueue(
                oc, state, oc.LOCAL_USER_ID, table, iid, reward["amount"], now
            )
            from danmemo.content.reward_text import text as text

            title = text(
                "home_other_login_bonus_reward_text",
                loginBonusName=rule["title"],
                day=count + 1,
            )
            gift.update(title=title, message=title, giftAcquiredType=2)
            row.update(
                lastReceivedAt=now,
                receiptCount=count + 1,
                lappedCount=lap,
                userLoginBonusPlayableState=2,
                closedAt=period.get("end", 0),
            )
            rows[bid] = row
            old = progress.get(bid, {})
            progress[bid] = dict(
                userId=oc.LOCAL_USER_ID,
                loginBonusId=bid,
                lastReceivedAt=now,
                count=old.get("count", 0) + 1,
            )
            ledger[str(bid)] = dict(
                day=mission_day(now),
                giftId=gift["id"],
                contentId=reward["id"],
                returnFrom=last_login if rule["kind"] == 3 else 0,
            )
            changed.update(("UserLoginBonus", "UserLoginBonusProgress", "UserGift"))
        if changed:
            tables["UserLoginBonus"] = list(rows.values())
            tables["UserLoginBonusProgress"] = list(progress.values())
            _stamp(oc, state, changed)
            oc.write_progress(state)
        return len(changed)


def reconcile(table, row, previous):
    """Only acknowledge native presentation; the client cannot mint a day."""
    if not previous:
        return None
    expected = dict(previous)
    if table == "UserLoginBonus" and row.get("userLoginBonusPlayableState") == 1:
        if all(
            (
                row.get(k) == v
                for k, v in previous.items()
                if k != "userLoginBonusPlayableState"
            )
        ):
            expected["userLoginBonusPlayableState"] = 1
    return expected
