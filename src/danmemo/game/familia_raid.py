"""Shared Familia Rush with recovered overkill/enemy-coefficient score rules.

See README.md for scope and limitations. Client damage is trusted in this
private test server; rounding/concurrency/anti-cheat are not publisher code.
"""

import copy
import functools
import hashlib
import json
import math
import struct
import time
from danmemo.master import reader as master
from danmemo.game import resources as resources
from danmemo.game import familia_economy as economy

ROUTES = {
    "guild_raid/status/get",
    "guild_raid/status/preinitiation",
    "guild_raid/ranking/member/get",
    "guild_raid/ranking/familia/top/get",
    "guild_raid/ranking/familia/around/get",
    "guild_raid/player_info/get",
    "guild_raid/stage/complete",
    "guild_raid/reward/defeat",
    "guild_raid/status/log",
}
SCHEMA = "\nCREATE TABLE IF NOT EXISTS familia_raid_state(\n    season INTEGER NOT NULL, familia INTEGER NOT NULL, state TEXT NOT NULL,\n    PRIMARY KEY(season,familia));\nCREATE TABLE IF NOT EXISTS familia_raid_attempts(\n    uid INTEGER NOT NULL, attempt TEXT NOT NULL, signature TEXT NOT NULL,\n    response TEXT NOT NULL, pass_after INTEGER NOT NULL, stamp INTEGER NOT NULL,\n    applied INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(uid,attempt));\nCREATE TABLE IF NOT EXISTS familia_raid_scores(\n    season INTEGER NOT NULL, familia INTEGER NOT NULL, uid INTEGER NOT NULL,\n    score INTEGER NOT NULL, PRIMARY KEY(season,familia,uid));\nCREATE TABLE IF NOT EXISTS familia_raid_rewards(\n    season INTEGER NOT NULL, familia INTEGER NOT NULL, defeat INTEGER NOT NULL,\n    uid INTEGER NOT NULL, stage INTEGER NOT NULL, reward INTEGER NOT NULL,\n    claimed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(season,familia,defeat,uid));\nCREATE TABLE IF NOT EXISTS familia_raid_logs(\n    id INTEGER PRIMARY KEY AUTOINCREMENT, season INTEGER NOT NULL,\n    familia INTEGER NOT NULL, entry TEXT NOT NULL);\n"
_RESULT_KEYS = (
    "available",
    "guildRaidStageId",
    "loopCount",
    "bosses",
    "rank",
    "defeatCount",
    "defeatRewardExists",
    "displayDefeatStageId",
    "displayDefeatLoopCount",
    "lastBattleInfo",
    "ranking",
    "defeatNow",
    "score",
    "totalDamage",
    "isRecorded",
    "isTraining",
)


def completion_result(result, values=None):
    """Normalize older receipts to the original 0x2470534 root contract."""
    info = result.pop("stageInfo", {})
    if isinstance(info, dict):
        result.update({key: info[key] for key in _RESULT_KEYS if key in info})
    if values is not None:
        result.update(values)
    result.pop("unionRaidStageId", None)
    return result


@functools.cache
def catalogs():
    seasons = {
        r.number(0, 8): r
        for r in master.rows(master.master_bytes("guildRaidInformation"))
    }
    stages = {
        r.number(0, 8): dict(
            id=r.number(0, 8),
            season=r.number(1, 8),
            loop=r.number(2),
            order=r.number(3),
            adventureStageId=r.number(5, 8),
            bosses=[
                dict(enemyIndex=i - 8, hp=r.number(i))
                for i in range(8, 13)
                if r.number(i)
            ],
            reward=r.number(13, 8),
            scoreCoefficient=score_coefficient(r),
        )
        for r in master.rows(master.master_bytes("guildRaidStage"))
    }
    return (seasons, stages)


def score_coefficient(row):
    value = struct.unpack("<f", row.number(14).to_bytes(4, "little"))[0]
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid original Familia Rush enemy coefficient")
    return math.floor(value * 100 + 0.5)


def season_stages(season):
    if type(season) is not int or season not in catalogs()[0]:
        raise ValueError("Unknown Familia Rush season")
    return sorted(
        (s for s in catalogs()[1].values() if s["season"] == season),
        key=lambda s: (s["loop"], s["order"], s["id"]),
    )


