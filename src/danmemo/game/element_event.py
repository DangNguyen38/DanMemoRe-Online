"""Seventh Zone native wire adapter; damage scoring is a documented reconstruction.

Original server bonus equations are absent. Do not interpret unused master
coefficients as verified score rules. See README.md for scope and limitations.
"""

import copy
import functools
import json
from danmemo.master import reader as master

ROUTES = {
    "element_event/summary/get",
    "element_event/stage/complete",
    "element_event/ranking/top/get",
    "element_event/ranking/around/get",
    "element_event/ranking/character/get",
}


@functools.cache
def catalogs():
    infos = {
        r.number(0, 8): dict(id=r.number(0, 8), adventure=r.number(1, 8), tag=r.text(5))
        for r in master.rows(master.master_bytes("elementEventInformation"))
    }
    zones = {
        r.number(0, 8): dict(id=r.number(0, 8), info=r.number(1, 8))
        for r in master.rows(master.master_bytes("elementEventStage"))
    }
    stages = {
        r.number(2, 8): dict(
            id=r.number(0, 8),
            zone=r.number(1, 8),
            adventureStageId=r.number(2, 8),
            difficulty=r.number(3),
        )
        for r in master.rows(master.master_bytes("elementEventStageDifficulty"))
    }
    return (infos, zones, stages)


def capture_adventure(state, adventure):
    sid = adventure.get("stageId")
    if sid not in catalogs()[2]:
        return
    stage = next(
        (
            r
            for r in state["tables"].get("UserAdventureStage", [])
            if r.get("adventureStageId") == sid
        ),
        None,
    )
    if not stage:
        return
    records = state.setdefault("extraModeNativeAttempts", {})
    stamp = stage.get("lastTryDateTime", 0)
    if records.get(str(sid), {}).get("stamp") != stamp:
        records[str(sid)] = dict(stamp=stamp)
    if adventure.get("events") and all(
        (e.get("isFinished") for e in adventure["events"])
    ):
        records[str(sid)]["finished"] = True


def complete(oc, request):
    sid = request.get("adventureStageId")
    stage = catalogs()[2].get(sid)
    metrics = request.get("userElementEventScore")
    if not stage or not isinstance(metrics, dict):
        raise ValueError("Unknown Seventh Zone stage or score")
    for key in (
        "totalDamage",
        "maxDamage",
        "actionCount",
        "turnCount",
        "pcTotalDamage",
    ):
        value = metrics.get(key, 0)
        if type(value) is not int or not 0 <= value <= 2147483647:
            raise ValueError("Invalid Seventh Zone " + key)
    defeated = metrics.get("defeat", request.get("isEnemyAnnihilated", False))
    if type(defeated) is not bool:
        raise ValueError("Invalid Seventh Zone defeat flag")
    with oc.PROGRESS_LOCK:
        saved = oc.load_progress()
        row = next(
            (
                r
                for r in saved["tables"].get("UserAdventureStage", [])
                if r["adventureStageId"] == sid
            ),
            {},
        )
        key = f"{sid}:{request.get('token', '')}:{row.get('lastTryDateTime', 0)}"
        signature = json.dumps(request, sort_keys=True)
        prior = saved.get("elementEventCompletions", {}).get(key)
        if prior and prior["signature"] != signature:
            raise ValueError("Seventh Zone completed attempt changed")
        if prior:
            result = copy.deepcopy(prior["response"])
        else:
            result = oc.stage_complete_response(
                dict(
                    request,
                    token=request.get("token", ""),
                    isEnemyAnnihilated=defeated,
                    userElementEventScore=metrics,
                )
            )
            saved = oc.load_progress()
            records = saved.setdefault("elementEventScores", {})
            damage = metrics.get("totalDamage", 0)
            old = records.get(str(sid), {}).get("score", 0)
            score = dict(
                adventureStageId=sid,
                totalDamage=damage,
                maxDamage=metrics.get("maxDamage", 0),
                actionCount=metrics.get("actionCount", 0),
                defeat=defeated,
                baseScore=damage,
                maxDamageBonusRate=0.0,
                maxDamageBonusScore=0,
                provisionalTotalScore=damage,
                actionCountBonusRate=0.0,
                finalScore=damage,
                turnCount=metrics.get("turnCount", 0),
                turnCountBonusScore=0,
                turnCountBonusRate=0.0,
                pcTotalDamage=metrics.get("pcTotalDamage", 0),
                pcDamageBonusScore=0,
            )
            result.update(score=score, isEnemyAnnihilated=defeated)
            if damage >= old:
                records[str(sid)] = dict(
                    score=damage, zone=stage["zone"], difficulty=stage["difficulty"]
                )
            saved.setdefault("elementEventCompletions", {})[key] = dict(
                signature=signature, response=copy.deepcopy(result)
            )
            oc.write_progress(saved)
        world = getattr(oc, "ONLINE_SHARED", None)
        if world:
            score = result["score"]["finalScore"]
            with world.connect() as db:
                db.execute(
                    "INSERT INTO scores VALUES(?,?,?,?,?) ON CONFLICT(kind,season,uid) DO UPDATE SET detail=CASE WHEN excluded.score>score THEN excluded.detail ELSE detail END,score=MAX(score,excluded.score)",
                    (
                        "element",
                        stage["zone"],
                        world.uid,
                        score,
                        json.dumps(dict(difficultyRank=stage["difficulty"])),
                    ),
                )
                party = request.get("userPveParty")
                if not isinstance(party, dict):
                    info = next(iter(saved["tables"].get("UserInfo", [])), {})
                    selected = next(
                        (
                            p
                            for p in saved["tables"].get("UserParty", [])
                            if p.get("id") == info.get("activePartyId")
                        ),
                        None,
                    )
                    if selected:
                        party = dict(
                            slots=[
                                dict(
                                    pc=dict(pcId=s.get("pcId", 0)),
                                    assistingPc=dict(pcId=s.get("assistingPcId", 0)),
                                )
                                for s in selected.get("slots", [])
                            ]
                        )
                if isinstance(party, dict):
                    db.execute(
                        "INSERT INTO unit_usage VALUES(?,?,?,?) ON CONFLICT(kind,season,uid) DO UPDATE SET party=excluded.party",
                        ("element", stage["zone"], world.uid, json.dumps(party)),
                    )
        return result


