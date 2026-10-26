"""FWG round settlement from recovered client/master contracts.

Mail channels, prize rows, win-bonus rows and yesterday-result fields are
original evidence. Daily matching, configurable battles and inter-round
preparation, draw refusal and the existing
600s unfinished-battle lease are documented private-server reconstruction.
No historical wins or rewards are fabricated at migration.
"""

import functools
import json
import time
from danmemo.config import modes as modes
from danmemo.master import reader as master

REGULAR = 65000000042
SCHEMA = "\nCREATE TABLE IF NOT EXISTS familia_war_rounds(\n season INTEGER NOT NULL, round_no INTEGER NOT NULL, starts INTEGER NOT NULL,\n ends INTEGER NOT NULL, snapshot TEXT NOT NULL, results TEXT,\n PRIMARY KEY(season,round_no));\nCREATE TABLE IF NOT EXISTS familia_war_cadence(\n season INTEGER PRIMARY KEY, anchor_round INTEGER NOT NULL,\n anchor_start INTEGER NOT NULL, preparation_seconds INTEGER NOT NULL,\n battle_seconds INTEGER NOT NULL DEFAULT 86400);\nCREATE TABLE IF NOT EXISTS familia_war_preparations(\n season INTEGER NOT NULL,round_no INTEGER NOT NULL,starts INTEGER NOT NULL,\n ends INTEGER NOT NULL,pairs TEXT NOT NULL,PRIMARY KEY(season,round_no));\n"


def policy(sid):
    cfg = modes.config("online")["modes"]
    fwg = cfg.get("familia_war_game")
    if fwg and fwg.get("lifecycle") == "original-contract-v1":
        if sid == fwg.get("seasonId", REGULAR) or sid in {
            s["id"] for s in modes.seasons("familia_war_game")
        }:
            return fwg
    mfwg = cfg.get("master_familia_war_game")
    if mfwg and mfwg.get("lifecycle") == "original-contract-v1":
        if sid == mfwg.get("seasonId", 65000000039) or sid in {
            s["id"] for s in modes.seasons("master_familia_war_game")
        }:
            return mfwg
    return None


