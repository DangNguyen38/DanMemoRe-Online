"""Recovered Under Telskyura wire shape with documented local match policy.

See README.md for scope and limitations. This is not original server code.
"""

import copy
import functools
import hashlib
import secrets
import time
from danmemo.game.daily_windows import mission_day as mission_day
from danmemo.config import modes as modes
from danmemo.game import arena as arena
from danmemo.game import resources as resources

ROUTES = frozenset(
    (
        "under_pvp/" + suffix
        for suffix in (
            "summary/get",
            "rival_list/update",
            "player_info/get",
            "character/ranking/get",
            "battle/start",
            "battle/end",
            "battle/restart",
        )
    )
)
INFO_FIELDS = ("medalAmount", "resumeRivalUserId", "resumeRating")


def repair_userdata_contract(client, state):
    """Repair the singleton Info contract; GameStatus/Revenge are season lists.

    Original input dispatch: 0x27fc974 -> 0x27a9590 (Info object),
    0x27fcf80 -> 0x280e92c (GameStatus array), 0x27fdafc (Revenge array).
    Preserve existing fields and inventory, including legacy extra fields.
    """
    changed = False
    tables = state.get("tables", {})
    legacy = {r.get("userId"): r for r in tables.get("UserUnderPvPGameStatus", [])}
    for row in tables.get("UserUnderPvPInfo", []):
        row_changed = False
        for field in INFO_FIELDS:
            if field not in row:
                value = legacy.get(row.get("userId"), {}).get(field, 0)
                row[field] = value if type(value) is int and value >= 0 else 0
                row_changed = True
        if row_changed:
            _table(client, state, "UserUnderPvPInfo")
            changed = True
    return changed


def reconcile_snapshot_row(state, table, incoming, saved):
    """Keep server-owned battle state through delayed native userdata pushes."""
    if table == "UserUnderPvPInfo":
        incoming = copy.deepcopy(incoming)
        for field in INFO_FIELDS:
            incoming.setdefault(field, saved.get(field, 0))
    seasons = state.get("arena", {}).get("under", {})
    managed = {
        sid
        for sid, s in seasons.items()
        if s.get("activeBattle") or s.get("completedBattles")
    }
    if not managed or not saved:
        return incoming
    if table == "UserUnderPvPRecord":
        return (
            copy.deepcopy(saved)
            if str(incoming.get("underPvpId")) in managed
            else incoming
        )
    fields = {
        "UserInfo": ("processingUnderPvpState",),
        "UserUnderPvPInfo": ("resumeRivalUserId", "resumeRating"),
        "UserUnderPvpRevengeStatus": (
            "underPvpId",
            "isRevenge",
            "revengeUserId",
            "revengeStartAt",
        ),
        "UserUnderPvPGameStatus": ("underPvpId", "lastBattleStartAt"),
    }.get(table, ())
    if fields:
        incoming = copy.deepcopy(incoming)
        for field in fields:
            if field in saved:
                incoming[field] = saved[field]
    return incoming


def selected(client, request, battle=False):
    server = "online" if getattr(client, "ONLINE_SHARED", None) else "offline"
    period = modes.config(server)["modes"].get("telskyura")
    identifier = request.get("underPvpId", modes.selected_season("telskyura", period))
    arena.season("underPvpInformation", identifier)
    if identifier != modes.selected_season("telskyura", period):
        raise ValueError("Under Telskyura season is not selected")
    if battle and (not period or not period["start"] <= time.time() < period["end"]):
        raise ValueError("Under Telskyura season is outside its battle period")
    return identifier


def saved_record(state, identifier):
    saved = state.get("arena", {}).get("under", {}).get(str(identifier))
    if saved is not None:
        return saved
    return next(
        (
            r
            for r in state.get("tables", {}).get("UserUnderPvPRecord", [])
            if r.get("underPvpId") == identifier
        ),
        {},
    )


def grade_for_score(score):
    rows = arena.indexed("underPvpGrade").values()
    return max(
        (r for r in rows if r.number(5) <= score), key=lambda r: r.number(7)
    ).number(0, 8)


