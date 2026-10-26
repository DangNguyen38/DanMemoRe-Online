"""Original native Casino/Lotto wire with explicitly reconstructed test draws.

Poker hands, double-up and medal exchanges run in the original client and use
user_data/push. casino/ranking/update carries DAILY SCORE, never wallet stock.
Lotto prize probabilities/rewards were not archived; PRIVATE_TEST_PRIZES is a
private test policy, not an assertion about WFS's original lottery algorithm.
"""

import copy
import functools
import hashlib
import json
import secrets
import time
from danmemo.master import reader as master

ROUTES = {"casino/ranking/get", "casino/ranking/update", "takarakuji/get"}
SCHEMA = "CREATE TABLE IF NOT EXISTS casino_daily_scores(\n day INTEGER NOT NULL,uid INTEGER NOT NULL,score INTEGER NOT NULL,\n updated INTEGER NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(day,uid));\nCREATE TABLE IF NOT EXISTS casino_daily_scores_jst(\n day INTEGER NOT NULL,uid INTEGER NOT NULL,score INTEGER NOT NULL,\n updated INTEGER NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(day,uid));"
PRIVATE_TEST_PRIZES = (
    (1, 205000001, 1000),
    (4, 205000001, 400),
    (15, 205000001, 100),
    (80, 205000001, 40),
    (200, 205000002, 1000000),
    (700, 205000002, 10000),
)
NO_PRIZE = 99999999


def integer(value, name, maximum=9223372036854775807):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Invalid " + name)
    return value


@functools.cache
def lotto_catalog():
    return {
        r.number(0, 8): dict(start=r.number(2, 8), end=r.number(3, 8), item=r.number(4))
        for r in master.rows(master.master_bytes("takarakujiInfo"))
    }


def day(stamp):
    return (stamp + 9 * 3600) // 86400


def schedule(oc, identifier):
    from danmemo.config import modes as modes

    original = lotto_catalog().get(identifier)
    if original is None:
        raise ValueError("Unknown Lotto season")
    server = "online" if hasattr(oc, "ONLINE_SHARED") else "offline"
    selected = modes.config(server)["modes"].get("lotto_orario")
    if selected and selected.get("seasonId", 56000000011) == identifier:
        return dict(selected, drawAt=selected.get("drawAt", selected["end"] - 1))
    return None


def stamp(oc, state, tables):
    for table in tables:
        state["tokens"][table] = hashlib.md5(
            oc.msgpack_value(state["tables"][table])
        ).hexdigest()


def ensure_tables(oc, state):
    tables = state["tables"]
    changed = set()
    uid = oc.LOCAL_USER_ID
    defaults = {
        "UserCasinoState": [
            dict(
                userId=uid,
                medalAmount=0,
                signature=0,
                cumulativeMedalAmount=0,
                score=0,
                lastScoreUpdatedAt=0,
            )
        ],
        "UserPokerState": [
            dict(
                userId=uid,
                cards=[],
                exchangedCards=[],
                trashedCards=[],
                betAmount=0,
                phase=0,
                rewardMedalAmount=0,
                openedCard=0,
                doubleUpCount=0,
            )
        ],
    }
    for name, rows in defaults.items():
        if not tables.get(name):
            tables[name] = rows
            changed.add(name)
    for name, source, key in (
        ("UserCasinoMedalRate", "casinoMedalRate", "casinoMedalRateId"),
        ("UserCasinoMedalProduct", "casinoMedalProduct", "casinoMedalProductId"),
    ):
        rows = tables.setdefault(name, [])
        known = {r[key] for r in rows}
        for r in master.rows(master.master_bytes(source)):
            identifier = r.number(0)
            if identifier not in known:
                rows.append(
                    dict(
                        userId=uid,
                        **{key: identifier},
                        count=0,
                        lastExchangedAt=0,
                        readState=1,
                    )
                )
                changed.add(name)
    rounds = state.setdefault("lottoRounds", {})
    for sid, spec in lotto_catalog().items():
        awarded = state.get("lottoTicketAwards", {}).get(str(spec["item"]), 0)
        integer(awarded, "Lotto ticket count", 10000)
        if not awarded:
            continue
        saved = rounds.setdefault(str(sid), dict(tickets=[], settled=False))
        numbers = {r["number"] for r in saved["tickets"]}
        while len(saved["tickets"]) < awarded:
            number = secrets.randbelow(100000000)
            if number in numbers:
                continue
            numbers.add(number)
            saved["tickets"].append(
                dict(userId=uid, takarakujiInfoId=sid, number=number, prize=NO_PRIZE)
            )
        rows = tables.setdefault("UserTakarakujiInfo", [])
        if not any((r["takarakujiInfoId"] == sid for r in rows)):
            rows.append(dict(userId=uid, takarakujiInfoId=sid, playResultEffect=False))
            changed.add("UserTakarakujiInfo")
    stamp(oc, state, changed)
    return changed


def reconcile_push(state, row):
    """Do not let an older native snapshot erase a newly received medal gift."""
    pending = state.get("casinoGiftReconciliation")
    if pending is None:
        return row
    if row.get("medalAmount") == pending["medalAmount"]:
        state.pop("casinoGiftReconciliation", None)
        return row
    result = dict(row)
    for key in ("medalAmount", "cumulativeMedalAmount"):
        result[key] = pending[key]
    return result


def virtual_gift_stock(state, table, iid):
    if table == "CasinoMedal" and iid == 205000006:
        return (
            next(iter(state["tables"].get("UserCasinoState", [])), {}).get(
                "medalAmount", 0
            ),
            999999999,
        )
    if table == "LottoTicket" and iid in {r["item"] for r in lotto_catalog().values()}:
        return (state.get("lottoTicketAwards", {}).get(str(iid), 0), 10000)
    raise ValueError("Unknown Casino/Lotto gift identity")