def window(db, sid, now=None):
    p = policy(sid)
    if p is None:
        return None
    now = int(time.time()) if now is None else int(now)
    first = int(p.get("battleStart", p["start"]))
    row = db.execute(
        "SELECT preparation_end FROM familia_war_phases WHERE season=?", (sid,)
    ).fetchone()
    if "battleStart" not in p and row and (row[0] > first):
        first = row[0]
    end = p["end"]
    display = max(end, int(p.get("displayEnd", end + 86400)))
    prep = int(p.get("roundPreparationSeconds", 0))
    columns = {r[1] for r in db.execute("PRAGMA table_info(familia_war_cadence)")}
    if "battle_seconds" not in columns:
        db.execute(
            "ALTER TABLE familia_war_cadence ADD COLUMN battle_seconds INTEGER NOT NULL DEFAULT 86400"
        )
    cadence = db.execute(
        "SELECT * FROM familia_war_cadence WHERE season=?", (sid,)
    ).fetchone()
    if prep > 0 and cadence is None:
        latest = db.execute(
            "SELECT round_no,ends FROM familia_war_rounds WHERE season=? ORDER BY round_no DESC LIMIT 1",
            (sid,),
        ).fetchone()
        number = latest["round_no"] + 1 if latest else 0
        anchor = latest["ends"] + prep if latest else first
        db.execute(
            "INSERT INTO familia_war_cadence VALUES(?,?,?,?,?)",
            (sid, number, anchor, prep, int(p.get("roundBattleSeconds", 86400))),
        )
        cadence = db.execute(
            "SELECT * FROM familia_war_cadence WHERE season=?", (sid,)
        ).fetchone()
    if (
        cadence
        and int(p.get("roundBattleSeconds", cadence["battle_seconds"]))
        != cadence["battle_seconds"]
    ):
        latest = db.execute(
            "SELECT round_no,ends FROM familia_war_rounds WHERE season=? ORDER BY round_no DESC LIMIT 1",
            (sid,),
        ).fetchone()
        number = latest["round_no"] + 1 if latest else cadence["anchor_round"]
        anchor = (
            latest["ends"] + cadence["preparation_seconds"]
            if latest
            else cadence["anchor_start"]
        )
        db.execute(
            "UPDATE familia_war_cadence SET anchor_round=?,anchor_start=?,battle_seconds=? WHERE season=?",
            (number, anchor, int(p["roundBattleSeconds"]), sid),
        )
        cadence = db.execute(
            "SELECT * FROM familia_war_cadence WHERE season=?", (sid,)
        ).fetchone()
    extra = dict(cadence=dict(cadence)) if cadence else {}

    def state(term, next_time, index=None, **fields):
        return dict(
            term=term,
            nextTermTime=next_time,
            first=first,
            end=end,
            index=index,
            **extra,
            **fields,
        )

    def preparing(number, start, duration):
        if start >= end:
            return state(1, end)
        return state(
            1,
            start,
            preparation=dict(index=number, start=start, end=min(start + duration, end)),
        )

    if cadence:
        if now < p["start"]:
            return state(0, p["start"])
        if now >= end:
            return state(3 if now < display else 0, display)
        current = db.execute(
            "SELECT * FROM familia_war_rounds WHERE season=? AND starts<=? AND ends>? ORDER BY round_no DESC LIMIT 1",
            (sid, now, now),
        ).fetchone()
        if current:
            return state(
                2,
                min(current["ends"], end),
                current["round_no"],
                roundStart=current["starts"],
                roundEnd=min(current["ends"], end),
            )
        anchor = cadence["anchor_start"]
        duration = cadence["battle_seconds"] + cadence["preparation_seconds"]
        if now < anchor:
            return preparing(cadence["anchor_round"], anchor, cadence["battle_seconds"])
        offset = (now - anchor) // duration
        start = anchor + offset * duration
        finish = min(start + cadence["battle_seconds"], end)
        if now >= finish:
            return preparing(
                cadence["anchor_round"] + offset + 1,
                start + duration,
                cadence["battle_seconds"],
            )
        return state(
            2,
            finish,
            cadence["anchor_round"] + offset,
            roundStart=start,
            roundEnd=finish,
        )
    if now < p["start"]:
        return dict(term=0, nextTermTime=p["start"], first=first, end=end, index=None)
    if now >= end:
        return dict(
            term=3 if now < display else 0,
            nextTermTime=display,
            first=first,
            end=end,
            index=None,
        )
    if now < first:
        return preparing(0, first, 86400)
    index = (now - first) // 86400
    return dict(
        term=2,
        nextTermTime=min(first + (index + 1) * 86400, end),
        first=first,
        end=end,
        index=index,
    )


@functools.cache
def reward_rows():
    return master.rows(master.master_bytes("guildVsGuildFamiliaVersusReward"))


@functools.cache
def bonus_rows():
    return master.rows(master.master_bytes("guildVsGuildWinBonus"))


def results(db, sid):
    return [
        json.loads(r["results"])
        for r in db.execute(
            "SELECT results FROM familia_war_rounds WHERE season=? AND results IS NOT NULL ORDER BY round_no",
            (sid,),
        )
    ]