def record(client, state, identifier):
    settled = next(
        (
            r
            for r in state["tables"].get("UserUnderPvPRecord", [])
            if r["underPvpId"] == identifier and r.get("isAggregate")
        ),
        None,
    )
    if settled:
        return copy.deepcopy(settled)
    saved = saved_record(state, identifier)
    score = saved.get("score", 0)
    shared = getattr(client, "ONLINE_SHARED", None)
    gid = grade_for_score(score)
    return dict(
        underPvpId=identifier,
        userId=client.LOCAL_USER_ID,
        rank=shared.rank_for_score("under", identifier, score) if shared else 1,
        preRank=saved.get("preRank", 1),
        score=score,
        gradeId=gid,
        winCount=saved.get("winCount", 0),
        loseCount=saved.get("loseCount", 0),
        consecutiveWinCount=saved.get("consecutiveWinCount", 0),
        highestGradeId=max(gid, saved.get("highestGradeId", gid)),
        isAggregate=False,
    )


@functools.cache
def bot_rivals(gid):
    pcs = arena.indexed("dummyUnderPvpPc")
    slots = arena.indexed("dummyUnderPvpPartySlot")
    profiles = list(arena.indexed("dummyUnderPvpUserProfile").values())
    result = []
    sources = list(arena.indexed("dummyUnderPvpParty").values())
    sources.sort(key=lambda r: abs(r.number(2, 8) - gid))
    for source in sources:
        party = dict(userId=source.number(0, 8), attackPattern=2, slots=[])
        for number, field in enumerate(range(5, 11), 1):
            slot = slots.get(source.number(field, 8))
            if slot is None:
                break
            pc, assist = (pcs.get(slot.number(1, 8)), pcs.get(slot.number(2, 8)))
            if pc is None or assist is None:
                break
            party["slots"].append(
                dict(
                    slotNumber=number,
                    isHidden=False,
                    pc=arena.dummy_pc(pc),
                    assistingPc=arena.dummy_pc(assist),
                )
            )
        if len(party["slots"]) != 6:
            continue
        party = arena.native_party(party)
        n = len(result)
        info = dict(
            userId=party["userId"],
            name=profiles[n % len(profiles)].text(1),
            score=0,
            gradeId=source.number(2, 8),
            rank=n + 1,
            preRank=n + 1,
            leaderPcId=party["slots"][0]["pc"]["pcId"],
            titleBadgeId=0,
            subTitleBadgeId=0,
            isDummy=True,
            totalExp=0,
        )
        result.append(dict(rating=n, underPvpPlayerInfo=info, underPvpParty=party))
        if len(result) == 12:
            break
    if not result:
        raise ValueError("No archived Under Telskyura dummy teams")
    return result


def rivals(client, identifier, state, *, all_candidates=False):
    gid = record(client, state, identifier)["gradeId"]
    shared = getattr(client, "ONLINE_SHARED", None)
    result = []
    if shared:
        ranks = {r["userId"]: r for r in shared.ranking("under", identifier)}
        for n, rival in enumerate(shared.rivals(gid, kind="under", season=identifier)):
            info = copy.deepcopy(rival["pvpPlayerInfo"])
            score = ranks.get(info["userId"], {})
            info.update(
                score=score.get("score", 0),
                rank=score.get("rank", n + 1),
                gradeId=grade_for_score(score.get("score", 0)),
            )
            result.append(
                dict(
                    rating=0,
                    underPvpPlayerInfo=info,
                    underPvpParty=arena.native_party(rival["pvpParty"]),
                )
            )
    saved = saved_record(state, identifier)
    pool = result or bot_rivals(gid)
    return (
        copy.deepcopy(pool)
        if all_candidates
        else arena.matching.select(pool, saved, "underPvpPlayerInfo")
    )


def ranking(client, identifier):
    shared = getattr(client, "ONLINE_SHARED", None)
    return shared.ranking("under", identifier) if shared else []


