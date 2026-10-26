"""Original Union Raid wire/master shapes with local shared battle policies.

Private server formulas, alliance assignment and settlement are reconstructed.
See README.md for scope and limitations. for evidence and test limitations.
"""

import copy
import functools
import hashlib
import json
import struct
import time
from danmemo.master import reader as master
from danmemo.game import arena as arena
from danmemo.game.extra_modes import (
    integer as integer,
    prepare_test_stages as prepare_test_stages,
)

ROUTES = {
    "union_raid/" + s
    for s in (
        "status/get",
        "status/preinitiation",
        "stage/complete",
        "reward/defeat",
        "status/log",
        "battle/log",
        "head_line/list",
        "skill/get",
        "skill/growth",
        "player_info/get",
        "ranking/member/get",
        "ranking/score/top/get",
        "ranking/score/around/get",
        "ranking/familia/top/get",
        "ranking/familia/around/get",
        "chat/list",
        "chat/send",
        "reaction/send",
    )
}
SCHEMA = "\nCREATE TABLE IF NOT EXISTS union_membership(\n season INTEGER NOT NULL,familia INTEGER NOT NULL,side INTEGER NOT NULL,\n PRIMARY KEY(season,familia));\nCREATE TABLE IF NOT EXISTS union_stage_state(\n season INTEGER NOT NULL,side INTEGER NOT NULL,lane INTEGER NOT NULL,state TEXT NOT NULL,\n PRIMARY KEY(season,side,lane));\nCREATE TABLE IF NOT EXISTS union_battle_receipts(\n uid INTEGER NOT NULL,attempt TEXT NOT NULL,signature TEXT NOT NULL,response TEXT NOT NULL,\n pass_key TEXT NOT NULL,pass_after INTEGER NOT NULL,stamp INTEGER NOT NULL,\n applied INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(uid,attempt));\nCREATE TABLE IF NOT EXISTS union_scores(\n season INTEGER NOT NULL,uid INTEGER NOT NULL,familia INTEGER NOT NULL,side INTEGER NOT NULL,\n score INTEGER NOT NULL,medals INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(season,uid));\nCREATE TABLE IF NOT EXISTS union_battle_logs(\n id INTEGER PRIMARY KEY AUTOINCREMENT,season INTEGER NOT NULL,side INTEGER NOT NULL,\n entry TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS union_reward_watermark(id INTEGER PRIMARY KEY,last_id INTEGER NOT NULL);\nCREATE TABLE IF NOT EXISTS union_reward_facts(\n id INTEGER PRIMARY KEY,season INTEGER NOT NULL,uid INTEGER NOT NULL,side INTEGER NOT NULL,\n stamp INTEGER NOT NULL,day INTEGER NOT NULL,stage INTEGER NOT NULL,kind INTEGER NOT NULL,\n reward_group INTEGER NOT NULL,damage INTEGER NOT NULL,medals INTEGER NOT NULL,kills INTEGER NOT NULL);\nCREATE INDEX IF NOT EXISTS union_reward_facts_stage ON union_reward_facts(season,side,stage);\nCREATE INDEX IF NOT EXISTS union_reward_facts_player ON union_reward_facts(season,uid,kind,id);\nCREATE INDEX IF NOT EXISTS union_reward_facts_clock ON union_reward_facts(season,stamp,day);\nCREATE TABLE IF NOT EXISTS union_skills(\n season INTEGER NOT NULL,side INTEGER NOT NULL,skill_group INTEGER NOT NULL,\n medals INTEGER NOT NULL,PRIMARY KEY(season,side,skill_group));\nCREATE TABLE IF NOT EXISTS union_messages(\n id INTEGER PRIMARY KEY AUTOINCREMENT,season INTEGER NOT NULL,side INTEGER NOT NULL,\n uid INTEGER NOT NULL,stamp INTEGER NOT NULL,message TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS union_reactions(\n message INTEGER NOT NULL,uid INTEGER NOT NULL,reaction INTEGER NOT NULL,\n PRIMARY KEY(message,uid));\nCREATE TABLE IF NOT EXISTS union_mutation_receipts(\n uid INTEGER NOT NULL,receipt TEXT NOT NULL,signature TEXT NOT NULL,response TEXT NOT NULL,\n PRIMARY KEY(uid,receipt));\nCREATE TABLE IF NOT EXISTS union_mail_grants(\n uid INTEGER NOT NULL,gift INTEGER NOT NULL,PRIMARY KEY(uid,gift));\nCREATE TABLE IF NOT EXISTS union_defeat_rewards(\n season INTEGER NOT NULL,side INTEGER NOT NULL,uid INTEGER NOT NULL,reward INTEGER NOT NULL,\n claimed INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(season,side,uid,reward));\nCREATE TABLE IF NOT EXISTS union_party_snapshots(\n season INTEGER NOT NULL,uid INTEGER NOT NULL,party TEXT NOT NULL,\n PRIMARY KEY(season,uid));\nCREATE TABLE IF NOT EXISTS union_announcements(\n season INTEGER NOT NULL,side INTEGER NOT NULL,kind TEXT NOT NULL,\n id INTEGER NOT NULL,familia INTEGER NOT NULL,stamp INTEGER NOT NULL,\n PRIMARY KEY(season,side,kind,id,familia));\n"
SEASON = 413000001
SIDES = (414000001, 414000002)