def battle_bounds(db, sid, stamp=None):
    now = int(time.time()) if stamp is None else int(stamp)
    state = window(db, sid, now)
    if state is None:
        return None
    if "cadence" in state:
        row = db.execute(
            "SELECT starts,ends,snapshot FROM familia_war_rounds WHERE season=? AND starts<=? AND ends>? ORDER BY round_no DESC LIMIT 1",
            (sid, now, now),
        ).fetchone()
        if row:
            start = row["starts"]
            members = json.loads(row["snapshot"]).values()
            return (
                max(
                    start,
                    min(
                        (r.get("acceptedAfter", start) for r in members), default=start
                    ),
                ),
                row["ends"],
            )
        if state["index"] is not None:
            return (state["roundStart"], state["roundEnd"])
        return (now, now)
    first = state["first"]
    end = state["end"]
    position = max(0, (min(now, end - 1) - first) // 86400)
    start = first + position * 86400
    finish = min(first + (position + 1) * 86400, end)
    row = db.execute(
        "SELECT starts,snapshot FROM familia_war_rounds WHERE season=? AND round_no=?",
        (sid, position),
    ).fetchone()
    if row and row["starts"] == start:
        members = json.loads(row["snapshot"]).values()
        start = max(
            start, min((r.get("acceptedAfter", start) for r in members), default=start)
        )
    return (start, finish)


def finished_season(db, sid, now):
    state = window(db, sid, now)
    return bool(
        state
        and now >= state["end"]
        and (
            not db.execute(
                "SELECT 1 FROM familia_war_rounds WHERE season=? AND results IS NULL LIMIT 1",
                (sid,),
            ).fetchone()
        )
    )


def standing(db, sid, fid):
    settled = [r[str(fid)] for r in results(db, sid) if str(fid) in r]
    return dict(
        wins=sum((r["outcome"] == "win" for r in settled)),
        losses=sum((r["outcome"] == "lose" for r in settled)),
        bonus=sum((r["winBonus"] for r in settled)),
    )


def _points(db, sid, fid, start=None, end=None):
    query = "SELECT start_response,response FROM familia_war_battles WHERE season=? AND familia=? AND finished IS NOT NULL"
    args = [sid, fid]
    if start is not None:
        row = db.execute(
            "SELECT snapshot FROM familia_war_rounds WHERE season=? AND starts=? AND ends=?",
            (sid, start, end),
        ).fetchone()
        if row:
            start = max(
                start,
                json.loads(row["snapshot"])
                .get(str(fid), {})
                .get("acceptedAfter", start),
            )
        query += " AND started>=? AND started<?"
        args.extend((start, end))
    total = captain = deputy = 0
    for r in db.execute(query, args):
        value = json.loads(r["response"])["gvgResult"]
        gain = value.get("point", 0)
        total += gain
        if value.get("isCaptain"):
            captain += gain
        elif value.get("isSubCaptain"):
            deputy += gain
    return dict(totalPoint=total, captainPoint=captain, subCaptainPoint=deputy)


def _settle(world, db, sid, row, now):
    if row["results"] is not None or now < row["ends"]:
        return
    pending = db.execute(
        "SELECT MAX(started+600) FROM familia_war_battles WHERE season=? AND started>=? AND started<? AND finished IS NULL",
        (sid, row["starts"], row["ends"]),
    ).fetchone()[0]
    if pending and now < pending:
        return
    snapshot = json.loads(row["snapshot"])
    out = {}
    for fid, entry in snapshot.items():
        enemy = entry["enemy"]
        if enemy is None:
            continue
        own = _points(db, sid, int(fid), row["starts"], row["ends"])
        other = _points(db, sid, enemy, row["starts"], row["ends"])
        present = {
            r[0]
            for r in db.execute(
                "SELECT id FROM familias WHERE id IN (?,?)", (int(fid), enemy)
            )
        }
        if int(fid) not in present:
            outcome = "lose"
        elif enemy not in present:
            outcome = "win"
        elif own["totalPoint"] > other["totalPoint"]:
            outcome = "win"
        elif own["totalPoint"] < other["totalPoint"]:
            outcome = "lose"
        else:
            outcome = "draw"
        won = standing(db, sid, int(fid))["wins"] + int(outcome == "win")
        target = next(
            (
                r.number(3)
                for r in reversed(bonus_rows())
                if r.number(1, 8) == sid and r.number(2) <= won
            ),
            0,
        )
        bonus = (
            max(0, target - standing(db, sid, int(fid))["bonus"])
            if outcome == "win"
            else 0
        )
        participants = [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT actor FROM familia_war_battles WHERE season=? AND familia=? AND started>=? AND started<? AND finished IS NOT NULL",
                (
                    sid,
                    int(fid),
                    max(row["starts"], entry.get("acceptedAfter", row["starts"])),
                    row["ends"],
                ),
            )
            if r[0] in entry["members"]
        ]
        out[fid] = dict(
            outcome=outcome,
            enemy=enemy,
            points=own,
            enemyPoints=other,
            enemyProfile=snapshot[str(enemy)]["profile"],
            leagueId=entry["leagueId"],
            participants=participants,
            winBonus=bonus,
            settledAt=now,
        )
    db.execute(
        "UPDATE familia_war_rounds SET results=? WHERE season=? AND round_no=? AND results IS NULL",
        (json.dumps(out), sid, row["round_no"]),
    )