def _table(client, state, name):
    state.setdefault("tokens", {})[name] = hashlib.md5(
        client.msgpack_value(state["tables"][name])
    ).hexdigest()


def _updates(state, names):
    names = list(names)
    for name in (
        "UserUnderPvpRevengeTicket",
        "UserUnderPvPInfo",
        "UserUnderPvPGameStatus",
        "UserUnderPvpRevengeStatus",
        "UserEventItem",
    ):
        if name in state["tables"] and name in state["tokens"]:
            names.append(name)
    return dict(
        data={n: copy.deepcopy(state["tables"][n]) for n in names},
        dataTokens={n: state["tokens"][n] for n in names},
        operations=[],
    )


def _battle_state(client, state, identifier, request=None):
    """Equivalent persisted snapshots for the omitted original operations."""
    info = state["tables"]["UserInfo"][0]
    info["processingUnderPvpState"] = int(request is not None)
    _table(client, state, "UserInfo")
    for name in ("UserUnderPvPInfo", "UserUnderPvpRevengeStatus"):
        rows = state["tables"].setdefault(name, [])
        row = next(
            (
                r
                for r in rows
                if r.get("userId") == client.LOCAL_USER_ID
                and (name == "UserUnderPvPInfo" or r.get("underPvpId") == identifier)
            ),
            None,
        )
        if row is None:
            row = dict(userId=client.LOCAL_USER_ID)
            rows.append(row)
        if name == "UserUnderPvPInfo":
            row.setdefault("medalAmount", 0)
            row.update(
                resumeRivalUserId=request["userId"] if request else 0,
                resumeRating=request["rating"] if request else 0,
            )
        else:
            row.setdefault("initialSetting", False)
            revenge = bool(request and request["isRevenge"])
            row.update(
                underPvpId=identifier,
                isRevenge=revenge,
                revengeUserId=request["userId"] if revenge else 0,
                revengeStartAt=request["revengeStartAt"] if revenge else 0,
            )
        _table(client, state, name)


def _consume(client, state, identifier, revenge, now):
    if revenge:
        tid = arena.season("underPvpInformation", identifier).number(22)
        rows = state["tables"].get("UserUnderPvpRevengeTicket", [])
        row = next(
            (
                r
                for r in rows
                if r.get("underPvpRevengeTicketId") == tid
                and r.get("userId") == client.LOCAL_USER_ID
            ),
            None,
        )
        if not tid or row is None:
            raise ValueError("No Under Telskyura rematch ticket available")
        amount, reserved = (row.get("amount"), row.get("reservedAmount", 0))
        if (
            type(amount) is not int
            or type(reserved) is not int
            or reserved < 0
            or (amount - reserved < 1)
        ):
            raise ValueError("No unreserved Under Telskyura rematch ticket available")
        row["amount"] -= 1
        _table(client, state, "UserUnderPvpRevengeTicket")
        pending = state.setdefault("giftInventoryReconciliation", {}).setdefault(
            "UserUnderPvpRevengeTicket", []
        )
        if tid not in pending:
            pending.append(tid)
        return
    status = state["tables"].get("UserInfo", [{}])[0].get("playerStatus", {})
    if type(status.get("underPvpSP")) is not int or status["underPvpSP"] <= 0:
        raise ValueError("No Under Telskyura passes available")
    status["underPvpSP"] -= 1
    status["underPvpSPUpdatedAt"] = now
    state.setdefault("arenaStatusReconciliation", {})["underPvpSP"] = now
    _table(client, state, "UserInfo")


