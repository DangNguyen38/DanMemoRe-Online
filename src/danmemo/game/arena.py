"""Local Arena protocol adapters backed by archived seasons and bot parties."""

import copy
import functools
import hashlib
import secrets
import struct
import time
from danmemo.game.daily_windows import mission_day as mission_day
from danmemo.game import rival_matching as matching
from danmemo.master import reader as master


@functools.cache
def indexed(name):
    return {r.number(0, 8): r for r in master.rows(master.master_bytes(name))}


def season(name, identifier):
    if type(identifier) is not int or identifier not in indexed(name):
        raise ValueError("Unknown Arena season")
    return indexed(name)[identifier]


def grade(pvp_id):
    return next(
        (
            r.number(0, 8)
            for r in indexed("pvpGrade").values()
            if r.number(1, 8) == pvp_id
        )
    )


@functools.cache
def grades(pvp_id):
    return tuple((r for r in indexed("pvpGrade").values() if r.number(1, 8) == pvp_id))


def grade_for_score(pvp_id, score, rank=None):
    rows = grades(pvp_id)
    eligible = [
        r
        for r in rows
        if score >= r.number(7)
        and (
            not r.number(6) or (rank is not None and r.number(5) <= rank <= r.number(6))
        )
    ]
    return max(eligible or rows[:1], key=lambda r: r.number(9)).number(0, 8)


def dummy_pc(row):
    names = {
        1: "pcId",
        2: "level",
        3: "currentJobIndex",
        4: "limitbreakCount",
        5: "usableSkillAmount",
        6: "strength",
        7: "vitality",
        8: "intelligence",
        9: "luck",
        10: "agility",
        11: "hp",
        12: "mp",
        13: "physicalAttackPower",
        14: "magicAttackPower",
        15: "defensePower",
    }
    result = {name: row.number(field) for field, name in names.items()}
    result.update(
        weaponId=0,
        armorId=0,
        weaponAbilityIds=[],
        armorAbilityIds=[],
        attackPattern=2,
        currentDP=0,
        isUnlockedHeroSublimate=False,
        heroSublimateLevel=0,
        hasAllStatusBoardOpened=False,
        unlockedAdditionalAbilityAmount=0,
    )
    return result


@functools.cache
def bot_parties(limit=3):
    pcs, slots = (indexed("dummyPvpPc"), indexed("dummyPvpPartySlot"))
    parties = []
    for row in indexed("dummyPvpParty").values():
        party = {
            "userId": 9000000001 + len(parties),
            "attackPattern": 2,
            "slots": [],
            "memoriaSlots": [],
        }
        for number, field in enumerate(range(4, 10), 1):
            slot = slots.get(row.number(field, 8))
            if slot is None:
                break
            pc, assist = (pcs.get(slot.number(1, 8)), pcs.get(slot.number(2, 8)))
            if pc is None or assist is None:
                break
            party["slots"].append(
                {
                    "slotNumber": number,
                    "pc": dummy_pc(pc),
                    "assistingPc": dummy_pc(assist),
                    "isHidden": False,
                }
            )
        if len(party["slots"]) != 6:
            continue
        party["memoriaSlots"] = [
            {
                "slotNumber": n,
                "isHidden": False,
                "otherMemoria": {"memoriaId": 0, "level": 1},
            }
            for n in range(1, 4)
        ]
        parties.append(party)
        if limit is not None and len(parties) == limit:
            return parties
    if parties:
        return parties
    raise ValueError("No valid archived Arena bot parties")


def pvp_record(client, state, identifier):
    settled = next(
        (
            r
            for r in state["tables"].get("UserPvPRecord", [])
            if r["pvpId"] == identifier and r.get("isAggregate")
        ),
        None,
    )
    if settled:
        return copy.deepcopy(settled)
    saved = state.get("arena", {}).get("pvp", {}).get(str(identifier), {})
    shared = getattr(client, "ONLINE_SHARED", None)
    rank = (
        shared.rank_for_score("pvp", identifier, saved.get("score", 1000))
        if shared
        else 1
    )
    gid = grade_for_score(identifier, saved.get("score", 1000), rank)
    return dict(
        userId=client.LOCAL_USER_ID,
        pvpId=identifier,
        rank=rank,
        score=saved.get("score", 1000),
        gradeId=gid,
        winCount=saved.get("winCount", 0),
        loseCount=saved.get("loseCount", 0),
        consecutiveWinCount=saved.get("consecutiveWinCount", 0),
        highestGradeId=max(gid, saved.get("highestGradeId", gid)),
        isAggregate=False,
    )


def serial_highest_grade(saved, gid):
    """Recover unlocked classes from completed courses in this season only."""
    grades = indexed("serialPvpGrade")
    highest = max(gid, saved.get("highestGradeId", gid))
    courses = [
        (gid, saved.get("currentWinCount", 0), saved.get("courseFinished", False))
    ]
    previous = saved.get("previousCourseResult", {})
    courses.append((previous.get("currentGradeId"), previous.get("winCount", 0), True))
    for result in saved.get("completedBattles", {}).values():
        courses.append(
            (
                result.get("serialPvpResult", {}).get("gradeId"),
                result.get("currentWinCount", 0),
                result.get("existResult", False),
            )
        )
    for completed, wins, finished in courses:
        row = grades.get(completed)
        if not row or not finished or wins < row.number(6):
            continue
        following = [
            r
            for r in grades.values()
            if r.number(1) == row.number(1) and r.number(2) > row.number(2)
        ]
        if following:
            highest = max(
                highest, min(following, key=lambda r: r.number(2)).number(0, 8)
            )
    return highest