def _pairings(ordered, preferred, previous):
    """Private-server policy: rank-near pairs, avoiding last-round rematches.

    The original client receives enemyFamilia from the server; no publisher
    matching algorithm was recovered. Two eligible Familias must still be
    allowed to meet again. Never use this to rewrite an existing round.
    """
    waiting = list(ordered)
    pairs = []
    while waiting:
        fid = waiting.pop(0)
        enemy = preferred.get(fid)
        if enemy not in waiting:
            enemy = next(
                (other for other in waiting if previous.get(fid) != other),
                waiting[0] if waiting else None,
            )
        if enemy is not None:
            waiting.remove(enemy)
        pairs.append((fid, enemy))
    positions = {fid: i for i, fid in enumerate(ordered)}
    for index, (a, b) in enumerate(pairs):
        if b is None or previous.get(a) != b:
            continue
        options = []
        for other_index, (c, d) in enumerate(pairs):
            if other_index == index or d is None:
                continue
            for left, right in (((a, c), (b, d)), ((a, d), (b, c))):
                if all((previous.get(x) != y for x, y in (left, right))):
                    distance = sum(
                        (abs(positions[x] - positions[y]) for x, y in (left, right))
                    )
                    options.append((distance, other_index, left, right))
        if options:
            _, other_index, left, right = min(options)
            pairs[index] = left
            pairs[other_index] = right
    return pairs