@functools.cache
def stages():
    return {
        r.number(0): dict(
            id=r.number(0),
            season=r.number(1),
            kind=r.number(2),
            order=r.number(3),
            loop=r.number(4),
            next=r.number(5),
            adventureStageId=r.number(6, 8),
            medalRate=struct.unpack("<f", struct.pack("<I", r.number(15)))[0],
            bosses=[
                dict(enemyIndex=i - 9, hp=r.number(i))
                for i in range(9, 14)
                if r.number(i)
            ],
        )
        for r in master.rows(master.master_bytes("unionRaidStage"))
    }


@functools.cache
def skills():
    result = {}
    for r in master.rows(master.master_bytes("unionRaidSkill")):
        result.setdefault(r.number(1), []).append(
            dict(id=r.number(0), level=r.number(2), cost=r.number(4))
        )
    return {k: sorted(v, key=lambda r: r["level"]) for k, v in result.items()}


def membership(world, db):
    member = db.execute(
        "SELECT familia FROM members WHERE uid=?", (world.uid,)
    ).fetchone()
    if member is None:
        raise ValueError("Familia membership required for Union Raid")
    fid = member["familia"]
    row = db.execute(
        "SELECT side FROM union_membership WHERE season=? AND familia=?", (SEASON, fid)
    ).fetchone()
    if row:
        return (fid, row["side"])
    counts = {
        side: db.execute(
            "SELECT COUNT(*) FROM union_membership WHERE season=? AND side=?",
            (SEASON, side),
        ).fetchone()[0]
        for side in SIDES
    }
    side = min(SIDES, key=lambda s: (counts[s], s))
    db.execute("INSERT INTO union_membership VALUES(?,?,?)", (SEASON, fid, side))
    return (fid, side)


def lane(stage):
    return stage["kind"] * 10 + stage["order"]


def load(db, side):
    rows = db.execute(
        "SELECT lane,state FROM union_stage_state WHERE season=? AND side=?",
        (SEASON, side),
    ).fetchall()
    if rows:
        return {r["lane"]: json.loads(r["state"]) for r in rows}
    current = {}
    for stage in stages().values():
        if stage["season"] == SEASON and stage["loop"] == 1:
            current[lane(stage)] = dict(
                unionRaidStageId=stage["id"],
                loopCount=1,
                defeatCount=0,
                bosses=copy.deepcopy(stage["bosses"]),
                familiaScore=0,
            )
    save(db, side, current)
    return current


def save(db, side, current):
    for key, value in current.items():
        db.execute(
            "INSERT OR REPLACE INTO union_stage_state VALUES(?,?,?,?)",
            (SEASON, side, key, json.dumps(value)),
        )


def totals(db, side):
    score, medals = db.execute(
        "SELECT COALESCE(SUM(score),0),COALESCE(SUM(medals),0) FROM union_scores WHERE season=? AND side=?",
        (SEASON, side),
    ).fetchone()
    boss_scores = {i: 0 for i in range(1, 4)}
    enemy_score = 0
    for row in db.execute(
        "SELECT entry FROM union_battle_logs WHERE season=? AND side=?", (SEASON, side)
    ):
        entry = json.loads(row["entry"])
        stage = stages()[entry["params"]["p2"]]
        if stage["kind"] == 1:
            boss_scores[stage["order"]] += entry["params"]["p1"]
        else:
            enemy_score += entry["params"]["p1"]
    buff = sum(
        (
            next(
                (
                    v["level"]
                    for v in reversed(skills()[r["skill_group"]])
                    if v["cost"] <= r["medals"]
                ),
                0,
            )
            for r in db.execute(
                "SELECT skill_group,medals FROM union_skills WHERE season=? AND side=?",
                (SEASON, side),
            )
        )
    )
    return dict(
        unionId=side,
        bossScore=enemy_score,
        medalAmount=medals,
        buffLv=buff,
        enemyScore=[
            dict(enemyIndex=i, totalScore=amount) for i, amount in boss_scores.items()
        ],
        totalScore=score,
    )