def member(world, db):
    row = db.execute("SELECT familia FROM members WHERE uid=?", (world.uid,)).fetchone()
    if row is None:
        raise ValueError("Familia membership required for Rush")
    return row["familia"]


def visible_battle_info(value):
    """Make recorded Rush activity visible to the native dashboard and log."""
    if not isinstance(value, dict):
        return {}
    result = copy.deepcopy(value)
    required = ("playerName", "userId", "leaderPc", "attackedAt", "params")
    if result.get("logType", 0) < 1 and all((result.get(key) for key in required)):
        params = result["params"]
        if isinstance(params, dict) and "p1" in params and ("p2" in params):
            result["logType"] = 1
    return result


def load(db, season, fid):
    stages = season_stages(season)
    row = db.execute(
        "SELECT state FROM familia_raid_state WHERE season=? AND familia=?",
        (season, fid),
    ).fetchone()
    if row:
        current = json.loads(row["state"])
        visible = visible_battle_info(current.get("lastBattleInfo"))
        if visible != current.get("lastBattleInfo"):
            current["lastBattleInfo"] = visible
            save(db, season, fid, current)
        return current
    current = dict(
        cursor=0,
        defeats=0,
        bosses=copy.deepcopy(stages[0]["bosses"]),
        lastBattleInfo={},
    )
    save(db, season, fid, current)
    return current


def save(db, season, fid, current):
    db.execute(
        "INSERT OR REPLACE INTO familia_raid_state VALUES(?,?,?)",
        (season, fid, json.dumps(current)),
    )


def ranking(world, db, season, fid, members=False):
    if members:
        rows = db.execute(
            "SELECT uid,score FROM familia_raid_scores WHERE season=? AND familia=? ORDER BY score DESC,uid",
            (season, fid),
        ).fetchall()
        return [
            dict(rank=i + 1, score=r["score"], member=world.profile(db, r["uid"]))
            for i, r in enumerate(rows)
        ]
    rows = db.execute(
        "SELECT familia,SUM(score) AS score FROM familia_raid_scores WHERE season=? GROUP BY familia ORDER BY score DESC,familia",
        (season,),
    ).fetchall()
    result = []
    for i, r in enumerate(rows):
        family = db.execute(
            "SELECT name,info FROM familias WHERE id=?", (r["familia"],)
        ).fetchone()
        if family:
            result.append(
                dict(
                    rank=i + 1,
                    score=r["score"],
                    familia=dict(
                        id=r["familia"],
                        name=family["name"],
                        godId=json.loads(family["info"]).get("godId", 0),
                    ),
                )
            )
    return result


def status(world, db, season, fid):
    current = load(db, season, fid)
    stage = season_stages(season)[current["cursor"]]
    ranks = ranking(world, db, season, fid)
    rank = next((r["rank"] for r in ranks if r["familia"]["id"] == fid), 0)
    pending = db.execute(
        "SELECT stage FROM familia_raid_rewards WHERE season=? AND familia=? AND uid=? AND claimed=0 ORDER BY defeat LIMIT 1",
        (season, fid, world.uid),
    ).fetchone()
    display = catalogs()[1][pending["stage"]] if pending else stage
    return dict(
        code=0,
        available=any((b["hp"] for b in current["bosses"])),
        guildRaidStageId=stage["id"],
        loopCount=stage["loop"],
        bosses=copy.deepcopy(current["bosses"]),
        rank=rank,
        defeatCount=current["defeats"],
        defeatRewardExists=bool(pending),
        displayDefeatStageId=display["id"],
        displayDefeatLoopCount=display["loop"],
        lastBattleInfo=current["lastBattleInfo"],
        ranking=ranking(world, db, season, fid, True),
    )


def recovery(world, oc):
    """Apply a committed pass debit once, even after a worker/file-write crash."""
    with world.connect() as db:
        if not db.execute(
            "SELECT 1 FROM familia_raid_attempts WHERE uid=? AND applied=0 LIMIT 1",
            (world.uid,),
        ).fetchone():
            return
        db.execute("BEGIN IMMEDIATE")
        pending = db.execute(
            "SELECT * FROM familia_raid_attempts WHERE uid=? AND applied=0 ORDER BY rowid",
            (world.uid,),
        ).fetchall()
        if not pending:
            return
        with oc.PROGRESS_LOCK:
            state = oc.load_progress()
            info = state["tables"]["UserInfo"]
            for row in pending:
                player = info[0]["playerStatus"]
                player["graidSP"] = row["pass_after"]
                player["graidSPLastResetAt"] = row["stamp"]
                state.setdefault("arenaStatusReconciliation", {})["graidSP"] = row[
                    "stamp"
                ]
            state["tokens"]["UserInfo"] = hashlib.md5(
                oc.msgpack_value(info)
            ).hexdigest()
            oc.write_progress(state)
            db.execute(
                "UPDATE familia_raid_attempts SET applied=1 WHERE uid=?", (world.uid,)
            )