def summaries(client, action, request, state=None):
    serial = action.startswith("serial_pvp/")
    name, key = (
        ("serialPvpInformation", "serialPvpId")
        if serial
        else ("pvpInformation", "pvpId")
    )
    identifier = request.get(key)
    season(name, identifier)
    state = client.load_progress() if state is None else state
    if serial:
        gid = next(iter(indexed("serialPvpGrade")))
        saved = state.get("arena", {}).get("serial", {}).get(str(identifier), {})
        gid = saved.get("selectedGradeId", gid)
        shared = getattr(client, "ONLINE_SHARED", None)
        rank = (
            shared.rank_for_score("serial", identifier, saved.get("highScore", 0))
            if shared and saved.get("highScore", 0)
            else 0
        )
        record = dict(
            userId=client.LOCAL_USER_ID,
            serialPvpId=identifier,
            rank=rank,
            score=saved.get("highScore", 0),
            cumulativeScore=saved.get("cumulativeScore", 0),
            highestGradeId=serial_highest_grade(saved, gid),
            lastAdversaryIds=saved.get("lastAdversaryIds", []),
            isReceiveReward=False,
            isAggregate=False,
            winCountAtHighScore=saved.get("highScoreWins", 0),
            gradeIdAtHighScore=gid,
            matchingScore=0,
        )
        settled = next(
            (
                r
                for r in state["tables"].get("UserSerialPvpRecord", [])
                if r["serialPvpId"] == identifier and r.get("isAggregate")
            ),
            None,
        )
        if settled:
            record = copy.deepcopy(settled)
        shared = getattr(client, "ONLINE_SHARED", None)
        if shared and record["score"] and (not record.get("isAggregate")):
            record["rank"] = shared.rank_for_score(
                "serial", identifier, record["score"]
            )
        return dict(
            code=0,
            result=0,
            currentScore=saved.get("currentScore", 0),
            selectedGradeId=gid,
            currentWinCount=saved.get("currentWinCount", 0),
            existResult=saved.get("courseFinished", False),
            rivalList=serial_rivals(gid, client, saved),
            serialPvpRecord=[record],
            serialPvpUsingLimitData=dict(
                userId=client.LOCAL_USER_ID,
                serialPvpId=identifier,
                usedPcList=[],
                usedMemoriaList=[],
            ),
            serialPvpRanking=client.ONLINE_SHARED.ranking("serial", identifier)
            if getattr(client, "ONLINE_SHARED", None)
            else [],
        )
    record = pvp_record(client, state, identifier)
    return {
        "code": 0,
        "pvpRecord": [record],
        "pvpRanking": client.ONLINE_SHARED.ranking("pvp", identifier)
        if getattr(client, "ONLINE_SHARED", None)
        else [],
    }


def serial_rivals(gid, client=None, saved=None):
    shared = getattr(client, "ONLINE_SHARED", None)
    if shared:
        return matching.select(
            shared.rivals(gid, True), saved or {}, "serialPvpPlayerInfo"
        )
    profiles = list(indexed("dummyPvpUserProfile").values())
    result = []
    for n, source in enumerate(bot_parties(12)):
        party = copy.deepcopy(source)
        party.update(
            partyType=1,
            index=n,
            partyStrength=sum(
                (
                    s["pc"]["physicalAttackPower"] + s["pc"]["magicAttackPower"]
                    for s in party["slots"]
                )
            ),
        )
        for slot in party["slots"]:
            slot["commandFlow"] = ""
        info = dict(
            userId=party["userId"],
            name=profiles[n % len(profiles)].text(1),
            totalExp=0,
            gradeId=gid,
            rank=n + 1,
            preRank=n + 1,
            score=0,
            leaderPcId=party["slots"][0]["pc"]["pcId"],
            titleBadgeId=0,
            subTitleBadgeId=0,
            isDummy=True,
        )
        result.append(dict(serialPvpPlayerInfo=info, serialPvpParty=party))
    return matching.select(result, saved or {}, "serialPvpPlayerInfo")


def serial_course_result(saved, gid):
    if not saved.get("roundScores"):
        if saved.get("previousCourseResult"):
            return copy.deepcopy(saved["previousCourseResult"])
        raise ValueError("No Telskyura result available")
    return dict(
        code=0,
        roundScore=list(saved["roundScores"]),
        currentGradeId=gid,
        highScorePcId=saved.get("leaderPcId", 0),
        winCount=saved.get("currentWinCount", 0),
        previousRank=0,
        currentRank=1 if saved.get("highScore") else 0,
        isHighScore=saved.get("isHighScore", False),
        aroundRankingData=[],
    )


def serial_menu(client, action, request, request_id=None):
    identifier = request.get("serialPvpId")
    season("serialPvpInformation", identifier)
    if action == "serial_pvp/grade/select":
        gid = request.get("gradeId", request.get("selectedGradeId"))
        season("serialPvpGrade", gid)
        with client.PROGRESS_LOCK:
            state = client.load_progress()
            saved = (
                state.setdefault("arena", {})
                .setdefault("serial", {})
                .setdefault(str(identifier), {})
            )
            if saved.get("activeBattle"):
                raise ValueError("Cannot change grade during a Telskyura battle")
            saved["highestGradeId"] = serial_highest_grade(
                saved, saved.get("selectedGradeId", gid)
            )
            if saved.get("courseFinished") and saved.get("roundScores"):
                saved["previousCourseResult"] = serial_course_result(
                    saved, saved.get("selectedGradeId", gid)
                )
            if saved.get("selectedGradeId") != gid or saved.get("courseFinished"):
                saved.update(
                    selectedGradeId=gid,
                    roundScores=[],
                    currentScore=0,
                    currentWinCount=0,
                    courseFinished=False,
                    isHighScore=False,
                )
            matching.advance(saved, receipt=request_id)
            client.write_progress(state)
    elif action == "serial_pvp/rival_list/update":
        with client.PROGRESS_LOCK:
            state = client.load_progress()
            saved = (
                state.setdefault("arena", {})
                .setdefault("serial", {})
                .setdefault(str(identifier), {})
            )
            if matching.advance(saved, receipt=request_id):
                client.write_progress(state)
    response = summaries(client, "serial_pvp/summary/get", request)
    if action == "serial_pvp/grade/select":
        response["gradeId"] = gid
    return response