def announcements(db, side):
    now = int(time.time())
    db.execute(
        "INSERT OR IGNORE INTO union_announcements VALUES(?,?,?,?,?,?)",
        (SEASON, side, "headline", 428000001, 0, now),
    )
    score = totals(db, side)["totalScore"]
    for row in master.rows(master.master_bytes("unionRaidHeadLine")):
        if row.number(1) == 2 and row.number(2) <= score:
            db.execute(
                "INSERT OR IGNORE INTO union_announcements VALUES(?,?,?,?,?,?)",
                (SEASON, side, "headline", row.number(0), 0, now),
            )
    families = db.execute(
        "SELECT familia,SUM(score) score FROM union_scores WHERE season=? AND side=? GROUP BY familia",
        (SEASON, side),
    ).fetchall()
    for family in families:
        for row in master.rows(master.master_bytes("unionRaidLog")):
            if row.number(1) == 1 and row.number(2) <= family["score"]:
                db.execute(
                    "INSERT OR IGNORE INTO union_announcements VALUES(?,?,?,?,?,?)",
                    (SEASON, side, "log", row.number(0), family["familia"], now),
                )


def status(world, db, side):
    current = load(db, side)
    now = int(time.time())
    sides = [
        dict(totals(db, s), rank=i + 1)
        for i, s in enumerate(
            sorted(SIDES, key=lambda s: (-totals(db, s)["totalScore"], s))
        )
    ]
    pending = bool(
        db.execute(
            "SELECT 1 FROM union_defeat_rewards WHERE season=? AND side=? AND uid=? AND claimed=0",
            (SEASON, side, world.uid),
        ).fetchone()
    )
    return dict(
        code=0,
        available=True,
        unionId=side,
        medalAmount=totals(db, side)["medalAmount"],
        defeatRewardExists=pending,
        lastUpdateDateTime=now,
        lastUpdateIntermediateDateTime=now,
        rank=next((s["rank"] for s in sides if s["unionId"] == side)),
        stageInfo=list(current.values()),
        displayDefeatInfo=[],
        unionBattleInfo=sides,
        unionIntermediateBattleInfo=[
            dict(
                s, beforeMedalAmount=s["medalAmount"], beforeEnemyScore=s["enemyScore"]
            )
            for s in sides
        ],
    )


def capture_adventure(state, adventure, fresh):
    sid = adventure.get("stageId")
    stage = next((s for s in stages().values() if s["adventureStageId"] == sid), None)
    if stage is None:
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
    stamp = row.get("lastTryDateTime", 0)
    record = records.get(str(sid))
    if record is None or record.get("stamp") != stamp:
        key = "unionRaidNormalSP" if stage["kind"] == 0 else "unionRaidBossSP"
        record = dict(
            stamp=stamp,
            passKey=key,
            passesBefore=state["tables"]["UserInfo"][0]["playerStatus"].get(key, 0),
        )
        records[str(sid)] = record
    if adventure.get("events") and all(
        (e.get("isFinished") for e in adventure["events"])
    ):
        record["finished"] = True


def recovery(world, oc):
    if oc is None:
        return
    bootstrap_medal_wallet(world, oc)
    reconcile_mail_medals(world, oc)
    with world.connect() as db:
        if not db.execute(
            "SELECT 1 FROM union_battle_receipts WHERE uid=? AND applied=0 LIMIT 1",
            (world.uid,),
        ).fetchone():
            return
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            "SELECT * FROM union_battle_receipts WHERE uid=? AND applied=0 ORDER BY rowid",
            (world.uid,),
        ).fetchall()
        if not rows:
            return
        with oc.PROGRESS_LOCK:
            state = oc.load_progress()
            info = state["tables"]["UserInfo"]
            for row in rows:
                info[0]["playerStatus"][row["pass_key"]] = row["pass_after"]
                info[0]["playerStatus"][row["pass_key"] + "UpdatedAt"] = row["stamp"]
                state.setdefault("arenaStatusReconciliation", {})[row["pass_key"]] = (
                    row["stamp"]
                )
            wallet = db.execute(
                "SELECT medals FROM union_scores WHERE season=? AND uid=?",
                (SEASON, world.uid),
            ).fetchone()
            if wallet:
                medal_rows = state["tables"].setdefault("UserUnionRaidMedal", [])
                medal = next((r for r in medal_rows if r["medalId"] == 410000001), None)
                if medal is None:
                    medal = dict(userId=world.uid, medalId=410000001, amount=0)
                    medal_rows.append(medal)
                medal["amount"] = wallet["medals"]
                state["tokens"]["UserUnionRaidMedal"] = hashlib.md5(
                    oc.msgpack_value(medal_rows)
                ).hexdigest()
            state["tokens"]["UserInfo"] = hashlib.md5(
                oc.msgpack_value(info)
            ).hexdigest()
            oc.write_progress(state)
            db.execute(
                "UPDATE union_battle_receipts SET applied=1 WHERE uid=?", (world.uid,)
            )