def capture_adventure(state, adventure, fresh):
    """Keep the pushed native raid record through the client's stage reset."""
    sid = adventure.get("stageId")
    stage = next(
        (s for s in catalogs()[1].values() if s["adventureStageId"] == sid), None
    )
    if not stage:
        return
    attempt_stage = next(
        (
            r
            for r in state["tables"].get("UserAdventureStage", [])
            if r["adventureStageId"] == sid
        ),
        None,
    )
    if not attempt_stage:
        return
    stamp = attempt_stage.get("lastTryDateTime", 0)
    records = state.setdefault("familiaRushNativeAttempts", {})
    record = records.get(str(sid))
    if (
        record
        and record.get("awaitingStart")
        and (record.get("expectedRaidStageId") == stage["id"])
    ):
        record["stamp"] = stamp
        if stamp != record.get("previousTryDateTime"):
            record["awaitingStart"] = False
    elif fresh or not record or record["stamp"] != stamp:
        record = dict(
            stamp=stamp,
            passesBefore=state["tables"]["UserInfo"][0]["playerStatus"].get(
                "graidSP", 0
            ),
        )
        records[str(sid)] = record
    score = adventure.get("userGuildRaidScore")
    if isinstance(score, dict) and score.get("guildRaidStageId") == stage["id"]:
        record["score"] = copy.deepcopy(score)
    events = adventure.get("events", [])
    if events and all((e.get("isFinished") for e in events)):
        record["finished"] = True


def begin_native_attempt(oc, stage_id):
    """Bind the status preinitiation to a stage before the native SP debit."""
    if oc is None:
        return {}
    stage = catalogs()[1][stage_id]
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        previous = next(
            (
                r.get("lastTryDateTime", 0)
                for r in state["tables"].get("UserAdventureStage", [])
                if r["adventureStageId"] == stage["adventureStageId"]
            ),
            0,
        )
        state.setdefault("familiaRushNativeAttempts", {})[
            str(stage["adventureStageId"])
        ] = dict(
            stamp=0,
            passesBefore=state["tables"]["UserInfo"][0]["playerStatus"].get(
                "graidSP", 0
            ),
            expectedRaidStageId=stage_id,
            awaitingStart=True,
            previousTryDateTime=previous,
        )
        oc.write_progress(state)
        return {}