def _serial_battle_response(client, state, response, *, persist=True):
    """Deliver recovered 30200/30100 processing-state effects as table deltas.

    Original operations set/clear this selector (25f3b3c); model-party selection
    at 2230394 reads it before using activeSerialPvpPartyId. This adapter omits
    the original operation queue, so deliver the equivalent snapshot, as Under
    does. Retry overlays use current state, never a stale receipt's flag.
    """
    active = any(
        (
            v.get("activeBattle")
            for v in state.get("arena", {}).get("serial", {}).values()
        )
    )
    info = state["tables"]["UserInfo"][0]
    flag = int(active)
    if info.get("processingSerialPvpState") != flag:
        info["processingSerialPvpState"] = flag
        state["tokens"]["UserInfo"] = hashlib.md5(
            client.msgpack_value(state["tables"]["UserInfo"])
        ).hexdigest()
        if persist:
            client.write_progress(state)
    result = copy.deepcopy(response)
    result.update(
        data={"UserInfo": copy.deepcopy(state["tables"]["UserInfo"])},
        dataTokens={"UserInfo": state["tokens"]["UserInfo"]},
        operations=[],
    )
    return result


def serial_battle(client, action, request, request_id):
    """Offline bot matches; use the round score calculated by the original client."""
    identifier = request.get("serialPvpId")
    season("serialPvpInformation", identifier)
    with client.PROGRESS_LOCK:
        state = client.load_progress()
        saved = (
            state.setdefault("arena", {})
            .setdefault("serial", {})
            .setdefault(str(identifier), {})
        )
        gid = saved.get("selectedGradeId", next(iter(indexed("serialPvpGrade"))))
        if action.endswith("/result/get"):
            return serial_course_result(saved, gid)
        active = saved.get("activeBattle")
        if action.endswith("/restart"):
            if (
                not active
                or (
                    "userId" in request
                    and request["userId"] != active["request"]["userId"]
                )
                or (
                    "battleId" in request
                    and request["battleId"] != active["response"]["battleId"]
                )
                or (not ("userId" in request or "battleId" in request))
            ):
                raise ValueError("Unknown Telskyura battle to resume")
            return _serial_battle_response(client, state, active["response"])
        if action.endswith("/start"):
            receipts = saved.setdefault("startReceipts", {})
            previous = receipts.get(str(request_id)) if request_id is not None else None
            if previous:
                if previous["request"] != request:
                    raise ValueError(
                        "Telskyura request nonce was reused with different parameters"
                    )
                return _serial_battle_response(client, state, previous["response"])
            if active:
                if active.get("request") == request:
                    for nonce in (active.get("requestId"), request_id):
                        if nonce is not None:
                            receipts[str(nonce)] = dict(
                                request=copy.deepcopy(request),
                                response=copy.deepcopy(active["response"]),
                            )
                    client.write_progress(state)
                    return _serial_battle_response(client, state, active["response"])
                raise ValueError("A Telskyura battle is already in progress")
            rival = next(
                (
                    r
                    for r in serial_rivals(gid, client, saved)
                    if r["serialPvpPlayerInfo"]["userId"] == request.get("userId")
                ),
                None,
            )
            party = request.get("userParty")
            if (
                rival is None
                or type(request.get("partyIndex")) is not int
                or (not isinstance(party, dict))
                or (party.get("userId") != client.LOCAL_USER_ID)
            ):
                raise ValueError("Invalid Telskyura rival or party")
            party = native_party(party)
            if not any((s["pc"]["pcId"] for s in party["slots"])):
                raise ValueError("Empty Telskyura party")
            if saved.get("courseFinished"):
                saved.update(
                    roundScores=[],
                    currentScore=0,
                    currentWinCount=0,
                    courseFinished=False,
                )
            response = dict(
                code=0,
                result=0,
                battleId=secrets.token_hex(16),
                serialPvpId=identifier,
                preRank=rival["serialPvpPlayerInfo"]["preRank"],
                randomSeed=secrets.randbits(32),
                selectedGradeId=gid,
                substitutedSpineIds=[],
                serialPvpRecord=summaries(
                    client, "serial_pvp/summary/get", request, state
                )["serialPvpRecord"],
                **rival,
            )
            saved["activeBattle"] = dict(
                requestId=request_id,
                request=copy.deepcopy(request),
                userParty=party,
                gradeId=gid,
                response=response,
            )
            if request_id is not None:
                receipts[str(request_id)] = dict(
                    request=copy.deepcopy(request), response=copy.deepcopy(response)
                )
        else:
            bid, result = (request.get("battleId"), request.get("result"))
            completed = saved.setdefault("completedBattles", {})
            if isinstance(bid, str) and bid in completed:
                return _serial_battle_response(client, state, completed[bid])
            metrics = result.get("attack_result") if isinstance(result, dict) else None
            int_fields = (
                "battleMainScore",
                "battleSubScore",
                "characterBonus",
                "technicalPlayBonus",
                "technicalPartyBonus",
                "roundScore",
            )
            if (
                not active
                or bid != active["response"]["battleId"]
                or (not isinstance(result, dict))
                or (type(result.get("isWin")) is not bool)
                or (type(result.get("isRetire")) is not bool)
                or (not isinstance(metrics, dict))
                or any(
                    (
                        type(metrics.get(k)) is not int
                        or not 0 <= metrics[k] <= 2147483647
                        for k in int_fields
                    )
                )
                or (type(metrics.get("gradeBonusRate")) not in (int, float))
                or (not 0 <= metrics["gradeBonusRate"] <= 100000)
            ):
                raise ValueError("Invalid Telskyura battle result")
            won = result["isWin"] and (not result["isRetire"])
            from danmemo.game.mission_sync import record_battle as record_battle

            record_battle(state, "serial", won, int(time.time()))
            score = metrics["roundScore"] if not result["isRetire"] else 0
            saved.setdefault("roundScores", []).append(score)
            saved["currentScore"] = sum(saved["roundScores"])
            saved["cumulativeScore"] = saved.get("cumulativeScore", 0) + score
            saved["battleCount"] = saved.get("battleCount", 0) + 1
            daily = saved.setdefault("dailyBattles", {})
            today = str(mission_day(time.time()))
            daily[today] = daily.get(today, 0) + 1
            saved["currentWinCount"] = saved.get("currentWinCount", 0) + int(won)
            saved["leaderPcId"] = next(
                (
                    s["pc"]["pcId"]
                    for s in active["userParty"]["slots"]
                    if s["pc"]["pcId"]
                )
            )
            saved["courseFinished"] = not won or saved["currentWinCount"] >= season(
                "serialPvpGrade", gid
            ).number(6)
            saved["highestGradeId"] = serial_highest_grade(saved, gid)
            saved["isHighScore"] = saved["courseFinished"] and saved[
                "currentScore"
            ] > saved.get("highScore", 0)
            if saved["isHighScore"]:
                saved.update(
                    highScore=saved["currentScore"],
                    highScoreWins=saved["currentWinCount"],
                )
                saved["bestParty"] = copy.deepcopy(active["userParty"])
            saved.pop("activeBattle")
            matching.advance(saved, rival=active["request"]["userId"])
            response = dict(
                code=0,
                result=0,
                serialPvpResult=dict(
                    serialPvpId=identifier,
                    isWin=won,
                    gradeId=active["gradeId"],
                    score=saved["currentScore"],
                ),
            )
            summary = summaries(client, "serial_pvp/summary/get", request, state)
            response.update(
                {
                    k: summary[k]
                    for k in (
                        "serialPvpRecord",
                        "serialPvpUsingLimitData",
                        "currentScore",
                        "currentWinCount",
                        "selectedGradeId",
                        "existResult",
                    )
                }
            )
            record = summary["serialPvpRecord"][0]
            records = {
                r["serialPvpId"]: r
                for r in state["tables"].get("UserSerialPvpRecord", [])
            }
            records[identifier] = record
            state["tables"]["UserSerialPvpRecord"] = list(records.values())
            state["tokens"]["UserSerialPvpRecord"] = hashlib.md5(
                client.msgpack_value(state["tables"]["UserSerialPvpRecord"])
            ).hexdigest()
            completed[bid] = response
        response = _serial_battle_response(client, state, response, persist=False)
        client.write_progress(state)
        return response