def sync(world, db, sid, now=None):
    now = int(time.time()) if now is None else int(now)
    state = window(db, sid, now)
    if state is None:
        return None
    for row in db.execute(
        "SELECT * FROM familia_war_rounds WHERE season=?", (sid,)
    ).fetchall():
        if "cadence" in state:
            continue
        expected = state["first"] + row["round_no"] * 86400
        if row["starts"] != expected or row["ends"] != min(
            expected + 86400, state["end"]
        ):
            pending = db.execute(
                "SELECT 1 FROM familia_war_battles WHERE season=? AND started>=? AND started<? AND finished IS NULL AND started>? LIMIT 1",
                (sid, row["starts"], row["ends"], now - 600),
            ).fetchone()
            if pending:
                return state
            db.execute(
                "DELETE FROM familia_war_rounds WHERE season=? AND round_no=?",
                (sid, row["round_no"]),
            )
    if "cadence" not in state:
        db.execute(
            "DELETE FROM familia_war_rounds WHERE season=? AND results='{}'", (sid,)
        )
    for row in db.execute(
        "SELECT * FROM familia_war_rounds WHERE season=? AND results IS NULL AND ends<=? ORDER BY round_no",
        (sid, now),
    ).fetchall():
        _settle(world, db, sid, row, now)
    preparation = state.get("preparation")
    if state["index"] is None and (not preparation):
        return state
    if db.execute(
        "SELECT 1 FROM familia_war_rounds WHERE season=? AND results IS NULL AND ends<=? LIMIT 1",
        (sid, now),
    ).fetchone():
        return state
    number = preparation["index"] if preparation else state["index"]
    start = (
        preparation["start"]
        if preparation
        else state.get("roundStart", state["first"] + number * 86400)
    )
    end = (
        preparation["end"]
        if preparation
        else state.get("roundEnd", min(start + 86400, state["end"]))
    )
    if db.execute(
        "SELECT 1 FROM familia_war_rounds WHERE season=? AND round_no=?", (sid, number)
    ).fetchone():
        return state
    prepared = db.execute(
        "SELECT * FROM familia_war_preparations WHERE season=? AND round_no=? AND starts=? AND ends=?",
        (sid, number, start, end),
    ).fetchone()
    if preparation and prepared:
        entrants = {
            str(r[0])
            for r in db.execute(
                "SELECT DISTINCT m.familia FROM members m JOIN familias f ON f.id=m.familia JOIN familia_war_parties p USING(uid) WHERE m.joined<=?",
                (start,),
            )
        }
        if entrants == set(json.loads(prepared["pairs"])):
            return state
    from danmemo.game.familia_war import league as league

    clans = {}
    for row in db.execute(
        "SELECT f.id,f.name,f.owner,m.uid FROM familias f JOIN members m ON m.familia=f.id JOIN familia_war_parties p ON p.uid=m.uid WHERE m.joined<=? ORDER BY f.id,m.uid",
        (start,),
    ):
        fid = row["id"]
        if fid not in clans:
            profile = world.profile(db, row["owner"])
            clans[fid] = dict(
                members=[],
                profile=dict(
                    id=fid, name=row["name"], leaderPcId=profile["leaderPc"]["pcId"]
                ),
            )
        clans[fid]["members"].append(row["uid"])
    ordered = sorted(
        clans,
        key=lambda fid: (
            -standing(db, sid, fid)["wins"],
            -(_points(db, sid, fid)["totalPoint"] + standing(db, sid, fid)["bonus"]),
            fid,
        ),
    )
    snapshot = {}
    old = {
        r["familia"]: r["enemy"]
        for r in db.execute("SELECT * FROM familia_war_matches WHERE season=?", (sid,))
    }
    previous = {}
    regular = any((s["id"] == sid for s in modes.seasons("familia_war_game")))
    for row in db.execute(
        "SELECT round_no,starts,snapshot FROM familia_war_rounds WHERE season=? AND round_no<? AND results IS NOT NULL ORDER BY round_no DESC",
        (sid, number),
    ):
        if regular and (
            "cadence" in state
            or row["starts"] == state["first"] + row["round_no"] * 86400
        ):
            previous = {
                int(fid): entry["enemy"]
                for fid, entry in json.loads(row["snapshot"]).items()
            }
            break
    pairs = []
    remaining = set(ordered)
    if prepared:
        preferred = json.loads(prepared["pairs"])
        for fid in ordered:
            enemy = preferred.get(str(fid))
            if (
                fid in remaining
                and enemy != fid
                and (enemy in remaining)
                and (preferred.get(str(enemy)) == fid)
            ):
                pairs.append((fid, enemy))
                remaining.difference_update((fid, enemy))
    pairs.extend(
        _pairings(
            [fid for fid in ordered if fid in remaining],
            old if not results(db, sid) else {},
            previous,
        )
    )
    if preparation:
        mapping = {
            str(own): opponent
            for fid, enemy in pairs
            for own, opponent in (
                ((fid, enemy), (enemy, fid)) if enemy is not None else ((fid, None),)
            )
        }
        db.execute(
            "INSERT OR REPLACE INTO familia_war_preparations VALUES(?,?,?,?,?)",
            (sid, number, start, end, json.dumps(mapping)),
        )
        return state
    for fid, enemy in pairs:
        for own, opponent in (
            ((fid, enemy), (enemy, fid)) if enemy is not None else ((fid, None),)
        ):
            snapshot[str(own)] = dict(
                clans[own],
                enemy=opponent,
                leagueId=league(sid, ordered.index(own) + 1),
                acceptedAfter=now,
            )
    db.execute(
        "INSERT INTO familia_war_rounds VALUES(?,?,?,?,?,NULL)",
        (sid, number, start, end, json.dumps(snapshot)),
    )
    return state