def complete(world, request, oc):
    if oc is None:
        raise ValueError("Rush completion requires an account backend")
    state = oc.load_progress()
    stage_id = request.get("adventureStageId")
    attempt_stage = next(
        (
            s
            for s in state["tables"].get("UserAdventureStage", [])
            if s["adventureStageId"] == stage_id
        ),
        None,
    )
    native = state.get("familiaRushNativeAttempts", {}).get(str(stage_id), {})
    if attempt_stage is None or native.get("stamp") != attempt_stage.get(
        "lastTryDateTime", 0
    ):
        native = {}
    original_payload = "guildRaidStageId" in request or "bosses" in request
    score = (
        dict(
            guildRaidStageId=request.get("guildRaidStageId"),
            enemyDamage=request.get("bosses"),
        )
        if original_payload
        else request.get("userGuildRaidScore", native.get("score"))
    )
    if not isinstance(score, dict):
        raise ValueError("Missing original Familia Rush damage record")
    sid = score.get("guildRaidStageId")
    if type(sid) is not int or sid not in catalogs()[1]:
        raise ValueError("Unknown Rush stage")
    stage = catalogs()[1][sid]
    if original_payload and (
        type(request.get("loopCount")) is not int
        or request["loopCount"] != stage["loop"]
    ):
        raise ValueError("Rush loop does not match master")
    training = request.get("isTraining", False)
    if type(training) is not bool:
        raise ValueError("Invalid Rush training flag")
    if request.get("adventureStageId") != stage["adventureStageId"]:
        raise ValueError("Rush battle stage does not match master")
    damage = score.get("enemyDamage")
    if not isinstance(damage, list) or len(damage) > 100:
        raise ValueError("Invalid Rush damage list")
    amounts = {}
    valid = {b["enemyIndex"] for b in stage["bosses"]}
    for row in damage:
        if not isinstance(row, dict):
            raise ValueError("Invalid Rush enemy damage")
        index, amount = (row.get("enemyIndex"), row.get("damage"))
        if (
            type(index) is not int
            or not 0 <= index < 5
            or type(amount) is not int
            or (not 0 <= amount <= 2147483647)
            or (index not in valid and amount != 0)
        ):
            raise ValueError("Invalid Rush boss damage")
        if original_payload and index in amounts:
            raise ValueError("Duplicate Rush boss index")
        amounts[index] = amounts.get(index, 0) + amount
    token = request.get("token")
    if not isinstance(token, str):
        raise ValueError("Invalid Rush attempt token")
    if attempt_stage is None:
        raise ValueError("Rush stage has no active account attempt")
    if training:
        if not (
            token
            and token == attempt_stage.get("token")
            or (
                token == ""
                and native.get("finished") is True
                and (native.get("stamp", 0) > 0)
            )
        ):
            raise ValueError("Rush training token does not match active attempt")
        with world.connect() as db:
            fid = member(world, db)
            result = status(world, db, stage["season"], fid)
        result.update(
            guildRaidStageId=sid,
            loopCount=stage["loop"],
            bosses=copy.deepcopy(stage["bosses"]),
            defeatNow=False,
            score=0,
            totalDamage=sum(amounts.values()),
            isRecorded=False,
            isTraining=True,
            gainedItems=[],
            gainedPCs=[],
            gainedCostumes=[],
            friendshipStates=[],
            UserAdventureStage=[copy.deepcopy(attempt_stage)],
            adventureStageTaskIds=[],
            operations=[],
        )
        return result
    attempt = f"{sid}:{token}:{attempt_stage.get('lastTryDateTime', 0)}"
    signature = json.dumps(request, sort_keys=True, separators=(",", ":"))
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute(
            "SELECT * FROM familia_raid_attempts WHERE uid=? AND attempt=?",
            (world.uid, attempt),
        ).fetchone()
        if prior:
            if prior["signature"] != signature:
                raise ValueError("Rush completed attempt changed")
            result = json.loads(prior["response"])
        else:
            fid = member(world, db)
            season = stage["season"]
            current = load(db, season, fid)
            status_before = status(world, db, season, fid)
            if status_before["guildRaidStageId"] != sid:
                raise ValueError("Rush boss changed; refresh before challenging")
            passes = state["tables"]["UserInfo"][0]["playerStatus"].get("graidSP", 0)
            initial = native.get("passesBefore", passes)
            if (
                type(passes) is not int
                or type(initial) is not int
                or initial <= 0
                or (passes < 0)
            ):
                raise ValueError("No Familia Rush passes available")
            result = oc.stage_complete_response(dict(request, userGuildRaidScore=score))
            applied = 0
            for boss in current["bosses"]:
                dealt = min(boss["hp"], amounts.get(boss["enemyIndex"], 0))
                boss["hp"] -= dealt
                applied += dealt
            now = int(time.time())
            profile = world.profile(db, world.uid)
            current["lastBattleInfo"] = dict(
                logType=1,
                playerName=profile["name"],
                userId=world.uid,
                leaderPc=profile["leaderPc"],
                attackedAt=now,
                params=dict(p1=applied, p2=sid),
            )
            defeated = all((b["hp"] == 0 for b in current["bosses"]))
            db.execute(
                "INSERT INTO familia_raid_logs(season,familia,entry) VALUES(?,?,?)",
                (season, fid, json.dumps(current["lastBattleInfo"])),
            )
            ranked_score = sum(amounts.values()) * stage["scoreCoefficient"] // 100
            stage_info = dict(
                status_before,
                bosses=copy.deepcopy(current["bosses"]),
                lastBattleInfo=current["lastBattleInfo"],
                defeatNow=defeated,
                score=ranked_score,
                totalDamage=sum(amounts.values()),
                isRecorded=True,
                isTraining=False,
            )
            db.execute(
                "INSERT INTO familia_raid_scores VALUES(?,?,?,?) ON CONFLICT(season,familia,uid) DO UPDATE SET score=score+excluded.score",
                (season, fid, world.uid, ranked_score),
            )
            ranks = ranking(world, db, season, fid)
            stage_info["rank"] = next(
                (r["rank"] for r in ranks if r["familia"]["id"] == fid), 0
            )
            stage_info["ranking"] = ranking(world, db, season, fid, True)
            completion_result(result, stage_info)
            if defeated:
                stages = season_stages(season)
                current["defeats"] += 1
                db.execute(
                    "INSERT INTO familia_raid_rewards(season,familia,defeat,uid,stage,reward) SELECT ?,?,?,uid,?,? FROM members WHERE familia=?",
                    (season, fid, current["defeats"], sid, stage["reward"], fid),
                )
                result["defeatRewardExists"] = True
                if current["cursor"] + 1 < len(stages):
                    current["cursor"] += 1
                    current["bosses"] = copy.deepcopy(
                        stages[current["cursor"]]["bosses"]
                    )
            save(db, season, fid, current)
            db.execute(
                "INSERT INTO familia_raid_attempts VALUES(?,?,?,?,?,?,0)",
                (
                    world.uid,
                    attempt,
                    signature,
                    json.dumps(result),
                    passes if native else passes - 1,
                    now,
                ),
            )
    recovery(world, oc)
    return completion_result(result)


