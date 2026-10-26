"""Recovered Shooting/GvE wire contracts with documented local scoring.

See README.md for scope and limitations. Client combat is not authoritative.
"""

import copy
import functools
import hashlib
import json
import time
from danmemo.master import reader as master
from danmemo.game import arena as arena
from danmemo.storage import royale_score_history as history

ROUTES = {
    "shooting/ranking/get",
    "shooting/stage/complete",
    "gve/summary/get",
    "gve/ranking/user/familia/get",
    "gve/ranking/character/get",
    "gve/player_info/get",
    "gve/stage/complete",
}
ROUTES.update(
    {
        "familia/achievement/get",
        "guild_event/status/log",
        "guild_event/stage/complete",
        "guild_event/player_info/get",
        "guild_event/score/user/daily/get",
        "guild_event/ranking/familia/top/get",
        "guild_event/ranking/familia/around/get",
        "guild_event/ranking/familia/get",
    }
)
SCHEMA = (
    "\nCREATE TABLE IF NOT EXISTS extra_mode_receipts(\n kind TEXT NOT NULL,uid INTEGER NOT NULL,attempt TEXT NOT NULL,\n signature TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(kind,uid,attempt));\nCREATE TABLE IF NOT EXISTS shooting_scores(\n season INTEGER NOT NULL,day INTEGER NOT NULL,uid INTEGER NOT NULL,\n pc INTEGER NOT NULL,score INTEGER NOT NULL,\n PRIMARY KEY(season,day,uid));\nCREATE TABLE IF NOT EXISTS royale_scores(\n season INTEGER NOT NULL,uid INTEGER NOT NULL,stage INTEGER NOT NULL,\n familia INTEGER NOT NULL,score INTEGER NOT NULL,party TEXT,\n PRIMARY KEY(season,uid,stage));\n"
    + history.SCHEMA
)


def integer(value, label, maximum=2147483647):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Invalid " + label)
    return value


@functools.cache
def catalogs():
    seasons = {
        r.number(0, 8): r.number(2, 8)
        for r in master.rows(master.master_bytes("gveInformation"))
    }
    stages = {
        r.number(0, 8): dict(
            id=r.number(0, 8), season=r.number(1, 8), adventureStageId=r.number(2, 8)
        )
        for r in master.rows(master.master_bytes("gveStage"))
    }
    pcs = {r.number(0, 8) for r in master.rows(master.master_bytes("shootingGamePc"))}
    return (seasons, stages, pcs)


def day(stamp):
    return (stamp + 9 * 3600) // 86400


def prepare_test_stages(oc, mode, identifiers, stock=None):
    """One-time test unlock; never clear story or reset an account."""
    if oc is None:
        return {}
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        markers = state.setdefault("extraModeTestSetup", [])
        if mode in markers:
            return {}
        rows = state["tables"].setdefault("UserAdventureStage", [])
        index = {r["adventureStageId"]: r for r in rows}
        for sid in identifiers:
            if sid in index:
                if index[sid].get("state") == 0:
                    index[sid]["state"] = 1
                continue
            data = oc.stage_catalog()["stages"].get(str(sid))
            if data is None:
                raise ValueError("Mode stage not archived")
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
        updated = ["UserAdventureStage"]
        if stock:
            now = int(time.time())
            status = state["tables"]["UserInfo"][0]["playerStatus"]
            for key, amount in stock.items():
                status[key] = max(status.get(key, 0), amount)
                status[key + "UpdatedAt"] = now
                state.setdefault("arenaStatusReconciliation", {})[key] = now
            updated.append("UserInfo")
        markers.append(mode)
        for table in updated:
            state["tokens"][table] = hashlib.md5(
                oc.msgpack_value(state["tables"][table])
            ).hexdigest()
        oc.write_progress(state)
        return dict(
            data={k: copy.deepcopy(state["tables"][k]) for k in updated},
            dataTokens={k: state["tokens"][k] for k in updated},
        )