def opponent(db, sid, fid, state):
    if state["index"] is None:
        preparation = state.get("preparation")
        if not preparation:
            return None
        row = db.execute(
            "SELECT pairs FROM familia_war_preparations WHERE season=? AND round_no=? AND starts=? AND ends=?",
            (sid, preparation["index"], preparation["start"], preparation["end"]),
        ).fetchone()
        enemy = json.loads(row["pairs"]).get(str(fid)) if row else None
        return (
            enemy
            if enemy is not None
            and db.execute("SELECT 1 FROM familias WHERE id=?", (enemy,)).fetchone()
            else None
        )
    row = db.execute(
        "SELECT starts,snapshot,results FROM familia_war_rounds WHERE season=? AND round_no=?",
        (sid, state["index"]),
    ).fetchone()
    if (
        not row
        or row["results"] is not None
        or row["starts"]
        != state.get("roundStart", state["first"] + state["index"] * 86400)
    ):
        return None
    return json.loads(row["snapshot"]).get(str(fid), {}).get("enemy")


def yesterday(db, sid, fid):
    state = window(db, sid)
    for row in db.execute(
        "SELECT round_no,starts,results FROM familia_war_rounds WHERE season=? AND results IS NOT NULL ORDER BY round_no DESC",
        (sid,),
    ):
        if (
            state
            and "cadence" not in state
            and (row["starts"] != state["first"] + row["round_no"] * 86400)
        ):
            continue
        item = json.loads(row["results"]).get(str(fid))
        if item and item["outcome"] != "draw":
            return dict(
                yesterdayFamilia=dict(
                    item["enemyProfile"],
                    result=int(item["outcome"] == "lose"),
                    **item["enemyPoints"],
                ),
                yesterdayPoint=item["points"],
                yesterdayResult=int(item["outcome"] == "win"),
            )
        return None
    return None


def eligible_members(db, sid, state, fid=None):
    """The frozen round roster, shared by status and battle validation."""
    if state["index"] is None:
        return set()
    row = db.execute(
        "SELECT starts,snapshot,results FROM familia_war_rounds WHERE season=? AND round_no=?",
        (sid, state["index"]),
    ).fetchone()
    if (
        not row
        or row["results"] is not None
        or row["starts"]
        != state.get("roundStart", state["first"] + state["index"] * 86400)
    ):
        return set()
    return {
        uid
        for key, clan in json.loads(row["snapshot"]).items()
        if fid is None or key == str(fid)
        for uid in clan["members"]
    }


def eligible(db, sid, uid, state):
    member = db.execute("SELECT familia FROM members WHERE uid=?", (uid,)).fetchone()
    return bool(member and uid in eligible_members(db, sid, state, member["familia"]))


def mail_candidates(db, sid, uid):
    result = []
    for row in db.execute(
        "SELECT round_no,results FROM familia_war_rounds WHERE season=? AND results IS NOT NULL",
        (sid,),
    ):
        for fid, value in json.loads(row["results"]).items():
            if uid not in value["participants"] or value["outcome"] == "draw":
                continue
            kind = 0 if value["outcome"] == "win" else 1
            for prize in reward_rows():
                if (
                    prize.number(1, 8) != sid
                    or prize.number(2) != kind
                    or prize.number(13, 8) != value["leagueId"]
                ):
                    continue
                items = [
                    (prize.number(i), prize.number(i + 1))
                    for i in (3, 5, 7, 9, 11)
                    if prize.number(i) and prize.number(i + 1) > 0
                ]
                key = f"fwg-versus:{sid}:{row['round_no']}:{fid}:{prize.number(0, 8)}"
                result.append(
                    (
                        key,
                        f"FWG S{sid % 1000}: {('Win' if kind == 0 else 'Lose')} (Round {row['round_no'] + 1})",
                        items,
                    )
                )
    return result