def receive_rewards(world, season, oc):
    if oc is None:
        raise ValueError("Rush rewards require account backend")
    rewards = {
        r.number(0, 8): r
        for r in master.rows(master.master_bytes("guildRaidDefeatReward"))
    }
    result = dict(code=0, guildRaidDefeatRewards=[])
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        fid = member(world, db)
        pending = db.execute(
            "SELECT * FROM familia_raid_rewards WHERE season=? AND familia=? AND uid=? AND claimed=0 ORDER BY defeat",
            (season, fid, world.uid),
        ).fetchall()
        if not pending:
            return result
        tables = copy.deepcopy(oc.load_progress()["tables"])
        patch = {}
        now = int(time.time())
        for claim in pending:
            row = rewards[claim["reward"]]
            for field in (2, 4, 6, 8, 10):
                iid, amount = (row.number(field), row.number(field + 1))
                if not iid or not amount:
                    continue
                from danmemo.game.transactions import identities as identities

                identity = identities().get(iid)
                if identity is None:
                    raise ValueError("Rush reward resource is not archived")
                table, key = identity
                resources.add(tables, table, key, iid, amount, world.uid, now)
                patch[table] = tables[table]
            result["guildRaidDefeatRewards"].append(
                dict(defeatRewardId=claim["reward"])
            )
        receipt = (
            f"rush-reward:{season}:{fid}:{pending[0]['defeat']}:{pending[-1]['defeat']}"
        )
        db.execute(
            "INSERT INTO familia_economy_receipts VALUES(?,?,?,?,?,0)",
            (world.uid, receipt, "rush-reward", json.dumps(result), json.dumps(patch)),
        )
        db.execute(
            "UPDATE familia_raid_rewards SET claimed=1 WHERE season=? AND familia=? AND uid=?",
            (season, fid, world.uid),
        )
    economy.recovery(world, oc)
    return result