def war_battle(client, action, request, request_id):
    identifier = request.get("pvpId")
    season("pvpInformation", identifier)
    with client.PROGRESS_LOCK:
        state = client.load_progress()
        saved = (
            state.setdefault("arena", {})
            .setdefault("pvp", {})
            .setdefault(str(identifier), {})
        )
        active = saved.get("activeBattle")
        if active and (action.endswith("/start") or action.endswith("/restart")):
            response = active["response"]
            if (
                request.get("battleId", response["battleId"]) != response["battleId"]
                or request.get("userId", response["pvpPlayerInfo"]["userId"])
                != response["pvpPlayerInfo"]["userId"]
            ):
                raise ValueError("War Game resume does not match the pending battle")
            return copy.deepcopy(response)
        if action.endswith("/restart"):
            raise ValueError("Unknown War Game battle to resume")
        if action.endswith("/start"):
            from danmemo.config import modes as admin_game_modes

            period = admin_game_modes.config(
                "online" if hasattr(client, "ONLINE_SHARED") else "offline"
            )["modes"].get("war_game")
            if (
                period
                and identifier == admin_game_modes.selected_season("war_game", period)
                and (not period["start"] <= time.time() < period["end"])
            ):
                raise ValueError("War Game season is outside its battle period")
            if (
                period
                and "seasonId" in period
                and (identifier != admin_game_modes.selected_season("war_game", period))
            ):
                raise ValueError("War Game season is not selected")
            infos = state["tables"].get("UserInfo", [])
            status = infos[0].get("playerStatus", {}) if infos else {}
            if type(status.get("pvpSP")) is not int or status["pvpSP"] <= 0:
                raise ValueError("No War Game passes available")
            counter = saved.get("battleCount", 0)
            shared = getattr(client, "ONLINE_SHARED", None)
            if shared:
                from danmemo.game import war_matching as offline_war_matching

                rivals = shared.war_rivals(
                    identifier, recent=saved.get("lastAdversaryIds", [])
                )
                requested = request.get("userId")
                own_record = pvp_record(client, state, identifier)
                from danmemo.game import war_scoring as offline_war_scoring

                saved.setdefault(
                    "scoringContext",
                    dict(
                        policy=offline_war_scoring.POLICY,
                        previousGradeId=offline_war_scoring.previous_grade(
                            state, identifier
                        ),
                    ),
                )
                rival = (
                    next(
                        (
                            r
                            for r in rivals
                            if r["pvpPlayerInfo"]["userId"] == requested
                        ),
                        None,
                    )
                    if requested
                    else offline_war_matching.select(
                        rivals,
                        user_id=client.LOCAL_USER_ID,
                        season=identifier,
                        score=own_record["score"],
                        grade_id=own_record["gradeId"],
                        battle_count=counter,
                        recent=saved.get("lastAdversaryIds", []),
                    )
                )
                if rival is None:
                    raise ValueError("Unknown shared rival")
                party, player_info = (rival["pvpParty"], rival["pvpPlayerInfo"])
            else:
                party = copy.deepcopy(bot_parties()[counter % len(bot_parties())])
                profile = list(indexed("dummyPvpUserProfile").values())[counter % 3]
                player_info = dict(
                    userId=party["userId"],
                    name=profile.text(1),
                    totalExp=0,
                    gradeId=grade(identifier),
                    score=1000,
                    friendRelationShip=0,
                )
            response = dict(
                code=0,
                battleId=secrets.token_hex(16),
                randomSeed=secrets.randbits(32),
                pvpParty=party,
                substitutedSpineIds=[],
                pvpRecord=[pvp_record(client, state, identifier)],
                pvpPlayerInfo=player_info,
            )
            saved["activeBattle"] = {"requestId": request_id, "response": response}
        else:
            battle_id = request.get("battleId")
            completed = saved.setdefault("completedBattles", {})
            if battle_id in completed:
                response = copy.deepcopy(completed[battle_id])
                response["pvpResult"].setdefault("scoreDifference", 0)
                if "data" in response:
                    response["data"]["UserInfo"] = copy.deepcopy(
                        state["tables"]["UserInfo"]
                    )
                    response["dataTokens"]["UserInfo"] = state["tokens"]["UserInfo"]
                return response
            active = saved.get("activeBattle")
            result = request.get("result")
            if (
                not active
                or battle_id != active["response"]["battleId"]
                or (not isinstance(result, dict))
                or (type(result.get("isWin")) is not bool)
            ):
                raise ValueError("Invalid War Game battle result")
            infos = state["tables"].get("UserInfo", [])
            status = infos[0].get("playerStatus", {}) if infos else {}
            from danmemo.game.pass_recovery import consume_war as consume_war

            consume_war(status, int(time.time()))
            state.setdefault("arenaStatusReconciliation", {})["pvpSP"] = status[
                "pvpSPUpdatedAt"
            ]
            state["tokens"]["UserInfo"] = hashlib.md5(
                client.msgpack_value(infos)
            ).hexdigest()
            won = result["isWin"]
            from danmemo.game.mission_sync import record_battle as record_battle

            record_battle(state, "war", won, int(time.time()))
            today = str(mission_day(time.time()))
            daily = saved.setdefault("dailyBattles", {}).setdefault(
                today, dict(battles=0, wins=0)
            )
            daily["battles"] += 1
            daily["wins"] += int(won)
            score_difference = 0
            if getattr(client, "ONLINE_SHARED", None):
                old_score = saved.get("score", 1000)
                from danmemo.game import war_scoring as offline_war_scoring

                context = saved.setdefault(
                    "scoringContext",
                    dict(
                        policy=offline_war_scoring.POLICY,
                        previousGradeId=offline_war_scoring.previous_grade(
                            state, identifier
                        ),
                    ),
                )
                rank = client.ONLINE_SHARED.rank_for_score("pvp", identifier, old_score)
                calculation = offline_war_scoring.calculate(
                    identifier,
                    score=old_score,
                    rival_score=active["response"]["pvpPlayerInfo"]["score"],
                    grade_id=grade_for_score(identifier, old_score, rank),
                    won=won,
                    wins_before=saved.get("winCount", 0),
                    previous_grade_id=context["previousGradeId"],
                )
                saved["score"] = calculation["score"]
                saved["lastScoreCalculation"] = calculation
                score_difference = saved["score"] - old_score
            saved["winCount"] = saved.get("winCount", 0) + int(won)
            saved["loseCount"] = saved.get("loseCount", 0) + int(not won)
            saved["consecutiveWinCount"] = (
                saved.get("consecutiveWinCount", 0) + 1 if won else 0
            )
            saved["battleCount"] = saved.get("battleCount", 0) + 1
            from danmemo.game.war_matching import record_opponent as record_opponent

            record_opponent(saved, active["response"]["pvpPlayerInfo"]["userId"])
            saved["lastParty"] = copy.deepcopy(
                next(iter(state["tables"].get("UserPvPParty", [])), {})
            )
            record = pvp_record(client, state, identifier)
            saved["highestGradeId"] = record["highestGradeId"]
            response = dict(
                code=0,
                pvpRecord=[record],
                pvpResult=dict(
                    pvpId=identifier,
                    gradeId=record["gradeId"],
                    score=record["score"],
                    scoreDifference=score_difference,
                    isWin=won,
                ),
                data={"UserInfo": copy.deepcopy(infos)},
                dataTokens={"UserInfo": state["tokens"]["UserInfo"]},
                operations=[],
            )
            completed[battle_id] = response
            saved.pop("activeBattle", None)
            records = {r["pvpId"]: r for r in state["tables"].get("UserPvPRecord", [])}
            records[identifier] = record
            state["tables"]["UserPvPRecord"] = list(records.values())
            state["tokens"]["UserPvPRecord"] = hashlib.md5(
                client.msgpack_value(state["tables"]["UserPvPRecord"])
            ).hexdigest()
        client.write_progress(state)
        return copy.deepcopy(response)