def _normal_start_medals(client, state, identifier, now):
    """Recovered normal-start op27100 grant; revenge skips this branch."""
    product_id = arena.season("underPvpInformation", identifier).number(17)
    product = next(
        (
            r
            for r in arena.master.rows(arena.master.master_bytes("shopProduct"))
            if r.number(0) == product_id
        ),
        None,
    )
    if product is None:
        raise ValueError("Under Telskyura medal shop product is missing")
    iid = product.number(5, 8)
    if not any(
        (
            r.number(0) == iid
            for r in arena.master.rows(arena.master.master_bytes("eventItem"))
        )
    ):
        raise ValueError("Under Telskyura medal item is missing")
    row = resources.add(
        state["tables"],
        "UserEventItem",
        "eventItemId",
        iid,
        100,
        client.LOCAL_USER_ID,
        now,
    )
    if row["amount"] > 2147483647 or row["cumulativeAmount"] > 2147483647:
        raise ValueError("Under Telskyura medal inventory limit exceeded")
    _table(client, state, "UserEventItem")
    pending = state.setdefault("giftInventoryReconciliation", {}).setdefault(
        "UserEventItem", []
    )
    if iid not in pending:
        pending.append(iid)


def _info(client, state, identifier, started=None):
    rows = state["tables"].setdefault("UserUnderPvPGameStatus", [])
    info = next((r for r in rows if r.get("underPvpId") == identifier), None)
    if info is None:
        info = dict(
            userId=client.LOCAL_USER_ID,
            underPvpId=identifier,
            updatedMatchingCount=0,
            updateRivalList=False,
            lastRivalListUpdateAt=0,
            executeRivalListUpdate=False,
            lastBattleStartAt=0,
        )
        rows.append(info)
    info["underPvpId"] = identifier
    if started is not None:
        info["lastBattleStartAt"] = started
    _table(client, state, "UserUnderPvPGameStatus")
    return info


def _sync_record(client, state, identifier, last_battle=None, rival=None):
    rows = state["tables"].setdefault("UserUnderPvPRecord", [])
    rec = next((r for r in rows if r.get("underPvpId") == identifier), None)
    if rec is None:
        rec = dict(userId=client.LOCAL_USER_ID, underPvpId=identifier)
        rows.append(rec)
    for key, value in dict(
        updatedMatchingCount=0,
        lastAdversaryIds=[],
        lastBattleId="",
        isReceiveReward=False,
        updateRivalList=False,
        lastRivalListUpdateAt=0,
        executeRivalListUpdate=False,
    ).items():
        rec.setdefault(key, value)
    rec.update(record(client, state, identifier))
    rec.pop("preRank", None)
    if last_battle is not None:
        rec["lastBattleId"] = last_battle
    if rival is not None:
        rec["lastAdversaryIds"] = [rival] + [
            uid for uid in rec["lastAdversaryIds"] if uid != rival
        ][:9]
    _table(client, state, "UserUnderPvPRecord")


def _party(client, state, party):
    if not isinstance(party, dict) or party.get("userId") != client.LOCAL_USER_ID:
        raise ValueError("Invalid Under Telskyura party owner")
    if not isinstance(party.get("slots"), list):
        raise ValueError("Invalid Under Telskyura slots")
    result = arena.native_party(party)
    owned = {r.get("pcId") for r in state["tables"].get("UserPC", [])}
    used = []
    for slot in result["slots"]:
        for role in ("pc", "assistingPc"):
            pid = slot[role].get("pcId")
            if type(pid) is not int or pid < 0 or (pid and pid not in owned):
                raise ValueError("Under Telskyura party contains an unowned character")
            if pid:
                used.append(pid)
            for field in (
                "level",
                "currentJobIndex",
                "limitbreakCount",
                "hp",
                "mp",
                "strength",
                "vitality",
                "intelligence",
                "luck",
                "agility",
                "physicalAttackPower",
                "magicAttackPower",
                "defensePower",
            ):
                value = slot[role].get(field)
                if type(value) is not int or not 0 <= value <= 2147483647:
                    raise ValueError("Invalid Under Telskyura combat parameter")
            for field in ("weaponAbilityIds", "armorAbilityIds"):
                values = slot[role].get(field)
                if not isinstance(values, list) or any(
                    (type(v) is not int or v < 0 for v in values)
                ):
                    raise ValueError("Invalid Under Telskyura ability list")
    if not any((s["pc"]["pcId"] for s in result["slots"][:4])):
        raise ValueError("Under Telskyura party has no front unit")
    if len(used) != len(set(used)):
        raise ValueError("Under Telskyura party repeats a unit")
    return result