def dispatch(oc, action, request):
    if action == "element_event/stage/complete":
        return complete(oc, request)
    infos, zones, stages = catalogs()
    world = getattr(oc, "ONLINE_SHARED", None)
    if action == "element_event/summary/get":
        info = request.get("elementEventInfoId")
        if info not in infos:
            raise ValueError("Unknown Seventh Zone season")
        ids = [sid for sid, s in stages.items() if zones[s["zone"]]["info"] == info]
        from danmemo.game.extra_modes import prepare_test_stages as prepare_test_stages

        prepared = prepare_test_stages(oc, "seventh-" + str(info), ids)
        saved = oc.load_progress().get("elementEventScores", {})
        return dict(
            code=0,
            adventureStageRecords=[
                dict(
                    adventureStageId=sid, score=saved.get(str(sid), {}).get("score", 0)
                )
                for sid in ids
            ],
            **prepared,
        )
    zone = request.get("elementEventStageId")
    if zone not in zones:
        raise ValueError("Unknown Seventh Zone area")
    if action == "element_event/ranking/character/get":
        return dict(
            code=0,
            **world.unit_popularity("element", zone)
            if world
            else dict(normal=[], assist=[]),
        )
    ranking = []
    if world:
        with world.connect() as db:
            for index, r in enumerate(
                db.execute(
                    "SELECT * FROM scores WHERE kind=? AND season=? ORDER BY score DESC,uid",
                    ("element", zone),
                )
            ):
                profile = world.profile(db, r["uid"])
                ranking.append(
                    dict(
                        userId=r["uid"],
                        userName=profile["name"],
                        rank=index + 1,
                        score=r["score"],
                        difficultyRank=json.loads(r["detail"]).get("difficultyRank", 0),
                        leaderPc=profile["leaderPc"],
                    )
                )
    else:
        saved = oc.load_progress()
        records = saved.get("elementEventScores", {})
        score = max(
            (v["score"] for v in records.values() if v["zone"] == zone), default=0
        )
        if score:
            info = saved["tables"]["UserInfo"][0]
            ranking = [
                dict(
                    userId=oc.LOCAL_USER_ID,
                    userName=info.get("name", ""),
                    rank=1,
                    score=score,
                    difficultyRank=max(
                        (v["difficulty"] for v in records.values() if v["zone"] == zone)
                    ),
                    leaderPc=dict(
                        pcId=info.get("leaderPcId", 0),
                        level=1,
                        jobRank=0,
                        heroSublimateLevel=0,
                        isUnlockedHeroSublimate=False,
                    ),
                )
            ]
    if action == "element_event/ranking/around/get" and ranking:
        own = next(
            (i for i, r in enumerate(ranking) if r["userId"] == oc.LOCAL_USER_ID), 0
        )
        ranking = ranking[max(0, own - 5) : own + 6]
    return dict(code=0, ranking=ranking[:100])