def record_buster_score(pvb_stage, metrics, *, legacy=False):
    """Master-backed bonuses, checked against the July 2020 result capture.

    Rates remain in the native model's hundredths of a percent. Arithmetic is
    integer-based with truncation after each bonus; see the scoring web audit.
    The base remains submitted damage; its publisher-side derivation is unknown.
    """
    damage = metrics["totalDamage"]
    if legacy:
        multiplier = struct.unpack("<f", pvb_stage.number(6).to_bytes(4, "little"))[0]
        return dict(
            baseScore=damage,
            maxTurn=15,
            turnBonusRate=0,
            turnBonusScore=0,
            provisionalTotalScore=damage,
            difficultyBonusRate=round(multiplier * 100),
            finalScore=min(2147483647, int(damage * multiplier)),
        )
    bonuses = [
        r
        for r in indexed("pvbSurvivalBonusTable").values()
        if r.number(1, 8) == pvb_stage.number(4, 8)
    ]
    if not bonuses:
        raise ValueError("Missing Record Buster survival bonus table")
    max_turn = max((r.number(4) for r in bonuses))
    turns = min(metrics["turnCount"], max_turn)
    matches = [r for r in bonuses if r.number(3) <= turns <= r.number(4)]
    if len(matches) > 1 or (turns > 0 and (not matches)):
        raise ValueError("Invalid Record Buster survival bonus interval")
    turn_rate = matches[0].number(2) if matches else 0
    difficulty_rate = pvb_stage.number(5)
    turn_score = damage * turn_rate // 10000
    subtotal = damage + turn_score
    return dict(
        baseScore=damage,
        maxTurn=max_turn,
        turnBonusRate=turn_rate,
        turnBonusScore=turn_score,
        provisionalTotalScore=subtotal,
        difficultyBonusRate=difficulty_rate,
        finalScore=subtotal * (10000 + difficulty_rate) // 10000,
    )