def bootstrap_medal_wallet(world, oc):
    """Adopt an existing native medal wallet only when no shared wallet exists.

    Legacy saves can own medals without a union_scores row. Never overwrite an
    established shared balance from an absolute client snapshot or retry.
    """
    state = oc.load_progress()
    rows = [
        r
        for r in state["tables"].get("UserUnionRaidMedal", [])
        if r.get("medalId") == 410000001
    ]
    if not rows:
        return
    if len(rows) != 1 or rows[0].get("userId", world.uid) != world.uid:
        raise ValueError("Invalid Alliance medal wallet identity")
    amount = integer(rows[0].get("amount"), "Saved Alliance medals")
    if amount < 0:
        raise ValueError("Saved Alliance medals cannot be negative")
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute(
            "SELECT 1 FROM union_scores WHERE season=? AND uid=?", (SEASON, world.uid)
        ).fetchone():
            return
        if not db.execute("SELECT 1 FROM members WHERE uid=?", (world.uid,)).fetchone():
            return
        fid, side = membership(world, db)
        db.execute(
            "INSERT INTO union_scores VALUES(?,?,?,?,0,?)",
            (SEASON, world.uid, fid, side, amount),
        )
        for gift in state["tables"].get("UserGift", []):
            if (
                gift.get("state") == 1
                and state.get("adminGiftItems", {}).get(str(gift["id"]))
                == "UserUnionRaidMedal"
            ):
                db.execute(
                    "INSERT OR IGNORE INTO union_mail_grants VALUES(?,?)",
                    (world.uid, gift["id"]),
                )


def medal_balance(world):
    with world.connect() as db:
        row = db.execute(
            "SELECT medals FROM union_scores WHERE season=? AND uid=?",
            (SEASON, world.uid),
        ).fetchone()
    return row["medals"] if row is not None else None


def reconcile_medal_push(world, row):
    if row.get("medalId") != 410000001:
        return row
    balance = medal_balance(world)
    return dict(row, amount=balance) if balance is not None else row


def reconcile_mail_medals(world, oc):
    state = oc.load_progress()
    gifts = [
        r
        for r in state["tables"].get("UserGift", [])
        if r.get("state") == 1
        and state.get("adminGiftItems", {}).get(str(r["id"])) == "UserUnionRaidMedal"
    ]
    if not gifts:
        return
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM members WHERE uid=?", (world.uid,)).fetchone():
            return
        fid, side = membership(world, db)
        for gift in gifts:
            inserted = db.execute(
                "INSERT OR IGNORE INTO union_mail_grants VALUES(?,?)",
                (world.uid, gift["id"]),
            ).rowcount
            if inserted:
                db.execute(
                    "INSERT INTO union_scores VALUES(?,?,?,?,0,?) ON CONFLICT(season,uid) DO UPDATE SET medals=medals+excluded.medals",
                    (SEASON, world.uid, fid, side, gift["amount"]),
                )