def prepare_account(oc, season):
    """Add archived Rush stages without replacing any existing account rows."""
    if oc is None:
        return {}
    with oc.PROGRESS_LOCK:
        saved = oc.load_progress()
        marker = saved.setdefault("familiaRushTestSeasons", [])
        if season in marker:
            rows = saved["tables"].get("UserGuildRaidState", [])
            if rows and rows[0].get("guildRaidId") == 0:
                rows[0].update(guildRaidId=season, readDefeatCount=0)
                token = hashlib.md5(oc.msgpack_value(rows)).hexdigest()
                saved["tokens"]["UserGuildRaidState"] = token
                oc.write_progress(saved)
                return dict(
                    data={"UserGuildRaidState": rows},
                    dataTokens={"UserGuildRaidState": token},
                )
            return {}
        rows = saved["tables"].setdefault("UserAdventureStage", [])
        existing = {r["adventureStageId"] for r in rows}
        for stage in season_stages(season):
            sid = stage["adventureStageId"]
            if sid in existing:
                continue
            data = oc.stage_catalog()["stages"].get(str(sid))
            if data is None:
                raise ValueError("Rush adventure stage is not archived")
            rows.append(
                dict(
                    userId=oc.LOCAL_USER_ID,
                    adventureStageId=sid,
                    state=1,
                    tasks=[dict(adventureStageTaskId=t, state=0) for t in data[4]],
                    ticketOpenedAt=0,
                    ticketClosedAt=0,
                    usedTicketId=0,
                    firstTryDateTime=0,
                    firstCompleteDateTime=0,
                    lastTryDateTime=0,
                    lastCompleteDateTime=0,
                    token="",
                    cumulativeClearCount=0,
                    currentScore=0,
                    highScore=0,
                    dailyClearCount=0,
                )
            )
        status = saved["tables"]["UserInfo"][0]["playerStatus"]
        status["graidSP"] = max(status.get("graidSP", 0), 3)
        status["graidSPLastResetAt"] = int(time.time())
        saved.setdefault("arenaStatusReconciliation", {})["graidSP"] = status[
            "graidSPLastResetAt"
        ]
        saved["tables"]["UserGuildRaidState"] = [
            dict(userId=oc.LOCAL_USER_ID, guildRaidId=season, readDefeatCount=0)
        ]
        marker.append(season)
        updated = ("UserAdventureStage", "UserInfo", "UserGuildRaidState")
        for table in updated:
            saved["tokens"][table] = hashlib.md5(
                oc.msgpack_value(saved["tables"][table])
            ).hexdigest()
        oc.write_progress(saved)
        return dict(
            data={t: saved["tables"][t] for t in updated},
            dataTokens={t: saved["tokens"][t] for t in updated},
        )


def dispatch(world, action, request, oc=None, receipt=None):
    if action == "guild_raid/stage/complete":
        return complete(world, request, oc)
    season = request.get("guildRaidInformationId")
    if action == "guild_raid/player_info/get":
        target = request.get("userId")
        if type(target) is not int or target <= 0:
            raise ValueError("Invalid Rush profile target")
        with world.connect() as db:
            row = db.execute(
                "SELECT detail FROM player_profile_details WHERE uid=?", (target,)
            ).fetchone()
            if row is None:
                raise ValueError("Rush player has no published profile")
            result = dict(code=0, **json.loads(row["detail"]))
            party = db.execute(
                "SELECT party FROM players WHERE uid=?", (target,)
            ).fetchone()
            if party and party["party"]:
                result["party"] = dict(json.loads(party["party"]), partyType=4, index=0)
            return result
    season_stages(season)
    if action == "guild_raid/reward/defeat":
        return receive_rewards(world, season, oc)
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        fid = member(world, db)
        prepared = (
            prepare_account(oc, season)
            if action in ("guild_raid/status/get", "guild_raid/status/preinitiation")
            else {}
        )
        if action == "guild_raid/status/log":
            rows = db.execute(
                "SELECT id,entry FROM familia_raid_logs WHERE season=? AND familia=? ORDER BY id DESC LIMIT 100",
                (season, fid),
            ).fetchall()
            logs = []
            for row in rows:
                entry = visible_battle_info(json.loads(row["entry"]))
                if entry != json.loads(row["entry"]):
                    db.execute(
                        "UPDATE familia_raid_logs SET entry=? WHERE id=?",
                        (json.dumps(entry), row["id"]),
                    )
                logs.append(entry)
            return dict(code=0, logs=logs)
        if action.startswith("guild_raid/ranking/"):
            members = action.endswith("/member/get")
            result = dict(code=0, ranking=ranking(world, db, season, fid, members))
            if members:
                own = next(
                    (
                        r
                        for r in ranking(world, db, season, fid)
                        if r["familia"]["id"] == fid
                    ),
                    None,
                )
                result.update(
                    familiaRank=own["rank"] if own else 0,
                    totalScore=own["score"] if own else 0,
                )
            return result
        result = dict(status(world, db, season, fid), **prepared)
        if action == "guild_raid/status/preinitiation":
            seeded = begin_native_attempt(oc, result["guildRaidStageId"])
            for key in ("data", "dataTokens"):
                if key in seeded:
                    result.setdefault(key, {}).update(seeded[key])
        return result