def record_buster_result(client, state, request, stage, response, *, legacy=False):
    from danmemo.game.mission_sync import (
        recover_record_buster_counters as recover_record_buster_counters,
    )

    recover_record_buster_counters(state)
    pvb_stage = season("pvbStage", request.get("pvbStageId"))
    if pvb_stage.number(2, 8) != stage["adventureStageId"]:
        raise ValueError("Record Buster stage mismatch")
    metrics = request.get("userPvBScore")
    if not isinstance(metrics, dict) or any(
        (
            type(metrics.get(k)) is not int or metrics[k] < 0 or metrics[k] > 2147483647
            for k in ("totalDamage", "turnCount")
        )
    ):
        raise ValueError("Invalid Record Buster score")
    identifier = pvb_stage.number(1, 8)
    damage = metrics["totalDamage"]
    score = record_buster_score(pvb_stage, metrics, legacy=legacy)
    final = score["finalScore"]
    scores = state.setdefault("arena", {}).setdefault("pvb", {})
    old = scores.get(str(identifier), {})
    previous = old.get("scoreInt64", old.get("score", 0))
    from danmemo.config import modes as mode_schedules

    parent = season("pvbInformation", identifier).number(2, 8)
    period = (
        mode_schedules.selected_record_buster_events().get(parent, {}).get("period")
    )
    server = "online" if getattr(client, "ONLINE_SHARED", None) else "offline"
    configured = mode_schedules.config(server)["modes"].get("record_buster")
    if (
        period is None
        and configured
        and (mode_schedules.selected_season("record_buster", configured) == parent)
    ):
        period = configured
    if period is None and getattr(client, "ONLINE_SHARED", None):
        original = season("adventure", parent)
        period = dict(start=original.number(6, 8), end=original.number(7, 8))
    recorded = period is None or period["start"] <= time.time() <= period["end"]
    new_high = final > previous
    saved_score = max(previous, final) if recorded else previous
    shared = getattr(client, "ONLINE_SHARED", None)
    rank = (
        (shared.rank_for_score("pvb", identifier, saved_score) if shared else 1)
        if saved_score
        else 0
    )
    record = dict(
        userId=client.LOCAL_USER_ID,
        pvbId=identifier,
        groupId=1,
        totalRank=rank,
        groupRank=rank,
        score=min(saved_score, 2147483647),
        scoreInt64=saved_score,
        pvbStageId=request["pvbStageId"] if recorded else old.get("pvbStageId", 0),
        isAggregate=False,
        isReceiveTotalReward=False,
        isReceiveGroupReward=False,
    )
    if recorded and new_high or not old:
        party = copy.deepcopy(state.get("tables", {}).get("UserPvBParty", [{}])[0])
        leader = next(
            (
                s.get("pc", {}).get("pcId", 0)
                for s in party.get("slots", [])
                if s.get("pc", {}).get("pcId")
            ),
            0,
        )
        scores[str(identifier)] = dict(
            record,
            metrics=copy.deepcopy(metrics),
            pcId=leader,
            difficulty=pvb_stage.number(3),
            bestParty=party,
        )
    else:
        record = {key: old[key] for key in record}
    scores[str(identifier)]["cumulativeScore"] = old.get("cumulativeScore", 0) + (
        final if recorded else 0
    )
    stage["userPvBScore"] = dict(metrics, pvbStageId=request["pvbStageId"])
    stage["currentScore"] = final
    stage["highScore"] = max(stage.get("highScore", 0), final)
    response["pvbResult"] = dict(
        score,
        totalDamage=damage,
        turnCount=metrics["turnCount"],
        difficulty=pvb_stage.number(3),
        previousRank=1 if previous else 0,
        currentRank=rank,
        adventureStageId=stage["adventureStageId"],
        isRecorded=recorded,
        aroundPlayerInfo=[],
    )
    response["userPvbRecord"] = record
    response["pvbResult"]["pvbId"] = identifier
    records = {r["pvbId"]: r for r in state["tables"].get("UserPvBRecord", [])}
    records[identifier] = record
    state["tables"]["UserPvBRecord"] = list(records.values())
    state["tokens"]["UserPvBRecord"] = hashlib.md5(
        client.msgpack_value(state["tables"]["UserPvBRecord"])
    ).hexdigest()