def complete(world, request, oc):
    if oc is None:
        raise ValueError("Union Raid requires account backend")
    recovery(world, oc)
    sid = request.get("unionRaidStageId")
    stage = stages().get(sid) if type(sid) is int else None
    if stage is None or request.get("adventureStageId") != stage["adventureStageId"]:
        raise ValueError("Union Raid stage does not match master")
    if request.get("loopCount") != stage["loop"]:
        raise ValueError("Union Raid loop does not match master")
    training = request.get("isTraining", False)
    if type(training) is not bool:
        raise ValueError("Invalid Union Raid practice flag")
    bosses = request.get("bosses")
    if not isinstance(bosses, list) or len(bosses) > 5:
        raise ValueError("Invalid Union Raid damage list")
    amounts = {}
    for row in bosses:
        if not isinstance(row, dict):
            raise ValueError("Invalid Union Raid boss damage")
        index = integer(row.get("enemyIndex"), "Union boss index", 4)
        if index in amounts:
            raise ValueError("Duplicate Union boss damage")
        amount = integer(row.get("damage"), "Union damage")
        if amount and index not in {b["enemyIndex"] for b in stage["bosses"]}:
            raise ValueError("Union damage targets absent boss")
        amounts[index] = amount
    state = oc.load_progress()
    active = next(
        (
            r
            for r in state["tables"].get("UserAdventureStage", [])
            if r["adventureStageId"] == stage["adventureStageId"]
        ),
        None,
    )
    token = request.get("token")
    native = state.get("extraModeNativeAttempts", {}).get(
        str(stage["adventureStageId"]), {}
    )
    valid = (
        active is not None
        and isinstance(token, str)
        and (
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
    if not valid:
        raise ValueError("Union Raid has no matching active attempt")
    attempt = f"{sid}:{token}:{active.get('lastTryDateTime', 0)}"
    signature = json.dumps(request, sort_keys=True, separators=(",", ":"))
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        fid, side = membership(world, db)
        current = load(db, side)
        target = current[lane(stage)]
        snapshot = dict(
            code=0,
            stageInfo=copy.deepcopy(target),
            rank=0,
            lastBattleInfo={},
            defeatRewardExists=False,
            defeatNow=False,
            score=0,
            totalDamage=sum(amounts.values()),
            isRecorded=False,
            isTraining=training,
            gainedItems=dict(items=[]),
            gainedServerItems=dict(items=[]),
            UserUnionRaidMedal=[],
            operations=[],
            UserAdventureStage=[copy.deepcopy(active)],
            adventureStageTaskIds=[],
            gainedPCs=[],
            gainedCostumes=[],
            friendshipStates=[],
        )
        if training:
            snapshot["stageInfo"]["bosses"] = copy.deepcopy(stage["bosses"])
            snapshot["score"] = sum(
                (min(b["hp"], amounts.get(b["enemyIndex"], 0)) for b in stage["bosses"])
            )
            snapshot["isRecorded"] = True
            return snapshot
        prior = db.execute(
            "SELECT * FROM union_battle_receipts WHERE uid=? AND attempt=?",
            (world.uid, attempt),
        ).fetchone()
        if prior:
            if prior["signature"] != signature:
                raise ValueError("Union Raid completed attempt changed")
            result = json.loads(prior["response"])
        else:
            if target["unionRaidStageId"] != sid:
                raise ValueError("Union Raid boss changed; refresh before battle")
            key = "unionRaidNormalSP" if stage["kind"] == 0 else "unionRaidBossSP"
            passes = state["tables"]["UserInfo"][0]["playerStatus"].get(key, 0)
            initial = native.get("passesBefore", passes)
            if type(initial) is not int or initial <= 0 or passes < 0:
                raise ValueError("No Union Raid passes available")
            common = oc.stage_complete_response(request)
            party = request.get("userPveParty", request.get("userParty"))
            if isinstance(party, dict) and party.get("userId") == world.uid:
                db.execute(
                    "INSERT OR REPLACE INTO union_party_snapshots VALUES(?,?,?)",
                    (SEASON, world.uid, json.dumps(arena.native_party(party))),
                )
            damage = 0
            for boss in target["bosses"]:
                amount = min(boss["hp"], amounts.get(boss["enemyIndex"], 0))
                boss["hp"] -= amount
                damage += amount
            target["familiaScore"] += damage
            now = int(time.time())
            profile = world.profile(db, world.uid)
            entry = dict(
                userId=world.uid,
                playerName=profile["name"],
                leaderPc=profile["leaderPc"],
                attackedAt=now,
                params=dict(p1=damage, p2=sid),
            )
            defeated = all((b["hp"] == 0 for b in target["bosses"]))
            result = dict(
                common,
                **{
                    k: v for k, v in snapshot.items() if k not in ("code", "operations")
                },
            )
            result.update(
                stageInfo=copy.deepcopy(target),
                lastBattleInfo=entry,
                score=damage,
                defeatNow=defeated,
                isRecorded=True,
            )
            medals = int(damage * stage["medalRate"])
            db.execute(
                "INSERT INTO union_scores VALUES(?,?,?,?,?,?) ON CONFLICT(season,uid) DO UPDATE SET score=score+excluded.score,medals=medals+excluded.medals",
                (SEASON, world.uid, fid, side, damage, medals),
            )
            wallet = db.execute(
                "SELECT medals FROM union_scores WHERE season=? AND uid=?",
                (SEASON, world.uid),
            ).fetchone()[0]
            result["UserUnionRaidMedal"] = [
                dict(userId=world.uid, medalId=410000001, amount=wallet)
            ]
            db.execute(
                "INSERT INTO union_battle_logs(season,side,entry) VALUES(?,?,?)",
                (SEASON, side, json.dumps(entry)),
            )
            if defeated:
                next_stage = stages()[stage["next"]]
                current[lane(stage)] = dict(
                    unionRaidStageId=next_stage["id"],
                    loopCount=next_stage["loop"],
                    defeatCount=target["defeatCount"] + 1,
                    bosses=copy.deepcopy(next_stage["bosses"]),
                    familiaScore=target["familiaScore"],
                )
                defeated_count = sum((s["defeatCount"] for s in current.values()))
                rewards = [
                    r.number(0)
                    for r in master.rows(
                        master.master_bytes("unionRaidDefeatCountReward")
                    )
                    if r.number(1) == 418000001 and r.number(2) <= defeated_count
                ]
                for reward in rewards:
                    db.execute(
                        "INSERT OR IGNORE INTO union_defeat_rewards(season,side,uid,reward) SELECT ?,?,m.uid,? FROM members m JOIN union_membership u ON u.familia=m.familia WHERE u.season=? AND u.side=?",
                        (SEASON, side, reward, SEASON, side),
                    )
                result["defeatRewardExists"] = bool(
                    db.execute(
                        "SELECT 1 FROM union_defeat_rewards WHERE season=? AND side=? AND uid=? AND claimed=0",
                        (SEASON, side, world.uid),
                    ).fetchone()
                )
            save(db, side, current)
            db.execute(
                "INSERT INTO union_battle_receipts VALUES(?,?,?,?,?,?,?,0)",
                (
                    world.uid,
                    attempt,
                    signature,
                    json.dumps(result),
                    key,
                    passes if native else passes - 1,
                    now,
                ),
            )
    recovery(world, oc)
    return result


def rank(world, db, side, action):
    if "/familia/" in action:
        rows = db.execute(
            "SELECT familia,SUM(score) score FROM union_scores WHERE season=? AND side=? GROUP BY familia ORDER BY score DESC,familia",
            (SEASON, side),
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
    rows = db.execute(
        "SELECT uid,score FROM union_scores WHERE season=? AND side=? ORDER BY score DESC,uid",
        (SEASON, side),
    ).fetchall()
    return [
        dict(
            rank=i + 1,
            score=r["score"],
            member=world.profile(db, r["uid"]),
            name=world.profile(db, r["uid"])["name"],
            pcId=world.profile(db, r["uid"])["leaderPc"]["pcId"],
            unionId=side,
        )
        for i, r in enumerate(rows)
    ]


def medal_wallet(world, oc):
    if oc is None:
        return
    with world.connect() as db:
        row = db.execute(
            "SELECT medals FROM union_scores WHERE season=? AND uid=?",
            (SEASON, world.uid),
        ).fetchone()
    if row is None:
        return
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        rows = state["tables"].setdefault("UserUnionRaidMedal", [])
        medal = next((r for r in rows if r["medalId"] == 410000001), None)
        if medal is None:
            medal = dict(userId=world.uid, medalId=410000001, amount=0)
            rows.append(medal)
        if medal["amount"] == row["medals"]:
            return
        medal["amount"] = row["medals"]
        state["tokens"]["UserUnionRaidMedal"] = hashlib.md5(
            oc.msgpack_value(rows)
        ).hexdigest()
        oc.write_progress(state)


def skill_info(db, side, group):
    row = db.execute(
        "SELECT medals FROM union_skills WHERE season=? AND side=? AND skill_group=?",
        (SEASON, side, group),
    ).fetchone()
    amount = row["medals"] if row else 0
    candidates = [r for r in skills()[group] if r["cost"] <= amount]
    chosen = candidates[-1] if candidates else skills()[group][0]
    return dict(unionRaidSkillId=chosen["id"], medalAmount=amount)


def mutate(world, action, request, oc, receipt):
    if not isinstance(receipt, str) or not receipt:
        raise ValueError("Union mutation requires transport request identity")
    signature = json.dumps(
        dict(action=action, request=request), sort_keys=True, separators=(",", ":")
    )
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        fid, side = membership(world, db)
        prior = db.execute(
            "SELECT signature,response FROM union_mutation_receipts WHERE uid=? AND receipt=?",
            (world.uid, receipt),
        ).fetchone()
        if prior:
            if prior["signature"] != signature:
                raise ValueError("Union mutation receipt changed")
            result = json.loads(prior["response"])
        elif action == "union_raid/skill/growth":
            group = request.get("unionRaidSkillGroupId")
            if (
                type(group) is not int
                or group not in skills()
                or request.get("medalId") != 410000001
            ):
                raise ValueError("Unknown Alliance skill or medal")
            amount = integer(request.get("medalAmount"), "Alliance medal expenditure")
            wallet = db.execute(
                "SELECT medals FROM union_scores WHERE season=? AND uid=?",
                (SEASON, world.uid),
            ).fetchone()
            if amount <= 0 or wallet is None or amount > wallet["medals"]:
                raise ValueError("Insufficient Alliance medals")
            current = skill_info(db, side, group)["medalAmount"]
            if current + amount > skills()[group][-1]["cost"]:
                raise ValueError("Alliance skill exceeds archived maximum")
            db.execute(
                "UPDATE union_scores SET medals=medals-? WHERE season=? AND uid=?",
                (amount, SEASON, world.uid),
            )
            db.execute(
                "INSERT INTO union_skills VALUES(?,?,?,?) ON CONFLICT(season,side,skill_group) DO UPDATE SET medals=medals+excluded.medals",
                (SEASON, side, group, amount),
            )
            result = dict(
                code=0,
                userMedalAmount=wallet["medals"] - amount,
                unionRaidSkill=skill_info(db, side, group),
            )
        elif action == "union_raid/chat/send":
            messages = request.get("message")
            if not isinstance(messages, list) or not 1 <= len(messages) <= 3:
                raise ValueError("Invalid Alliance chat lines")
            words = {
                r.number(0)
                for r in master.rows(master.master_bytes("unionRaidChatWord"))
            }
            lines = set()
            word_count = 0
            for value in messages:
                if not isinstance(value, dict):
                    raise ValueError("Invalid Alliance chat line")
                line = integer(value.get("line"), "Alliance chat line", 3)
                ids = value.get("wordIds")
                if (
                    line in lines
                    or not isinstance(ids, list)
                    or len(ids) > 10
                    or any((type(i) is not int or i not in words for i in ids))
                ):
                    raise ValueError("Alliance chat words are not archived")
                lines.add(line)
                word_count += len(ids)
            if word_count == 0:
                raise ValueError("Empty Alliance message")
            db.execute(
                "INSERT INTO union_messages(season,side,uid,stamp,message) VALUES(?,?,?,?,?)",
                (SEASON, side, world.uid, int(time.time()), json.dumps(messages)),
            )
            result = dict(code=0)
        else:
            feed = request.get("feedId")
            reaction = request.get("reactions")
            if (
                not isinstance(reaction, list)
                or len(reaction) > 20
                or any((not isinstance(r, dict) for r in reaction))
            ):
                raise ValueError("Invalid Alliance chat reaction")
            reaction = [r for r in reaction if r.get("reactionCount", 0) > 0]
            if len(reaction) != 1:
                raise ValueError("One Alliance reaction per message is supported")
            rid = reaction[0].get("reactionId")
            known = {
                r.number(0)
                for r in master.rows(master.master_bytes("unionRaidChatReaction"))
            }
            message = db.execute(
                "SELECT uid FROM union_messages WHERE id=? AND season=? AND side=?",
                (feed, SEASON, side),
            ).fetchone()
            if (
                message is None
                or request.get("targetUserId") != message["uid"]
                or type(rid) is not int
                or (rid not in known)
            ):
                raise ValueError("Unknown Alliance message or reaction")
            db.execute(
                "INSERT INTO union_reactions VALUES(?,?,?) ON CONFLICT(message,uid) DO UPDATE SET reaction=excluded.reaction",
                (feed, world.uid, rid),
            )
            result = dict(code=0)
        if prior is None:
            result["acceptedAt"] = int(time.time())
            db.execute(
                "INSERT INTO union_mutation_receipts VALUES(?,?,?,?)",
                (world.uid, receipt, signature, json.dumps(result)),
            )
    medal_wallet(world, oc)
    return result


def receive_rewards(world, db, side, oc):
    if oc is None:
        raise ValueError("Alliance rewards require account backend")
    from danmemo.game.reward_delivery import mail as mail
    from danmemo.game.transactions import identities as identities

    pending = db.execute(
        "SELECT reward FROM union_defeat_rewards WHERE season=? AND side=? AND uid=? AND claimed=0",
        (SEASON, side, world.uid),
    ).fetchall()
    masters = {
        r.number(0): r
        for r in master.rows(master.master_bytes("unionRaidDefeatCountReward"))
    }
    changed = False
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        awarded = state.setdefault("unionDefeatRewardReceipts", [])
        for entry in pending:
            marker = f"{SEASON}:{side}:{entry['reward']}"
            row = masters[entry["reward"]]
            iid = row.number(3)
            amount = row.number(4)
            identity = identities().get(iid)
            if identity is None:
                raise ValueError("Union reward resource not recovered")
            if marker not in awarded:
                changed = (
                    mail(
                        oc,
                        state,
                        f"union-defeat:{marker}",
                        f"Union Raid season {SEASON}: confirmed defeat prize {entry['reward']}",
                        [(iid, amount)],
                        int(time.time()),
                    )
                    or changed
                )
        oc.write_progress(state)
        db.execute(
            "UPDATE union_defeat_rewards SET claimed=1 WHERE season=? AND side=? AND uid=?",
            (SEASON, side, world.uid),
        )
    return dict(
        code=0,
        gainedItems=dict(items=[]),
        gainedServerItems=dict(items=[]),
        UserUnionRaidMedal=[],
        operations=[],
        data={"UserGift": state["tables"].get("UserGift", [])} if changed else {},
        dataTokens={"UserGift": state["tokens"]["UserGift"]} if changed else {},
    )


def dispatch(world, action, request, oc=None, receipt=None):
    if action == "union_raid/stage/complete":
        return complete(world, request, oc)
    recovery(world, oc)
    identifier = request.get(
        "unionRaidInfoId", SEASON if action == "union_raid/player_info/get" else None
    )
    if identifier != SEASON:
        raise ValueError("Unknown Union Raid season")
    if action in (
        "union_raid/skill/growth",
        "union_raid/chat/send",
        "union_raid/reaction/send",
    ):
        return mutate(world, action, request, oc, receipt)
    medal_wallet(world, oc)
    with world.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        fid, side = membership(world, db)
        prepared = (
            prepare_test_stages(
                oc,
                "union-" + str(SEASON),
                [r["adventureStageId"] for r in stages().values()],
                dict(unionRaidBossSP=3, unionRaidNormalSP=5),
            )
            if action == "union_raid/status/get"
            else {}
        )
        if action == "union_raid/status/preinitiation":
            stage = stages().get(request.get("unionRaidStageId"))
            if stage is None:
                raise ValueError("Unknown Union Raid stage")
            return dict(code=0, stageInfo=load(db, side)[lane(stage)])
        if action == "union_raid/player_info/get":
            target = request.get("userId", request.get("targetUserId"))
            row = db.execute(
                "SELECT detail FROM player_profile_details WHERE uid=?", (target,)
            ).fetchone()
            if row is None:
                raise ValueError("Union Raid profile not published")
            result = dict(code=0, **json.loads(row["detail"]))
            party = db.execute(
                "SELECT party FROM union_party_snapshots WHERE season=? AND uid=?",
                (SEASON, target),
            ).fetchone()
            if party and party["party"]:
                result["party"] = dict(json.loads(party["party"]), partyType=6, index=0)
            return result
        if "/ranking/" in action:
            return dict(code=0, unionId=side, ranking=rank(world, db, side, action))
        if action == "union_raid/battle/log":
            rows = db.execute(
                "SELECT entry FROM union_battle_logs WHERE season=? AND side=? ORDER BY id DESC LIMIT 100",
                (SEASON, side),
            ).fetchall()
            return dict(code=0, logs=[json.loads(r["entry"]) for r in rows])
        if action == "union_raid/status/log":
            announcements(db, side)
            logs = []
            for r in db.execute(
                "SELECT * FROM union_announcements WHERE season=? AND side=? AND kind='log' ORDER BY stamp DESC,id DESC LIMIT 100",
                (SEASON, side),
            ):
                family = db.execute(
                    "SELECT name,info FROM familias WHERE id=?", (r["familia"],)
                ).fetchone()
                if family:
                    logs.append(
                        dict(
                            logId=r["id"],
                            familiaName=family["name"],
                            completedAt=r["stamp"],
                            godId=json.loads(family["info"]).get("godId", 0),
                        )
                    )
            return dict(code=0, logs=logs)
        if action == "union_raid/head_line/list":
            announcements(db, side)
            rows = db.execute(
                "SELECT * FROM union_announcements WHERE season=? AND side=? AND kind='headline' ORDER BY stamp DESC,id DESC LIMIT 100",
                (SEASON, side),
            ).fetchall()
            return dict(
                code=0,
                unionRaidInfoId=SEASON,
                headlines=[
                    dict(headlineId=r["id"], completedAt=r["stamp"], unionId=side)
                    for r in rows
                ],
            )
        if action == "union_raid/reward/defeat":
            return receive_rewards(world, db, side, oc)
        if action.startswith("union_raid/skill/"):
            return dict(
                code=0, unionRaidSkills=[skill_info(db, side, k) for k in skills()]
            )
        if (
            action.startswith("union_raid/chat/")
            or action == "union_raid/reaction/send"
        ):
            rows = db.execute(
                "SELECT * FROM union_messages WHERE season=? AND side=? ORDER BY id DESC LIMIT 50",
                (SEASON, side),
            ).fetchall()
            messages = []
            for row in rows:
                profile = world.profile(db, row["uid"])
                family = db.execute(
                    "SELECT name FROM familias WHERE id=(SELECT familia FROM members WHERE uid=?)",
                    (row["uid"],),
                ).fetchone()
                reactions = db.execute(
                    "SELECT reaction,COUNT(*) amount FROM union_reactions WHERE message=? GROUP BY reaction",
                    (row["id"],),
                ).fetchall()
                messages.append(
                    dict(
                        userId=row["uid"],
                        feedNo=row["id"],
                        familiaName=family["name"] if family else "",
                        userName=profile["name"],
                        postedAt=row["stamp"],
                        leaderPc=profile["leaderPc"],
                        message=json.loads(row["message"]),
                        reaction=[
                            dict(reactionId=r["reaction"], reactionCount=r["amount"])
                            for r in reactions
                        ],
                    )
                )
            return dict(code=0, unionRaidInfoId=SEASON, messages=messages)
        return dict(status(world, db, side), **prepared)
