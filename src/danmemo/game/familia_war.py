"""Private FWG test backend using recovered ARM64 request/result contracts.

Matching, daily limits and points are documented reconstructions. Combat is
client simulated; this module is not original server code or anti-cheat.
"""

from danmemo import paths as _paths
import hashlib
import json
import secrets
import time
from danmemo.game import arena as arena
from danmemo.master import reader as master
from danmemo.game import familia_war_votes as votes
from danmemo.game import familia as management
from danmemo.game import familia_war_lifecycle as lifecycle

REGULAR = 65000000042
MASTER = 65000000039
TOURNAMENT = 65600000009
BATTLE_LEASE_SECONDS = 600
PREPARATION_OVERRIDE = _paths.STATE / "admin/fwg_preparation_override.json"


def operator_preparation_end(sid, now):
    """Short, expiring operator UI test window; never retime accepted rounds."""
    try:
        value = json.loads(PREPARATION_OVERRIDE.read_text("utf-8"))
        start, end = (value["start"], value["end"])
        if (
            value["season"] == sid
            and type(start) is int
            and (type(end) is int)
            and (0 < end - start <= 1800)
            and (start <= now < end)
        ):
            return end
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


ROUTES = {
    "gvg/info/get",
    "gvg/status/get",
    "gvg/tournament/status/get",
    "gvg/familia/get",
    "gvg/party/update",
    "gvg/status/captain/update",
    "gvg/player_info/get",
    "gvg/battle/start",
    "gvg/battle/end",
    "gvg/tournament/get",
    "gvg/status/log",
    "gvg/tournament/status/log",
}
ROUTES.update(
    (
        "gvg/" + path
        for path in (
            "ranking/familia/top/get",
            "ranking/familia/around/get",
            "ranking/member/get",
            "league/ranking/member/get",
            "league/ranking/familia/top/get",
            "league/ranking/familia/around/get",
            "ranking/character/get",
        )
    )
)
ROUTES.update(votes.ROUTES)
SCHEMA = "\nCREATE TABLE IF NOT EXISTS familia_war_parties(\n uid INTEGER PRIMARY KEY, party TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS familia_war_phases(\n season INTEGER PRIMARY KEY, preparation_end INTEGER NOT NULL);\nCREATE TABLE IF NOT EXISTS familia_war_roles(\n season INTEGER NOT NULL, familia INTEGER NOT NULL, captain INTEGER NOT NULL,\n deputies TEXT NOT NULL, PRIMARY KEY(season,familia));\nCREATE TABLE IF NOT EXISTS familia_war_matches(\n season INTEGER NOT NULL, familia INTEGER NOT NULL, enemy INTEGER NOT NULL,\n PRIMARY KEY(season,familia));\nCREATE TABLE IF NOT EXISTS familia_war_battles(\n id TEXT PRIMARY KEY, season INTEGER NOT NULL, actor INTEGER NOT NULL,\n enemy INTEGER NOT NULL, familia INTEGER NOT NULL, enemy_familia INTEGER NOT NULL,\n started INTEGER NOT NULL, start_response TEXT NOT NULL,\n signature TEXT, response TEXT, finished INTEGER);\n"
SCHEMA += votes.SCHEMA
SCHEMA += lifecycle.SCHEMA


def season(request):
    from danmemo.config import modes as admin_game_modes

    cfg = admin_game_modes.config("online")["modes"]
    active_regular = cfg.get("familia_war_game", {}).get("seasonId", REGULAR)
    sid = request.get("gvgId", active_regular)
    valid_seasons = {s["id"] for s in admin_game_modes.seasons("familia_war_game")} | {
        s["id"] for s in admin_game_modes.seasons("master_familia_war_game")
    }
    if not valid_seasons:
        valid_seasons = {REGULAR, MASTER}
    if type(sid) is not int or sid not in valid_seasons:
        raise ValueError("Unknown enabled Familia War season")
    return sid


def membership(db, uid):
    row = db.execute("SELECT familia FROM members WHERE uid=?", (uid,)).fetchone()
    if not row:
        raise ValueError("Familia membership required")
    return row["familia"]


def party(db, uid):
    row = db.execute(
        "SELECT party FROM familia_war_parties WHERE uid=?", (uid,)
    ).fetchone()
    if not row:
        raise ValueError("Player must register a Familia War party")
    return dict(json.loads(row["party"]), userId=uid, partyType=5, index=0)