def prepare(oc):
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        ensure_tables(oc, state)
        state["casinoLottoSchema"] = 1
        oc.write_progress(state)


def settle_round(oc, state, sid, saved, period, now):
    if now < period["drawAt"] or not any(
        (t["prize"] == NO_PRIZE for t in saved["tickets"])
    ):
        return False
    from danmemo.game.reward_delivery import mail as mail

    for ticket in saved["tickets"]:
        if ticket["prize"] != NO_PRIZE:
            continue
        rank, iid, amount = draw_prize()
        ticket["prize"] = rank
        mail(
            oc,
            state,
            f"lotto:{sid}:{ticket['number']}",
            f"Lotto Orario #{sid % 100} - Prize {rank}",
            [(iid, amount)],
            now,
        )
    saved.update(
        settled=True,
        settledAt=now,
        policy="private-test-v1",
        period=dict(period),
        effectPending=True,
    )
    state["lottoRounds"][str(sid)] = saved
    return True


def reconcile(oc, state, now):
    """Scheduler settles earned, scheduled tickets even if the player is offline."""
    if not state.get("lottoTicketAwards"):
        return False
    before = json.dumps(
        (state.get("lottoRounds"), state["tables"].get("UserTakarakujiInfo")),
        sort_keys=True,
    )
    ensure_tables(oc, state)
    for key, saved in list(state["lottoRounds"].items()):
        current = schedule(oc, int(key))
        if (
            current
            and current["start"] <= now <= current["end"]
            and (not saved.get("settled"))
        ):
            saved["period"] = dict(current)
        period = saved.get("period") or current
        if not period:
            continue
        if not saved.get("period") and period["start"] <= now <= period["end"]:
            saved["period"] = dict(period)
        if saved.get("period"):
            settle_round(oc, state, int(key), saved, saved["period"], now)
    return before != json.dumps(
        (state.get("lottoRounds"), state["tables"].get("UserTakarakujiInfo")),
        sort_keys=True,
    )


def ranking(world, action, request, now=None):
    now = int(time.time()) if now is None else now
    with world.connect() as db:
        db.executescript(SCHEMA)
        if action.endswith("/update"):
            score = integer(request.get("medalAmount"), "Casino daily score")
            updated = integer(
                request.get("medalUpdatedAt"), "Casino score time", now + 300
            )
            reason = request.get("reason")
            if type(reason) not in (str, int) or len(str(reason)) > 256:
                raise ValueError("Invalid Casino score reason")
            db.execute(
                "INSERT INTO casino_daily_scores_jst VALUES(?,?,?,?,?) ON CONFLICT(day,uid) DO UPDATE SET score=MAX(score,excluded.score),updated=MAX(updated,excluded.updated),reason=excluded.reason",
                (day(updated), world.uid, score, updated, str(reason)),
            )
            return dict(code=0)
        rows = db.execute(
            "SELECT s.*,p.profile FROM casino_daily_scores_jst s JOIN players p USING(uid) WHERE s.day=? ORDER BY s.score DESC,s.uid LIMIT 10",
            (day(now) - 1,),
        ).fetchall()
        return dict(
            code=0,
            ranking=[
                dict(
                    userId=r["uid"],
                    name=json.loads(r["profile"]).get("name", "Player"),
                    rank=i + 1,
                    score=r["score"],
                )
                for i, r in enumerate(rows)
            ],
        )


def draw_prize():
    draw = secrets.randbelow(sum((p[0] for p in PRIVATE_TEST_PRIZES)))
    for rank, (weight, iid, amount) in enumerate(PRIVATE_TEST_PRIZES, 1):
        draw -= weight
        if draw < 0:
            return (rank, iid, amount)
    raise AssertionError("Unreachable lottery draw")


def lotto(oc, request, now=None):
    sid = integer(request.get("takarakujiInfoId"), "Lotto season")
    period = schedule(oc, sid)
    now = int(time.time()) if now is None else now
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        ensure_tables(oc, state)
        saved = state.setdefault("lottoRounds", {}).setdefault(
            str(sid), dict(tickets=[], settled=False)
        )
        play = False
        if period and period["start"] <= now <= period["end"]:
            saved.setdefault("period", dict(period))
            settle_round(oc, state, sid, saved, period, now)
        play = saved.pop("effectPending", False)
        info = dict(
            userId=oc.LOCAL_USER_ID, takarakujiInfoId=sid, playResultEffect=play
        )
        rows = state["tables"].setdefault("UserTakarakujiInfo", [])
        rows[:] = [r for r in rows if r["takarakujiInfoId"] != sid] + [info]
        stamp(oc, state, ["UserTakarakujiInfo"])
        oc.write_progress(state)
        data = {
            t: copy.deepcopy(state["tables"][t])
            for t in ("UserTakarakujiInfo", "UserGift")
            if t in state["tables"]
        }
        return dict(
            code=0,
            UserTakarakujiInfo=info,
            player=dict(
                prize=min((t["prize"] for t in saved["tickets"]), default=NO_PRIZE)
            ),
            tickets=copy.deepcopy(saved["tickets"]),
            data=data,
            dataTokens={t: state["tokens"][t] for t in data},
            operations=[],
        )


def dispatch(world, action, request, oc=None):
    if action == "takarakuji/get":
        if oc is None:
            raise ValueError("Lotto needs an account backend")
        return lotto(oc, request)
    return ranking(world, action, request)