def profile_response(client, uid, value):
    shared = getattr(client, "ONLINE_SHARED", None)
    if shared and (not value["underPvpPlayerInfo"].get("isDummy", False)):
        value.update(shared.profile_detail(uid))
    return value


def dispatch(client, action, request, request_id=None):
    if action not in ROUTES:
        return None
    if not isinstance(request, dict):
        raise ValueError("Invalid Under Telskyura request")
    identifier = selected(client, request)
    if "/battle/" in action:
        return battle(client, action, request, request_id, identifier)
    if action.endswith("rival_list/update"):
        with client.PROGRESS_LOCK:
            state = client.load_progress()
            saved = (
                state.setdefault("arena", {})
                .setdefault("under", {})
                .setdefault(
                    str(identifier), copy.deepcopy(saved_record(state, identifier))
                )
            )
            if arena.matching.advance(saved, receipt=request_id):
                client.write_progress(state)
    state = client.load_progress()
    rec = record(client, state, identifier)
    if action.endswith("character/ranking/get"):
        shared = getattr(client, "ONLINE_SHARED", None)
        return dict(
            code=0,
            **shared.unit_popularity("under", identifier)
            if shared
            else dict(normal=[], assist=[]),
        )
    entries = rivals(
        client, identifier, state, all_candidates=action.endswith("player_info/get")
    )
    if action.endswith("player_info/get"):
        uid = request.get("userId")
        if type(uid) is not int or uid <= 0:
            raise ValueError("Invalid Under Telskyura player")
        if uid == client.LOCAL_USER_ID:
            info = state["tables"].get("UserInfo", [{}])[0]
            party = saved_record(state, identifier).get("lastParty")
            shared = getattr(client, "ONLINE_SHARED", None)
            if party is None and shared:
                teams = shared.rivals(
                    rec["gradeId"], target=uid, kind="under", season=identifier
                )
                party = teams[0]["pvpParty"] if teams else None
            if party is None:
                raise ValueError("Player has not published an Under Telskyura team")
            return profile_response(
                client,
                uid,
                dict(
                    code=0,
                    underPvpPlayerInfo=dict(
                        rec,
                        name=info.get("name", ""),
                        leaderPcId=next(
                            (
                                s["pc"]["pcId"]
                                for s in party["slots"]
                                if s["pc"]["pcId"]
                            ),
                            0,
                        ),
                        totalExp=info.get("playerStatus", {}).get("totalExp", 0),
                        titleBadgeId=0,
                        subTitleBadgeId=0,
                        isDummy=False,
                    ),
                    underPvpParty=party,
                ),
            )
        target = next(
            (r for r in entries if r["underPvpPlayerInfo"]["userId"] == uid), None
        )
        shared = getattr(client, "ONLINE_SHARED", None)
        if target is None and shared:
            teams = shared.rivals(
                rec["gradeId"], target=uid, kind="under", season=identifier
            )
            if teams:
                info = copy.deepcopy(teams[0]["pvpPlayerInfo"])
                ranked = next(
                    (r for r in ranking(client, identifier) if r["userId"] == uid), {}
                )
                info.update(
                    score=ranked.get("score", 0),
                    rank=ranked.get("rank", 0),
                    gradeId=grade_for_score(ranked.get("score", 0)),
                )
                target = dict(
                    rating=0,
                    underPvpPlayerInfo=info,
                    underPvpParty=arena.native_party(teams[0]["pvpParty"]),
                )
        if target is None:
            raise ValueError("Unknown Under Telskyura player")
        return profile_response(client, uid, dict(code=0, **target))
    return dict(
        code=0,
        underPvpRecord=rec,
        rivalList=entries,
        underPvpRanking=ranking(client, identifier),
    )