def roles(db, sid, fid):
    row = db.execute(
        "SELECT * FROM familia_war_roles WHERE season=? AND familia=?", (sid, fid)
    ).fetchone()
    owner_row = db.execute("SELECT owner FROM familias WHERE id=?", (fid,)).fetchone()
    owner = owner_row["owner"] if owner_row else 0
    all_members = [
        r[0]
        for r in db.execute(
            "SELECT uid FROM members WHERE familia=? ORDER BY uid", (fid,)
        )
    ]
    present = set(all_members)
    captain = row["captain"] if row and row["captain"] in present else owner
    saved = json.loads(row["deputies"]) if row and row["deputies"] else []
    deputies = []
    for uid in saved:
        if uid in present and uid != captain and (uid not in deputies):
            deputies.append(uid)
    for uid in all_members:
        if len(deputies) >= 3:
            break
        if uid != captain and uid not in deputies:
            deputies.append(uid)
    while len(deputies) < 3:
        if all_members:
            deputies.append(all_members[len(deputies) % len(all_members)])
        elif owner:
            deputies.append(owner)
        else:
            deputies.append(0)
    return (captain, deputies[:3])


def phase(db, sid):
    """Persisted operator test window; native term 1 is preparation, 2 battle."""
    now = int(time.time())
    authored = lifecycle.window(db, sid, now)
    operator_end = operator_preparation_end(sid, now)
    if operator_end is not None and (authored is None or authored["term"] in (1, 2)):
        return dict(term=1, nextTermTime=operator_end)
    if authored is not None:
        return {k: authored[k] for k in ("term", "nextTermTime")}
    row = db.execute(
        "SELECT preparation_end FROM familia_war_phases WHERE season=?", (sid,)
    ).fetchone()
    end = row[0] if row else 0
    return dict(
        term=1 if now < end else 2,
        nextTermTime=end if now < end else (now // 86400 + 1) * 86400,
    )


def league(sid, rank):
    for row in master.rows(master.master_bytes("guildVsGuildLeague")):
        if (
            row.number(1, 8) == sid
            and row.number(4) <= rank
            and (not row.number(5) or rank <= row.number(5))
        ):
            return row.number(0, 8)
    return 0


def badge_result(rule, value, enemy, base, role):
    """Client task/role contract; bounded server reconstruction of settlement.

    Each achieved victory task earns one base badge, up to remaining stock.
    Role bonus uses archived rule fields 6/7, never the entire role reserve.
    """
    win = value["isWin"]
    death = win and value["totalDeathCount"] < rule.number(8)
    turn = win and value["turn"] <= rule.number(9)
    base_gain = min(base, int(win) + int(death) + int(turn))
    role_bonus = (
        rule.number(6)
        if enemy["isCaptain"]
        else rule.number(7)
        if enemy["isSubCaptain"]
        else 0
    )
    role_gain = min(role, role_bonus) if win else 0
    return (
        dict(
            isWin=win,
            isInside=enemy["isInside"],
            point=base_gain + role_gain,
            defenseCount=enemy["defenseCount"],
            isDeathBonus=death,
            isTurnBonus=turn,
            isCaptain=win and enemy["isCaptain"],
            isSubCaptain=win and enemy["isSubCaptain"],
        ),
        dict(base=base_gain, role=role_gain),
    )


def defense_debits(db, sid, uid, day, bounds=None):
    start, end = bounds if bounds is not None else (day * 86400, (day + 1) * 86400)
    rows = db.execute(
        "SELECT response FROM familia_war_battles WHERE season=? AND enemy=? AND finished IS NOT NULL AND started>=? AND started<?",
        (sid, uid, start, end),
    )
    debits = [json.loads(r["response"]).get("badgeDebit", {}) for r in rows]
    return (
        sum((d.get("base", 0) for d in debits)),
        sum((d.get("role", 0) for d in debits)),
    )


def repair_legacy_badges(db):
    """Explicit migration, called with a DB backup; no invented battle metrics."""
    rules = {
        r.number(1, 8): r for r in master.rows(master.master_bytes("guildVsGuildRule"))
    }
    pools = {}
    changed = 0
    for row in db.execute(
        "SELECT * FROM familia_war_battles WHERE finished IS NOT NULL ORDER BY started,id"
    ).fetchall():
        response = json.loads(row["response"])
        enemy = json.loads(row["start_response"])["enemy"]
        rule = rules.get(row["season"])
        if rule is None:
            continue
        key = (row["season"], row["enemy"], row["started"] // 86400)
        base_used, role_used = pools.get(key, (0, 0))
        if "badgeDebit" not in response:
            value = json.loads(row["signature"])
            if any(
                (type(value.get(k)) is not int for k in ("totalDeathCount", "turn"))
            ):
                raise ValueError(
                    "Cannot migrate a Familia War receipt without recorded counters"
                )
            role = (
                rule.number(3)
                if enemy["isCaptain"]
                else rule.number(4)
                if enemy["isSubCaptain"]
                else 0
            )
            result, debit = badge_result(
                rule,
                value,
                enemy,
                max(0, rule.number(5) - base_used),
                max(0, role - role_used),
            )
            response.update(gvgResult=result, badgeDebit=debit)
            db.execute(
                "UPDATE familia_war_battles SET response=? WHERE id=?",
                (json.dumps(response), row["id"]),
            )
            changed += 1
        debit = response["badgeDebit"]
        pools[key] = (base_used + debit["base"], role_used + debit["role"])
    return changed


def members(world, db, sid, fid):
    result = []
    day = int(time.time()) // 86400
    bounds = lifecycle.battle_bounds(db, sid)
    state = lifecycle.window(db, sid)
    eligible = (
        lifecycle.eligible_members(db, sid, state, fid)
        if state and state["term"] == 2
        else None
    )
    lower, upper = bounds if bounds else (day * 86400, (day + 1) * 86400)
    captain, deputies = roles(db, sid, fid)
    rule = next(
        (
            r
            for r in master.rows(master.master_bytes("guildVsGuildRule"))
            if r.number(1, 8) == sid
        )
    )
    for row in db.execute(
        "SELECT m.uid FROM members m JOIN familia_war_parties p USING(uid) WHERE familia=? ORDER BY uid",
        (fid,),
    ):
        uid = row["uid"]
        if eligible is not None and uid not in eligible:
            continue
        profile = world.profile(db, uid)
        battles = db.execute(
            "SELECT response FROM familia_war_battles WHERE season=? AND actor=? AND finished IS NOT NULL AND started>=? AND started<?",
            (sid, uid, lower, upper),
        ).fetchall()
        points = sum((json.loads(r["response"])["gvgResult"]["point"] for r in battles))
        all_battles = db.execute(
            "SELECT response FROM familia_war_battles WHERE season=? AND actor=? AND finished IS NOT NULL",
            (sid, uid),
        ).fetchall()
        total = sum(
            (json.loads(r["response"])["gvgResult"]["point"] for r in all_battles)
        )
        defenses = db.execute(
            "SELECT COUNT(*) FROM familia_war_battles WHERE season=? AND enemy=? AND finished IS NOT NULL AND started>=? AND started<?",
            (sid, uid, lower, upper),
        ).fetchone()[0]
        active = (
            db.execute(
                "SELECT 1 FROM familia_war_battles WHERE season=? AND (actor=? OR enemy=?) AND finished IS NULL AND started>=? LIMIT 1",
                (sid, uid, uid, int(time.time()) - BATTLE_LEASE_SECONDS),
            ).fetchone()
            is not None
        )
        base_used, role_used = defense_debits(db, sid, uid, day, bounds)
        result.append(
            dict(
                userId=uid,
                name=profile["name"],
                rank=profile["rank"],
                leaderPcId=profile["leaderPc"]["pcId"],
                state=int(active),
                offensePoint=points,
                defensePoint=max(0, rule.number(5) - base_used),
                roleDefensePoint=max(
                    0,
                    (
                        rule.number(3)
                        if uid == captain
                        else rule.number(4)
                        if uid in deputies
                        else 0
                    )
                    - role_used,
                ),
                isInside=True,
                position=len(result) + 1,
                totalPoint=total,
                battleCount=len(battles),
                defenseCount=defenses,
                remainingBattleCount=max(0, rule.number(2) - len(battles)),
            )
        )
    return result


def familia(world, db, sid, fid):
    row = db.execute("SELECT * FROM familias WHERE id=?", (fid,)).fetchone()
    roster = members(world, db, sid, fid)
    captain, deputies = roles(db, sid, fid)
    points = sum((r["offensePoint"] for r in roster))
    clans = [r[0] for r in db.execute("SELECT id FROM familias")]
    enabled = lifecycle.policy(sid) is not None
    standings = {
        clan: lifecycle.standing(db, sid, clan)
        if enabled
        else dict(wins=0, losses=0, bonus=0)
        for clan in clans
    }
    totals = {
        clan: (
            lifecycle._points(db, sid, clan)["totalPoint"]
            if enabled
            else sum((r["totalPoint"] for r in members(world, db, sid, clan)))
        )
        + standings[clan]["bonus"]
        for clan in clans
    }
    if enabled:
        bounds = lifecycle.battle_bounds(db, sid)
        points = lifecycle._points(db, sid, fid, *bounds)["totalPoint"]
    rank = sorted(clans, key=lambda clan: (-totals[clan], clan)).index(fid) + 1
    return dict(
        id=fid,
        name=row["name"],
        winCount=standings[fid]["wins"],
        loseCount=standings[fid]["losses"],
        gvgRank=rank,
        dailyPoint=points,
        totalPoint=totals[fid],
        winCountPoint=standings[fid]["bonus"],
        leaderPcId=world.profile(db, row["owner"])["leaderPc"]["pcId"],
        formationId=0,
        dailyBattleCount=sum((r["battleCount"] for r in roster)),
        remainingDailyBattleCount=sum((r["remainingBattleCount"] for r in roster)),
        captainUserId=captain,
        masterUserId=row["owner"],
        isSeed=False,
        subCaptainUserIds=deputies,
        leagueId=league(sid, rank),
    )


def opponent(db, fid, sid, world=None):
    if world is not None:
        state = lifecycle.sync(world, db, sid)
        if state is not None:
            return lifecycle.opponent(db, sid, fid, state)
    db.execute(
        "DELETE FROM familia_war_matches WHERE familia NOT IN (SELECT id FROM familias) OR enemy NOT IN (SELECT id FROM familias)"
    )
    existing = db.execute(
        "SELECT enemy FROM familia_war_matches WHERE season=? AND familia=?", (sid, fid)
    ).fetchone()
    if existing:
        return existing[0]
    ids = [
        r[0]
        for r in db.execute(
            "SELECT DISTINCT familia FROM members JOIN familia_war_parties USING(uid) WHERE familia NOT IN (SELECT familia FROM familia_war_matches WHERE season=?) ORDER BY familia",
            (sid,),
        )
    ]
    for index in range(0, len(ids) - 1, 2):
        a, b = ids[index : index + 2]
        db.executemany(
            "INSERT INTO familia_war_matches VALUES(?,?,?)", [(sid, a, b), (sid, b, a)]
        )
    row = db.execute(
        "SELECT enemy FROM familia_war_matches WHERE season=? AND familia=?", (sid, fid)
    ).fetchone()
    return row[0] if row else None


def status(world, db, sid):
    fid = membership(db, world.uid)
    enemy = opponent(db, fid, sid, world)
    roster = members(world, db, sid, fid)
    own = next((r for r in roster if r["userId"] == world.uid), None)
    pending = (
        db.execute(
            "SELECT 1 FROM familia_war_battles WHERE season=? AND actor=? AND finished IS NULL AND started>=? LIMIT 1",
            (sid, world.uid, int(time.time()) - BATTLE_LEASE_SECONDS),
        ).fetchone()
        is not None
    )
    return dict(
        code=0,
        result=0,
        info=phase(db, sid),
        familia=familia(world, db, sid, fid),
        enemyFamilia=familia(world, db, sid, enemy) if enemy else None,
        familiaMembers=roster,
        enemyFamiliaMembers=members(world, db, sid, enemy) if enemy else [],
        user=dict(
            dailyPoint=own["offensePoint"] if own else 0,
            totalPoint=own["totalPoint"] if own else 0,
            hasPosition=own is not None,
            hasOldBattle=pending,
        ),
        yesterdayResult=lifecycle.yesterday(db, sid, fid)
        if lifecycle.policy(sid)
        else None,
        refreshSeconds=10,
    )


def tournament(world, db):
    """Original bracket keys, populated only by real registered Familia pairs."""
    entries = []
    fid = membership(db, world.uid)
    from danmemo.config import modes as admin_game_modes

    msid = (
        admin_game_modes.config("online")["modes"]
        .get("master_familia_war_game", {})
        .get("seasonId", MASTER)
    )
    for row in db.execute(
        "SELECT DISTINCT familia FROM members JOIN familia_war_parties USING(uid) ORDER BY familia"
    ):
        clan = row[0]
        other = opponent(db, clan, msid)
        if other is None:
            continue
        own = familia(world, db, msid, clan)
        enemy = familia(world, db, msid, other)
        side = 0 if clan < other else 1
        entries.append(
            dict(
                familiaId=clan,
                familiaName=own["name"],
                side=side,
                leaderPcId=own["leaderPcId"],
                startPosition=len(entries) + 1,
                currentPosition=len(entries) + 1,
                winCount=0,
                loseCount=0,
                userVoteCount=0,
                tournamentScore=own["totalPoint"],
                matching=dict(
                    pairId=min(clan, other),
                    enemyFamilia=dict(
                        familiaId=other,
                        familiaName=enemy["name"],
                        side=1 - side,
                        leaderPcId=enemy["leaderPcId"],
                    ),
                ),
            )
        )
    value = dict(
        code=0,
        result=0,
        info=dict(term=2),
        familias=entries,
        ranking=[],
        user=dict(
            supporterTicketCount=0,
            voteTicketCount=0,
            supportPairId=None,
            supportFamiliaId=None,
            canBattle=any((e["familiaId"] == fid for e in entries)),
            canSupport=False,
            canSpectate=False,
            canVote=False,
        ),
    )
    return votes.decorate(world, db, msid, value)


def participation(world, db, sid, oc):
    if oc is None:
        return {}
    from danmemo.game.daily_windows import (
        mission_day as mission_day,
        mission_day_start as mission_day_start,
    )

    now = int(time.time())
    day = mission_day(now)
    count = db.execute(
        "SELECT COUNT(*) FROM familia_war_battles WHERE season=? AND actor=? AND finished IS NOT NULL AND started>=?",
        (sid, world.uid, mission_day_start(now)),
    ).fetchone()[0]
    total = db.execute(
        "SELECT COUNT(*) FROM familia_war_battles WHERE season=? AND actor=? AND finished IS NOT NULL",
        (sid, world.uid),
    ).fetchone()[0]
    tag = next(
        (
            r.text(1)
            for r in master.rows(master.master_bytes("guildVsGuildInformation"))
            if r.number(0, 8) == sid
        )
    )
    rules = [
        r
        for r in master.rows(master.master_bytes("achievement"))
        if r.number(4) == 2706 and r.text(10) == tag
    ]
    with oc.PROGRESS_LOCK:
        saved = oc.load_progress()
        rows = saved["tables"].setdefault("UserAchievement", [])
        before = json.dumps(rows, sort_keys=True)
        days = saved.setdefault("familiaWarMissionDays", {})
        reset = days.get(str(sid)) != day
        for rule in rules:
            aid = rule.number(0, 8)
            row = next((r for r in rows if r["achievementId"] == aid), None)
            if row is None:
                row = dict(
                    userId=world.uid, achievementId=aid, state=2, progressParam=0
                )
                rows.append(row)
            if reset and rule.number(20) == 1:
                row.update(state=2, progressParam=0)
                row.pop("completedAt", None)
            progress = count if rule.number(20) == 1 else total
            row["progressParam"] = progress
            if row["state"] != 4:
                row["state"] = 3 if progress >= rule.number(9) else 2
                if row["state"] == 3:
                    row["completedAt"] = row.get("completedAt") or int(time.time())
        days[str(sid)] = day
        token = hashlib.md5(oc.msgpack_value(rows)).hexdigest()
        saved["tokens"]["UserAchievement"] = token
        if reset or before != json.dumps(rows, sort_keys=True):
            oc.write_progress(saved)
        return dict(
            data={"UserAchievement": rows}, dataTokens={"UserAchievement": token}
        )


def dispatch(world, action, request, oc=None, receipt=None):
    sid = season(request)
    if action == "gvg/ranking/character/get":
        return dict(code=0, **world.unit_popularity("gvg", sid))
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        lifecycle.sync(world, db, sid)
        if action == "gvg/info/get":
            return dict(code=0, result=0, **phase(db, sid))
        fid = membership(db, world.uid)
        if action in votes.ROUTES:
            return votes.dispatch(world, db, action, request, receipt)
        if "/ranking/" in action:
            if "/member/" in action:
                rows = members(world, db, sid, fid)
                rows.sort(key=lambda r: (-r["totalPoint"], r["userId"]))
                ranking = []
                default_level = master.rows(master.master_bytes("adventureLevel"))[
                    0
                ].number(0, 8)
                for index, row in enumerate(rows, 1):
                    uid = row["userId"]
                    profile = world.profile(db, uid)
                    detail = json.loads(
                        db.execute(
                            "SELECT detail FROM player_profile_details WHERE uid=?",
                            (uid,),
                        ).fetchone()[0]
                    )
                    ranking.append(
                        dict(
                            rank=index,
                            userId=uid,
                            userName=profile["name"],
                            leaderPc=profile["leaderPc"],
                            score=row["totalPoint"],
                            dailyPoint=row["offensePoint"],
                            totalPoint=row["totalPoint"],
                            pvpRank=0,
                            adventureLevelId=detail.get(
                                "userAdventureLevelInfo", {}
                            ).get("adventureLevelId", default_level),
                        )
                    )
            else:
                rows = []
                for row in db.execute(
                    "SELECT DISTINCT familia FROM members JOIN familia_war_parties USING(uid)"
                ):
                    clan = row[0]
                    data = familia(world, db, sid, clan)
                    info = json.loads(
                        db.execute(
                            "SELECT info FROM familias WHERE id=?", (clan,)
                        ).fetchone()[0]
                    )
                    rows.append(
                        dict(
                            score=data["totalPoint"],
                            winCountPoint=data["winCountPoint"],
                            winCount=data["winCount"],
                            loseCount=data["loseCount"],
                            familia=dict(
                                id=clan,
                                name=data["name"],
                                winCount=data["winCount"],
                                loseCount=data["loseCount"],
                                godId=info.get("godId", 0),
                                isSeed=False,
                            ),
                        )
                    )
                rows.sort(key=lambda r: (-r["score"], r["familia"]["id"]))
                ranking = [dict(row, rank=i) for i, row in enumerate(rows, 1)]
            return dict(code=0, result=0, ranking=ranking)
        if action == "gvg/tournament/get":
            return tournament(world, db)
        if action in ("gvg/status/log", "gvg/tournament/status/log"):
            histories = []
            for row in db.execute(
                "SELECT * FROM familia_war_battles WHERE season=? AND (familia=? OR enemy_familia=?) AND finished IS NOT NULL ORDER BY finished DESC,rowid DESC LIMIT 100",
                (sid, fid, fid),
            ):
                value = json.loads(row["response"])["gvgResult"]
                if request.get("battleId") and request["battleId"] != row["id"]:
                    continue
                start = json.loads(row["start_response"])
                combat = json.loads(row["signature"])
                own = row["familia"] == fid
                actor = world.profile(db, row["actor"])
                enemy = world.profile(db, row["enemy"])
                histories.append(
                    dict(
                        logType=(3 if value["isWin"] else 4)
                        if own
                        else 6
                        if value["isWin"]
                        else 5,
                        userId=row["actor"],
                        userName=actor["name"],
                        leaderPc=actor["leaderPc"],
                        enemyUserId=row["enemy"],
                        enemyUserName=enemy["name"],
                        enemyLeaderPc=enemy["leaderPc"],
                        enemyIsInside=True,
                        enemyPosition=1,
                        dailyPoint=value["point"] if own else 0,
                        enemyDailyPoint=value["point"] if not own else 0,
                        battleId=row["id"],
                        targetUserId=row["enemy"],
                        isFirstCamp=own,
                        postedAt=row["finished"],
                        gvgId=sid,
                        gvgParty=start["user"]["party"],
                        enemyGvgParty=start["enemy"]["party"],
                        randomSeed=start["randomSeed"],
                        familiaName=start["user"]["familiaName"],
                        enemyFamiliaName=start["enemy"]["familiaName"],
                        isInside=start["user"]["isInside"],
                        enemyDefenseCount=start["enemy"]["defenseCount"],
                        enemyIsCaptain=start["enemy"]["isCaptain"],
                        enemyIsSubCaptain=start["enemy"]["isSubCaptain"],
                        signature=combat["signature"],
                    )
                )
            return dict(code=0, result=0, log=histories, gvgHistories=histories)
        if action == "gvg/party/update":
            value = request.get("party")
            if not isinstance(value, dict) or value.get("userId") != world.uid:
                raise ValueError(
                    "Familia War party must belong to authenticated player"
                )
            value = arena.native_party(value)
            if not any(((s.get("pc") or {}).get("pcId") for s in value["slots"])):
                raise ValueError("Familia War party is empty")
            value.update(partyType=5, index=0)
            db.execute(
                "INSERT OR REPLACE INTO familia_war_parties VALUES(?,?)",
                (world.uid, json.dumps(value)),
            )
            return dict(code=0, result=0, party=value)
        if action == "gvg/status/captain/update":
            owner = db.execute(
                "SELECT owner FROM familias WHERE id=?", (fid,)
            ).fetchone()[0]
            if world.uid != owner:
                raise ValueError("Only Familia master can assign captains")
            captain = request.get("captainUserId")
            raw_deputies = [
                request.get("subCaptainUserId_" + str(i), 0) for i in range(1, 4)
            ]
            raw_deputies = [i for i in raw_deputies if i]
            if type(captain) is not int or any(
                (type(i) is not int for i in raw_deputies)
            ):
                raise ValueError("Invalid captain IDs")
            all_members = [
                r[0]
                for r in db.execute(
                    "SELECT uid FROM members WHERE familia=? ORDER BY uid", (fid,)
                )
            ]
            if captain not in all_members:
                raise ValueError("Captain must be a Familia member")
            deputies = []
            for uid in raw_deputies:
                if uid in all_members and uid != captain and (uid not in deputies):
                    deputies.append(uid)
            for uid in all_members:
                if len(deputies) >= 3:
                    break
                if uid != captain and uid not in deputies:
                    deputies.append(uid)
            while len(deputies) < 3:
                if all_members:
                    deputies.append(all_members[len(deputies) % len(all_members)])
                else:
                    deputies.append(captain)
            deputies = deputies[:3]
            db.execute(
                "INSERT OR REPLACE INTO familia_war_roles VALUES(?,?,?,?)",
                (sid, fid, captain, json.dumps(deputies)),
            )
            return status(world, db, sid)
        if action == "gvg/familia/get":
            roster = []
            for row in db.execute(
                "SELECT uid FROM members WHERE familia=? ORDER BY uid", (fid,)
            ):
                uid = row[0]
                roster.append(
                    dict(
                        world.profile(db, uid),
                        post={1: 3, 2: 2, 3: 1}[management.role(db, uid, fid)],
                        pvpRank=0,
                    )
                )
            return dict(code=0, result=0, members=roster)
        if action in ("gvg/status/get", "gvg/tournament/status/get"):
            return status(world, db, sid)
        if action == "gvg/player_info/get":
            uid = request.get("userId")
            if type(uid) is not int:
                raise ValueError("Invalid Familia War profile target")
            row = db.execute(
                "SELECT detail FROM player_profile_details WHERE uid=?", (uid,)
            ).fetchone()
            if not row:
                raise ValueError("Player profile has not been published")
            detail = json.loads(row["detail"])
            profile = world.profile(db, uid)
            target_fid = membership(db, uid)
            target = next(
                (r for r in members(world, db, sid, target_fid) if r["userId"] == uid)
            )
            user = dict(
                id=uid,
                userName=profile["name"],
                totalExp=detail["playerInfo"]["totalExp"],
                party=party(db, uid),
                defensePoint=target["defensePoint"],
                roleDefensePoint=target["roleDefensePoint"],
                pvpRank=0,
                substitutedSpineIds=detail.get("substitutedSpineIds", []),
            )
            return dict(code=0, result=0, **detail, party=user["party"], user=user)
        if action == "gvg/battle/start":
            from danmemo.config import modes as admin_game_modes

            is_master = sid in {
                s["id"] for s in admin_game_modes.seasons("master_familia_war_game")
            }
            mode = "master_familia_war_game" if is_master else "familia_war_game"
            period = admin_game_modes.config("online")["modes"].get(mode)
            if period and (not period["start"] <= time.time() < period["end"]):
                raise ValueError("Familia War season is outside its battle period")
            if phase(db, sid)["term"] != 2:
                raise ValueError("Familia War is still in preparation")
            uid = request.get("enemyId")
            if type(uid) is not int or type(request.get("enemyType")) is not int:
                raise ValueError("Invalid Familia War opponent")
            enemy_fid = membership(db, uid)
            if enemy_fid == fid or opponent(db, fid, sid, world) != enemy_fid:
                raise ValueError("Opponent is outside the matched enemy Familia")
            state = lifecycle.window(db, sid)
            if state is not None and (
                not lifecycle.eligible(db, sid, world.uid, state)
                or not lifecycle.eligible(db, sid, uid, state)
            ):
                raise ValueError("Player must wait for the next Familia War matching")
            roster = members(world, db, sid, fid)
            own = next((r for r in roster if r["userId"] == world.uid), None)
            if not own or own["remainingBattleCount"] <= 0:
                raise ValueError("No Familia War challenges available")
            pending = db.execute(
                "SELECT start_response FROM familia_war_battles WHERE season=? AND actor=? AND finished IS NULL AND started>=?",
                (
                    sid,
                    world.uid,
                    int(time.time()) - BATTLE_LEASE_SECONDS if state is not None else 0,
                ),
            ).fetchone()
            if pending:
                prior = json.loads(pending[0])
                if prior["enemy"]["id"] != uid:
                    raise ValueError("Finish current Familia War battle first")
                return prior

            def combatant(target, clan):
                captain, deputies = roles(db, sid, clan)
                profile = world.profile(db, target)
                member = next(
                    (r for r in members(world, db, sid, clan) if r["userId"] == target)
                )
                return dict(
                    id=target,
                    userName=profile["name"],
                    familiaName=db.execute(
                        "SELECT name FROM familias WHERE id=?", (clan,)
                    ).fetchone()[0],
                    party=party(db, target),
                    isInside=True,
                    defenseCount=member["defenseCount"],
                    defensePoint=member["defensePoint"],
                    roleDefensePoint=member["roleDefensePoint"],
                    isCaptain=target == captain,
                    isSubCaptain=target in deputies,
                )

            bid = secrets.token_hex(16)
            result = dict(
                code=0,
                result=0,
                gvgId=sid,
                battleId=bid,
                randomSeed=secrets.randbelow(2147483647),
                user=combatant(world.uid, fid),
                enemy=combatant(uid, enemy_fid),
            )
            db.execute(
                "INSERT INTO familia_war_battles(id,season,actor,enemy,familia,enemy_familia,started,start_response) VALUES(?,?,?,?,?,?,?,?)",
                (
                    bid,
                    sid,
                    world.uid,
                    uid,
                    fid,
                    enemy_fid,
                    int(time.time()),
                    json.dumps(result),
                ),
            )
            return result
        if action == "gvg/battle/end":
            row = db.execute(
                "SELECT * FROM familia_war_battles WHERE id=? AND actor=? AND season=?",
                (request.get("battleId"), world.uid, sid),
            ).fetchone()
            if not row:
                raise ValueError("Unknown Familia War battle receipt")
            value = request.get("result")
            if not isinstance(value, dict) or type(value.get("isWin")) is not bool:
                raise ValueError("Invalid Familia War result")
            for key in ("totalDeathCount", "turn"):
                if type(value.get(key)) is not int or not 0 <= value[key] <= 1000:
                    raise ValueError("Invalid Familia War combat counter")
            if type(value.get("signature")) not in (int, str):
                raise ValueError("Missing original Familia War combat signature")
            signature = json.dumps(value, sort_keys=True)
            if row["response"]:
                if signature != row["signature"]:
                    raise ValueError("Completed Familia War result changed")
                return dict(
                    json.loads(row["response"]), **participation(world, db, sid, oc)
                )
            bounds = lifecycle.battle_bounds(db, sid, row["started"])
            if bounds is not None and (
                row["started"] < bounds[0]
                or time.time() >= max(bounds[1], row["started"] + BATTLE_LEASE_SECONDS)
            ):
                raise ValueError(
                    "Familia War result arrived after its settlement deadline"
                )
            enemy = json.loads(row["start_response"])["enemy"]
            rule = next(
                (
                    r
                    for r in master.rows(master.master_bytes("guildVsGuildRule"))
                    if r.number(1, 8) == sid
                )
            )
            base_used, role_used = defense_debits(
                db, sid, row["enemy"], row["started"] // 86400, bounds
            )
            allocation = (
                rule.number(3)
                if enemy["isCaptain"]
                else rule.number(4)
                if enemy["isSubCaptain"]
                else 0
            )
            score, debit = badge_result(
                rule,
                value,
                enemy,
                max(0, rule.number(5) - base_used),
                max(0, allocation - role_used),
            )
            result = dict(
                code=0, result=0, gvgId=sid, gvgResult=score, badgeDebit=debit
            )
            db.execute(
                "UPDATE familia_war_battles SET signature=?,response=?,finished=? WHERE id=?",
                (signature, json.dumps(result), int(time.time()), row["id"]),
            )
            used = json.loads(row["start_response"])["user"]["party"]
            db.execute(
                "INSERT OR REPLACE INTO unit_usage VALUES(?,?,?,?)",
                ("gvg", sid, world.uid, json.dumps(used)),
            )
            return dict(result, **participation(world, db, sid, oc))
    return None