def shooting(world, action, request):
    season = 67000000002
    if action.endswith("stage/complete"):
        pc = request.get("pcId")
        if type(pc) is not int or pc not in catalogs()[2]:
            raise ValueError("Unknown Shooting character")
        score = integer(request.get("score"), "Shooting score", 9223372036854775807)
        with world.connect() as db:
            db.execute(
                "INSERT INTO shooting_scores VALUES(?,?,?,?,?) ON CONFLICT(season,day,uid) DO UPDATE SET pc=CASE WHEN excluded.score>score THEN excluded.pc ELSE pc END,score=MAX(score,excluded.score)",
                (season, day(int(time.time())), world.uid, pc, score),
            )
        return dict(code=0)
    stamp = integer(
        request.get("rankingDateAt", int(time.time())),
        "Shooting ranking date",
        9223372036854775807,
    )
    with world.connect() as db:
        rows = db.execute(
            "SELECT * FROM shooting_scores WHERE season=? AND day=? ORDER BY score DESC,uid",
            (season, day(stamp)),
        ).fetchall()
        ranking = [
            dict(
                userId=r["uid"],
                name=world.profile(db, r["uid"])["name"],
                rank=i + 1,
                pcId=r["pc"],
                score=r["score"],
                goldCount=0,
                silverCount=0,
                bronzeCount=0,
            )
            for i, r in enumerate(rows)
        ]
    return dict(code=0, ranking=ranking)


def capture_adventure(state, adventure, fresh):
    sid = adventure.get("stageId")
    if not any((r["adventureStageId"] == sid for r in catalogs()[1].values())):
        return
    row = next(
        (
            r
            for r in state["tables"].get("UserAdventureStage", [])
            if r["adventureStageId"] == sid
        ),
        None,
    )
    if row is None:
        return
    records = state.setdefault("extraModeNativeAttempts", {})
    current = records.get(str(sid))
    stamp = row.get("lastTryDateTime", 0)
    if current is None or current.get("stamp") != stamp:
        current = dict(stamp=stamp)
        records[str(sid)] = current
    score = adventure.get("userGvEScore")
    if isinstance(score, dict) and score.get("gveStageId") in catalogs()[1]:
        current["score"] = copy.deepcopy(score)
    if adventure.get("events") and all(
        (e.get("isFinished") for e in adventure["events"])
    ):
        current["finished"] = True


def royale_ranking(world, db, season, fid=None, daily=False):
    history.import_legacy(db, season, catalogs()[1])
    rows = history.player_totals(db, season, int(time.time()), fid, daily)
    result = []
    for i, (uid, score) in enumerate(rows):
        user = world.profile(db, uid)
        family = db.execute(
            "SELECT f.id,f.name FROM familias f JOIN members m ON m.familia=f.id WHERE m.uid=?",
            (uid,),
        ).fetchone()
        result.append(
            dict(
                user=dict(id=uid, name=user["name"], pcId=user["leaderPc"]["pcId"]),
                familia=dict(family) if family else dict(id=0, name=""),
                rank=i + 1,
                score=score,
                pcId=user["leaderPc"]["pcId"],
            )
        )
    return result


def royale_summary(world, db, season, fid):
    ranks = royale_ranking(world, db, season, fid)
    family_score = dict(history.family_totals(db, season, int(time.time()))).get(fid, 0)
    rank = next((r["rank"] for r in ranks if r["user"]["id"] == world.uid), 0)
    return dict(
        code=0,
        summary=dict(familiaScore=family_score, userRank=rank),
        ranking=royale_ranking(world, db, season, fid),
    )


def native_royale_result(response):
    result = copy.deepcopy(response)
    if not isinstance(result.get("score"), dict):
        result["score"] = {
            key: result[key]
            for key in (
                "baseScore",
                "maxDamageBonusScore",
                "actionCountBonusRate",
                "finalScore",
                "adventureStageId",
                "isRecorded",
                "turnCount",
                "turnCountBonusScore",
                "turnCountBonusRate",
            )
            if key in result
        }
    return result


def royale_record(world, db, season, current_score=0, current_stamp=0):
    history.import_legacy(db, season, catalogs()[1])
    now = int(time.time())
    best, daily = history.best(db, season, world.uid, now)
    score = best["score"] if best else 0
    return dict(
        userId=world.uid,
        gveId=season,
        score=score,
        scoreInt64=score,
        dailyHighScore=daily,
        eventHighScore=score,
        gveHighScoreStageId=best["stage"] if best else 0,
        lastResetScoreAt=day(now) * 86400 - 9 * 3600,
        isReceiveFamiliaRankingReward=False,
        lastReceivedDailyRewardAt=0,
    )