def battle(client, action, request, request_id, identifier):
    with client.PROGRESS_LOCK:
        state = client.load_progress()
        previous = copy.deepcopy(saved_record(state, identifier))
        saved = (
            state.setdefault("arena", {})
            .setdefault("under", {})
            .setdefault(str(identifier), previous)
        )
        active = saved.get("activeBattle")
        if action.endswith("/end"):
            bid, result = (request.get("battleId"), request.get("result"))
            if (
                not isinstance(bid, str)
                or not isinstance(result, dict)
                or any((type(result.get(k)) is not bool for k in ("isWin", "isRetire")))
            ):
                raise ValueError("Invalid Under Telskyura battle result")
            completed = saved.setdefault("completedBattles", {})
            if bid in completed:
                receipt = completed[bid]
                if receipt["request"] != request:
                    raise ValueError(
                        "Under Telskyura result differs from the saved receipt"
                    )
                return dict(
                    copy.deepcopy(receipt["response"]),
                    **_updates(
                        state,
                        [
                            "UserInfo",
                            "UserUnderPvPInfo",
                            "UserUnderPvPGameStatus",
                            "UserUnderPvPRecord",
                        ],
                    ),
                )
            if not active or bid != active["response"]["battleId"]:
                raise ValueError("Unknown Under Telskyura battle")
            won = result["isWin"] and (not result["isRetire"])
            old = saved.get("score", 0)
            saved["score"] = max(0, old + (30 if won else -15))
            saved["scoreModel"] = "offline-under-v1"
            saved["winCount"] = saved.get("winCount", 0) + int(won)
            saved["loseCount"] = saved.get("loseCount", 0) + int(not won)
            saved["consecutiveWinCount"] = (
                saved.get("consecutiveWinCount", 0) + 1 if won else 0
            )
            saved["battleCount"] = saved.get("battleCount", 0) + 1
            today = str(mission_day(time.time()))
            daily = saved.setdefault("dailyBattles", {}).setdefault(
                today, dict(battles=0, wins=0)
            )
            daily["battles"] += 1
            daily["wins"] += int(won)
            saved["lastParty"] = active["userParty"]
            saved["lastBattleStartAt"] = active["startedAt"]
            saved["revenge"] = (
                dict(
                    userId=active["request"]["userId"],
                    revengeStartAt=active["startedAt"],
                )
                if not won
                else None
            )
            saved["revengeRival"] = (
                {
                    k: copy.deepcopy(active["response"][k])
                    for k in ("rating", "underPvpPlayerInfo", "underPvpParty")
                }
                if not won
                else None
            )
            rec = record(client, state, identifier)
            saved["highestGradeId"] = rec["highestGradeId"]
            _sync_record(client, state, identifier, bid, active["request"]["userId"])
            _battle_state(client, state, identifier)
            response = dict(
                code=0,
                result=0,
                underPvpRecord=rec,
                underPvpResult=dict(
                    underPvpId=identifier,
                    gradeId=rec["gradeId"],
                    score=rec["score"],
                    scoreDifference=rec["score"] - old,
                    isWin=won,
                ),
            )
            completed[bid] = dict(
                request=copy.deepcopy(request), response=copy.deepcopy(response)
            )
            saved.pop("activeBattle")
            arena.matching.advance(saved, rival=active["request"]["userId"])
            response.update(
                _updates(
                    state,
                    [
                        "UserInfo",
                        "UserUnderPvPInfo",
                        "UserUnderPvPGameStatus",
                        "UserUnderPvPRecord",
                    ],
                )
            )
        elif action.endswith("/restart"):
            if (
                not active
                or any(
                    (
                        request.get(k) != active["request"].get(k)
                        for k in ("userId", "rating", "isRevenge", "revengeStartAt")
                    )
                )
                or request.get("lastBattleStartAt") != active["startedAt"]
            ):
                raise ValueError("Unknown Under Telskyura battle to resume")
            return dict(
                copy.deepcopy(active["response"]),
                **_updates(
                    state,
                    [
                        "UserInfo",
                        "UserUnderPvPInfo",
                        "UserUnderPvPGameStatus",
                        "UserUnderPvPRecord",
                    ],
                ),
            )
        else:
            previous = (
                saved.get("startReceipts", {}).get(str(request_id))
                if request_id is not None
                else None
            )
            if previous:
                if previous["request"] != request:
                    raise ValueError(
                        "Under Telskyura request nonce was reused with different parameters"
                    )
                return dict(
                    copy.deepcopy(previous["response"]),
                    **_updates(
                        state,
                        [
                            "UserInfo",
                            "UserUnderPvPInfo",
                            "UserUnderPvPGameStatus",
                            "UserUnderPvPRecord",
                        ],
                    ),
                )
            if active:
                if active["request"] == request:
                    if request_id is not None:
                        saved.setdefault("startReceipts", {})[str(request_id)] = dict(
                            request=copy.deepcopy(request),
                            response=copy.deepcopy(active["response"]),
                        )
                        client.write_progress(state)
                    return dict(
                        copy.deepcopy(active["response"]),
                        **_updates(
                            state,
                            [
                                "UserInfo",
                                "UserUnderPvPInfo",
                                "UserUnderPvPGameStatus",
                                "UserUnderPvPRecord",
                            ],
                        ),
                    )
                raise ValueError("An Under Telskyura battle is already in progress")
            selected(client, request, battle=True)
            if (
                type(request.get("rating")) is not int
                or type(request.get("isRevenge")) is not bool
            ):
                raise ValueError("Invalid Under Telskyura match parameters")
            if (
                type(request.get("revengeStartAt")) is not int
                or request["revengeStartAt"] < 0
            ):
                raise ValueError("Invalid Under Telskyura revenge timestamp")
            party = _party(client, state, request.get("userParty"))
            target = next(
                (
                    r
                    for r in rivals(
                        client, identifier, state, all_candidates=request["isRevenge"]
                    )
                    if r["underPvpPlayerInfo"]["userId"] == request.get("userId")
                    and r["rating"] == request["rating"]
                ),
                None,
            )
            if request["isRevenge"]:
                revenge = saved.get("revenge")
                if not revenge or any(
                    (request[k] != revenge[k] for k in ("userId", "revengeStartAt"))
                ):
                    raise ValueError("Unknown Under Telskyura rematch")
                frozen = saved.get("revengeRival")
                if frozen and frozen["rating"] == request["rating"]:
                    target = copy.deepcopy(frozen)
            elif request["revengeStartAt"]:
                raise ValueError("Unexpected Under Telskyura revenge timestamp")
            if target is None:
                raise ValueError("Unknown Under Telskyura rival or rating")
            now = int(time.time())
            _consume(client, state, identifier, request["isRevenge"], now)
            if not request["isRevenge"]:
                _normal_start_medals(client, state, identifier, now)
            _info(client, state, identifier, now)
            _battle_state(client, state, identifier, request)
            response = dict(
                code=0,
                result=0,
                battleId=secrets.token_hex(16),
                underPvpId=identifier,
                randomSeed=secrets.randbits(32),
                substitutedSpineIds=[],
                underPvpRecord=record(client, state, identifier),
                isRevenge=request["isRevenge"],
                revengeStartAt=request["revengeStartAt"],
                lastBattleStartAt=now,
                **copy.deepcopy(target),
            )
            _sync_record(client, state, identifier, response["battleId"])
            saved["activeBattle"] = dict(
                requestId=request_id,
                request=copy.deepcopy(request),
                userParty=party,
                startedAt=now,
                response=copy.deepcopy(response),
            )
            if request_id is not None:
                saved.setdefault("startReceipts", {})[str(request_id)] = dict(
                    request=copy.deepcopy(request), response=copy.deepcopy(response)
                )
            saved["revenge"] = None
            response.update(
                _updates(
                    state,
                    [
                        "UserInfo",
                        "UserUnderPvPInfo",
                        "UserUnderPvPGameStatus",
                        "UserUnderPvPRecord",
                    ],
                )
            )
        client.write_progress(state)
        return copy.deepcopy(response)