def record_summary(client, request):
    identifier = request.get("pvbId")
    season("pvbInformation", identifier)
    state = client.load_progress()
    records = [
        copy.deepcopy(row)
        for row in state["tables"].get("UserPvBRecord", [])
        if row["pvbId"] == identifier
    ]
    if not records:
        records = [
            dict(
                userId=client.LOCAL_USER_ID,
                pvbId=identifier,
                groupId=1,
                totalRank=0,
                groupRank=0,
                score=0,
                scoreInt64=0,
                pvbStageId=0,
                isAggregate=False,
                isReceiveTotalReward=False,
                isReceiveGroupReward=False,
            )
        ]
    shared = getattr(client, "ONLINE_SHARED", None)
    if shared and records[0]["scoreInt64"] and (not records[0].get("isAggregate")):
        rank = shared.rank_for_score("pvb", identifier, records[0]["scoreInt64"])
        records[0].update(totalRank=rank, groupRank=rank)
    ranking = record_ranking(client, "pvb/total/ranking/get", request)[
        "pvbTotalRanking"
    ]
    return dict(
        code=0,
        pvbGroupRankingIndex=0,
        isFinalGroup=True,
        pvbGroupRanking=ranking,
        userPvbRecord=records[0],
    )


def record_ranking(client, action, request):
    identifier = request.get("pvbId")
    season("pvbInformation", identifier)
    if getattr(client, "ONLINE_SHARED", None):
        rows = client.ONLINE_SHARED.ranking("pvb", identifier, "/friend/" in action)
        if "/group/" in action:
            if request.get("pvbGroupRankingIndex", 0) != 0:
                return dict(
                    code=0,
                    pvbGroupRankingIndex=0,
                    isFinalGroup=True,
                    pvbGroupRanking=[],
                )
            return dict(
                code=0, pvbGroupRankingIndex=0, isFinalGroup=True, pvbGroupRanking=rows
            )
        return {
            "code": 0,
            "hasNextPage": False,
            "pvbFriendRanking" if "/friend/" in action else "pvbTotalRanking": rows,
        }
    saved = client.load_progress().get("arena", {}).get("pvb", {}).get(str(identifier))
    rows = []
    if saved and saved["score"]:
        info = client.load_progress()["tables"]["UserInfo"][0]
        rows = [
            dict(
                userId=client.LOCAL_USER_ID,
                name=info["name"],
                pcId=saved.get("pcId", 0),
                gradeId=0,
                rank=1,
                score=saved.get("scoreInt64", saved["score"]),
                difficulty=saved["difficulty"],
            )
        ]
    return {
        "code": 0,
        "hasNextPage": False,
        "pvbFriendRanking" if "/friend/" in action else "pvbTotalRanking": rows,
    }


def native_party(party):
    """Complete nullable snapshot fields required by the native PvP/PvE parser.

    Keep actual combat stats from the request. Defaults describe missing optional
    equipment, ascension and empty slots; they do not strengthen the party.
    """
    result = copy.deepcopy(party)
    result.setdefault("attackPattern", 2)
    if result.get("memoriaSlots") is None:
        result["memoriaSlots"] = [
            dict(
                slotNumber=n, isHidden=False, otherMemoria={"memoriaId": 0, "level": 1}
            )
            for n in range(1, 4)
        ]
    defaults = dict(
        pcId=0,
        level=1,
        currentJobIndex=1,
        limitbreakCount=0,
        usableSkillAmount=0,
        strength=0,
        vitality=0,
        intelligence=0,
        luck=0,
        agility=0,
        hp=0,
        mp=0,
        physicalAttackPower=0,
        magicAttackPower=0,
        defensePower=0,
        weaponId=0,
        armorId=0,
        weaponAbilityIds=[],
        armorAbilityIds=[],
        attackPattern=2,
        currentDP=0,
        isUnlockedHeroSublimate=False,
        heroSublimateLevel=0,
        hasAllStatusBoardOpened=False,
        unlockedAdditionalAbilityAmount=0,
    )
    slots = {}
    for slot in result.get("slots", []):
        if not isinstance(slot, dict) or type(slot.get("slotNumber")) is not int:
            raise ValueError("Invalid Arena party slot")
        number = slot["slotNumber"]
        if not 1 <= number <= 6 or number in slots:
            raise ValueError("Invalid or duplicate Arena party slot")
        slots[number] = slot
    for number in range(1, 7):
        slot = slots.setdefault(number, {"slotNumber": number})
        for key in ("pc", "assistingPc"):
            pc = slot.get(key)
            if pc is not None and (not isinstance(pc, dict)):
                raise ValueError("Invalid Arena character snapshot")
            pc = slot[key] = pc or {}
            for field, value in defaults.items():
                if pc.get(field) is None:
                    pc[field] = copy.deepcopy(value)
    result["slots"] = [slots[n] for n in range(1, 7)]
    return result