def royale_period(season):
    from danmemo.config import modes as modes

    parent = catalogs()[0][season]
    selection = modes.selected_familia_events().get(parent)
    if selection and selection["mode"] == "familia_royale":
        return selection["period"]
    period = modes.config("online")["modes"].get("familia_royale")
    if period and parent == modes.selected_season("familia_royale", period):
        return period
    return None


def complete_royale(world, request, oc):
    if oc is None:
        raise ValueError("Royale completion requires account backend")
    state = oc.load_progress()
    sid = request.get("adventureStageId")
    native = state.get("extraModeNativeAttempts", {}).get(str(sid), {})
    score = request.get("userGvEScore") or native.get("score")
    if not isinstance(score, dict):
        score = {
            k: request.get(k)
            for k in (
                "gveStageId",
                "totalDamage",
                "maxDamage",
                "actionCount",
                "turnCount",
            )
        }
    else:
        score = dict(score)
        if "gveStageId" not in score:
            score["gveStageId"] = request.get("gveStageId")
        elif (
            request.get("gveStageId") is not None
            and score["gveStageId"] != request["gveStageId"]
        ):
            raise ValueError("Conflicting Royale stage IDs")
    stage = catalogs()[1].get(score.get("gveStageId"))
    if stage is None or stage["adventureStageId"] != sid:
        raise ValueError("Royale stage does not match master")
    damage = integer(score.get("totalDamage"), "Royale damage")
    turns = integer(score.get("turnCount", request.get("turnCount", 0)), "Royale turns")
    active = next(
        (
            r
            for r in state["tables"].get("UserAdventureStage", [])
            if r["adventureStageId"] == sid
        ),
        None,
    )
    token = request.get("token", "")
    if (
        active is None
        or not isinstance(token, str)
        or (
            not (
                token
                and token == active.get("token")
                or (
                    not token
                    and native.get("finished")
                    and (native.get("stamp", 0) > 0)
                    and (native["stamp"] == active.get("lastTryDateTime"))
                )
            )
        )
    ):
        raise ValueError("No active Royale attempt")
    attempt = f"{sid}:{token}:{active.get('lastTryDateTime', 0)}"
    signature = json.dumps(request, sort_keys=True, separators=(",", ":"))
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute(
            "SELECT * FROM extra_mode_receipts WHERE kind=? AND uid=? AND attempt=?",
            ("royale", world.uid, attempt),
        ).fetchone()
        if prior:
            if prior["signature"] != signature:
                raise ValueError("Royale completed attempt changed")
            repaired = native_royale_result(json.loads(prior["response"]))
            repaired["userGvERecord"] = royale_record(world, db, stage["season"])
            db.execute(
                "UPDATE extra_mode_receipts SET response=? WHERE kind=? AND uid=? AND attempt=?",
                (json.dumps(repaired), "royale", world.uid, attempt),
            )
            return repaired
        member = db.execute(
            "SELECT familia FROM members WHERE uid=?", (world.uid,)
        ).fetchone()
        common = oc.stage_complete_response(
            dict(request, token=token, userGvEScore=score)
        )
        fid = member["familia"] if member else 0
        season = stage["season"]
        history.import_legacy(db, season, catalogs()[1])
        now = int(time.time())
        period = royale_period(season)
        recorded = period is None or period["start"] <= now < period["end"] + 600
        party = request.get("userPveParty", request.get("userParty"))
        if not isinstance(party, dict):
            party = next(
                (
                    p
                    for p in state["tables"].get("UserGvEParty", [])
                    if p.get("userId") == world.uid and p.get("gveId", season) == season
                ),
                None,
            )
        party = (
            arena.native_party(party)
            if isinstance(party, dict) and party.get("userId") == world.uid
            else None
        )
        party_json = json.dumps(party) if party else None
        if recorded:
            history.record(
                db,
                season,
                world.uid,
                attempt,
                fid,
                stage["id"],
                damage,
                party_json,
                now,
            )
        if party and recorded:
            db.execute(
                "INSERT INTO unit_usage VALUES(?,?,?,?) ON CONFLICT(kind,season,uid) DO UPDATE SET party=excluded.party",
                ("gve", season, world.uid, party_json),
            )
        if recorded:
            db.execute(
                "INSERT INTO royale_scores VALUES(?,?,?,?,?,?) ON CONFLICT(season,uid,stage) DO UPDATE SET score=MAX(score,excluded.score),party=CASE WHEN excluded.score>score THEN excluded.party ELSE party END",
                (season, world.uid, stage["id"], fid, damage, party_json),
            )
        common.update(
            gveStageId=stage["id"],
            score=damage,
            baseScore=damage,
            maxDamageBonusScore=0,
            actionCountBonusRate=0,
            finalScore=damage,
            adventureStageId=sid,
            isRecorded=recorded,
            isEnemyAnnihilated=request.get("isEnemyAnnihilated", False),
            turnCount=turns,
            turnCountBonusScore=0,
            turnCountBonusRate=0,
            userGvERecord=royale_record(
                world, db, season, damage, active.get("lastTryDateTime", 0)
            ),
        )
        common = native_royale_result(common)
        db.execute(
            "INSERT INTO extra_mode_receipts VALUES(?,?,?,?,?)",
            ("royale", world.uid, attempt, signature, json.dumps(common)),
        )
        return common


def dispatch(world, action, request, oc=None, receipt=None):
    if action.startswith("shooting/"):
        return shooting(world, action, request)
    if action in ("gve/stage/complete", "guild_event/stage/complete"):
        return complete_royale(world, request, oc)
    season = request.get("gveId", request.get("targetEventId"))
    if season is None and action in (
        "gve/player_info/get",
        "guild_event/player_info/get",
    ):
        season = sorted(catalogs()[0].keys())[-1]
    if action == "familia/achievement/get" and season not in catalogs()[0]:
        season = next((s for s, a in catalogs()[0].items() if a == season), None)
    if type(season) is not int or season not in catalogs()[0]:
        raise ValueError("Unknown Royale season")
    with world.connect() as db:
        history.import_legacy(db, season, catalogs()[1])
        member = db.execute(
            "SELECT familia FROM members WHERE uid=?", (world.uid,)
        ).fetchone()
        fid = member["familia"] if member else 0
        prepared = (
            prepare_test_stages(
                oc,
                "royale-" + str(season),
                [
                    r["adventureStageId"]
                    for r in catalogs()[1].values()
                    if r["season"] == season
                ],
            )
            if action
            in ("gve/summary/get", "guild_event/status/log", "familia/achievement/get")
            and member
            else {}
        )
        if action == "familia/achievement/get":
            score = dict(history.family_totals(db, season, int(time.time()))).get(
                fid, 0
            )
            result = dict(obtained=[], not_obtained=[])
            for rule in master.rows(master.master_bytes("familiaAchievement")):
                if rule.number(19, 8) == season:
                    result[
                        "obtained" if score >= rule.number(7, 8) else "not_obtained"
                    ].append(
                        dict(
                            achievementId=rule.number(0, 8),
                            currentParam=score,
                            updatedAt=int(time.time()),
                        )
                    )
            return dict(code=0, achievements=result)
        if action == "guild_event/status/log":
            logs = []
            history.adopt_joined_players(db, season, int(time.time()))
            rows = (
                db.execute(
                    "SELECT e.* FROM royale_score_events e "
                    + history.JOIN
                    + " WHERE e.season=? AND "
                    + history.FAMILY
                    + "=? ORDER BY e.stamp DESC,e.attempt DESC LIMIT 100",
                    (season, fid),
                ).fetchall()
                if fid
                else []
            )
            for row in rows:
                profile = world.profile(db, row["uid"])
                previous = db.execute(
                    "SELECT MAX(score),MAX(CASE WHEN day=? THEN score ELSE 0 END) FROM royale_score_events WHERE season=? AND uid=? AND (stamp<? OR (stamp=? AND attempt<?))",
                    (
                        row["day"],
                        season,
                        row["uid"],
                        row["stamp"],
                        row["stamp"],
                        row["attempt"],
                    ),
                ).fetchone()
                logs.append(
                    dict(
                        userId=row["uid"],
                        userName=profile["name"],
                        leaderPc=profile["leaderPc"],
                        gveStageId=row["stage"],
                        totalDamage=row["score"],
                        finalScore=row["score"],
                        isHighScore=row["score"] > (previous[0] or 0),
                        isDailyHighScore=row["score"] > (previous[1] or 0),
                        attackedAt=row["stamp"],
                    )
                )
            return dict(
                code=0,
                logs=logs[
                    : integer(request.get("count", 100), "Royale log count", 100)
                ],
            )
        if action == "guild_event/score/user/daily/get":
            record = royale_record(world, db, season)
            return dict(code=0, userGvERecord=record, **record)
        if action.startswith("guild_event/ranking/familia/"):
            families = [
                dict(familia=fid, score=score)
                for fid, score in history.family_totals(db, season, int(time.time()))
            ]
            ranking = []
            for i, r in enumerate(families):
                family = db.execute(
                    "SELECT name,info FROM familias WHERE id=?", (r["familia"],)
                ).fetchone()
                if family:
                    ranking.append(
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
            if not action.endswith("/familia/get"):
                return dict(code=0, belongsFamilia=bool(fid), ranking=ranking)

            def members(daily):
                return [
                    dict(
                        userId=r["user"]["id"],
                        userName=r["user"]["name"],
                        rank=r["rank"],
                        score=r["score"],
                        leaderPc=world.profile(db, r["user"]["id"])["leaderPc"],
                        adventureLevelId=master.rows(
                            master.master_bytes("adventureLevel")
                        )[0].number(0, 8),
                    )
                    for r in royale_ranking(world, db, season, fid, daily)
                ]

            own = next(
                (
                    dict(rank=r["rank"], score=r["score"])
                    for r in ranking
                    if r["familia"]["id"] == fid
                ),
                dict(rank=-1, score=0),
            )
            return dict(
                code=0,
                member_daily_ranking=members(True),
                member_total_ranking=members(False),
                familia_score_rank=own,
            )
        if action in ("gve/player_info/get", "guild_event/player_info/get"):
            target = request.get("targetUserId", request.get("userId"))
            row = db.execute(
                "SELECT detail FROM player_profile_details WHERE uid=?", (target,)
            ).fetchone()
            if row is None:
                raise ValueError("Royale player profile not published")
            result = dict(code=0, **json.loads(row["detail"]))
            party = db.execute(
                "SELECT party FROM royale_scores WHERE season=? AND uid=? ORDER BY score DESC LIMIT 1",
                (season, target),
            ).fetchone()
            if not party or not party["party"]:
                party = db.execute(
                    "SELECT party FROM royale_scores WHERE uid=? AND party IS NOT NULL ORDER BY season DESC, score DESC LIMIT 1",
                    (target,),
                ).fetchone()
            saved_party = None
            if party and party["party"]:
                saved_party = json.loads(party["party"])
            else:
                p_row = db.execute(
                    "SELECT party FROM players WHERE uid=?", (target,)
                ).fetchone()
                if p_row and p_row["party"]:
                    saved_party = json.loads(p_row["party"])
                else:
                    user_path = world.path.parent / str(target) / "progress.json"
                    if user_path.exists():
                        try:
                            user_data = json.loads(user_path.read_text("utf-8"))
                            u_tables = user_data.get("tables", {})
                            candidates = u_tables.get(
                                "UserGvEParty", []
                            ) + u_tables.get("UserPvPParty", [])
                            active_p = next(
                                (
                                    p
                                    for p in candidates
                                    if any((s.get("pc") for s in p.get("slots", [])))
                                ),
                                None,
                            )
                            if active_p:
                                saved_party = arena.native_party(active_p)
                        except Exception:
                            pass
            if saved_party:
                result["party"] = dict(saved_party, partyType=3, index=0)
                result["gveParty"] = dict(saved_party, gveId=season)
                result["gveParty"].setdefault("familiaPc", {"skillIds": []})
            return result
        if action == "gve/ranking/character/get":
            return dict(code=0, **world.unit_popularity("gve", season))
        if action == "gve/ranking/user/familia/get":
            return dict(code=0, ranking=royale_ranking(world, db, season, fid))
        return dict(royale_summary(world, db, season, fid), **prepared)