def monster_result(client, state, request, stage, response):
    """Offline practice scoring; the original server formula is not archived.

    A clear earns 100 base points and three bonuses of up to 100 each. The
    damage benchmark uses the archived stage coefficient as thousands of HP.
    This interpretation is explicit, not a claim of original scoring parity.
    """
    metrics, party = (request.get("userPVEScore"), request.get("userPveParty"))
    fields = (
        "actionCount",
        "clearTurn",
        "deathCount",
        "frontPCs",
        "maxDamage",
        "memberPCs",
        "totalDamage",
    )
    if (
        not isinstance(metrics, dict)
        or any(
            (
                type(metrics.get(k)) is not int or not 0 <= metrics[k] <= 2147483647
                for k in fields
            )
        )
        or (not isinstance(party, dict))
        or (not isinstance(party.get("slots"), list))
        or (not 1 <= metrics["frontPCs"] <= metrics["memberPCs"] <= 6)
        or (metrics["deathCount"] > metrics["memberPCs"])
        or (metrics["maxDamage"] > metrics["totalDamage"])
    ):
        raise ValueError("Invalid Monsterphilia result")
    rule = next(
        (
            r
            for r in indexed("adventureStageScore").values()
            if r.number(1, 8) == stage["adventureStageId"]
        ),
        None,
    )
    if rule is None:
        raise ValueError("Missing Monsterphilia stage score master")
    benchmark = struct.unpack("<f", rule.number(3).to_bytes(4, "little"))[0] * 1000
    if benchmark <= 0:
        raise ValueError("Invalid Monsterphilia damage benchmark")
    if request.get("isEnemyAnnihilated"):
        components = dict(
            baseScore=100,
            maxDamageBonus=min(100, int(100 * metrics["maxDamage"] / benchmark)),
            turnBonus=100 // max(1, metrics["clearTurn"]),
            actionBonus=0,
            survivorBonus=100
            * (metrics["memberPCs"] - metrics["deathCount"])
            // metrics["memberPCs"],
        )
    else:
        components = dict.fromkeys(
            (
                "baseScore",
                "maxDamageBonus",
                "turnBonus",
                "actionBonus",
                "survivorBonus",
            ),
            0,
        )
    submitted_tasks = request.get("adventureStageTaskIds", request.get("tasks", []))
    completed_tasks = {
        row["adventureStageTaskId"]
        for row in stage.get("tasks", [])
        if row.get("state") == 2 and row["adventureStageTaskId"] in submitted_tasks
    }
    final = sum(components.values()) * (100 + 20 * len(completed_tasks)) // 100
    stage["currentScore"] = final
    stage["highScore"] = max(stage.get("highScore", 0), final)
    response.update(components, finalScore=final, rewardRanks=[])
    if not request.get("isEnemyAnnihilated"):
        return set()
    from danmemo.game.monster_rewards import award as award

    changed = award(client, state, stage, final, response)
    info = state["tables"].get("UserInfo", [{}])[0]
    party = native_party(party)
    party["userId"] = client.LOCAL_USER_ID
    leader = next(
        (
            s.get("pc", {}).get("pcId", 0)
            for s in party["slots"]
            if s.get("pc", {}).get("pcId")
        ),
        0,
    )
    entry = dict(
        userId=client.LOCAL_USER_ID,
        name=info.get("name", ""),
        totalExp=info.get("playerStatus", {}).get("totalExp", 0),
        pcId=leader,
        rank=1,
        score=final,
        pveParty=party,
        clearedAt=int(time.time() * 1000),
    )
    saved = (
        state.setdefault("arena", {})
        .setdefault("pve", {})
        .setdefault(str(stage["adventureStageId"]), {"recent": []})
    )
    saved["recent"] = [entry] + saved["recent"][:2]
    if final > saved.get("best", {}).get("score", -1):
        saved["best"] = entry
    saved["lastMetrics"] = copy.deepcopy(metrics)
    saved["scoreModel"] = "offline-practice-v2"
    return changed


def monster_ranking(client, action, request):
    sid = request.get("adventureStageId")
    info = client.stage_catalog()["stages"].get(str(sid))
    if type(sid) is not int or info is None or info[3] != 12:
        raise ValueError("Not a Monsterphilia stage")
    saved = client.load_progress().get("arena", {}).get("pve", {}).get(str(sid), {})
    rows = (
        saved.get("recent", [])
        if "/recent/" in action
        else [saved["best"]]
        if "best" in saved
        else []
    )
    shared = getattr(client, "ONLINE_SHARED", None)
    if shared:
        import json

        with shared.connect() as db:
            published = [
                json.loads(r["detail"])
                for r in db.execute(
                    "SELECT detail FROM scores WHERE kind='pve' AND season=? ORDER BY score DESC,uid",
                    (sid,),
                )
            ]
        if "/recent/" in action:
            rows = sorted(
                (entry for value in published for entry in value.get("_recent", [])),
                key=lambda r: (-r.get("clearedAt", 0), r["userId"]),
            )
        else:
            rows = [dict(value, rank=rank) for rank, value in enumerate(published, 1)]
    rows = copy.deepcopy(rows[:3])
    for row in rows:
        row.pop("_recent", None)
        row["userRank"] = row.get("totalExp", 0)
        row["pveParty"] = native_party(row["pveParty"])
    return dict(code=0, **{str(n): row for n, row in enumerate(rows)})
