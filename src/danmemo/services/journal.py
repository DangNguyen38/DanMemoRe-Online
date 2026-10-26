"""Persistent, redacted diagnostic journal shared by both game servers.

SQLite serializes writers across account processes. JSONL is the readable
companion; a logging failure must never prevent gameplay or saving progress.
"""

from danmemo import paths as _paths
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import time
import traceback

ROOT = Path(os.environ.get("DANMEMO_LOG_ROOT", _paths.STATE / "logs"))
MAX_EVENTS = 4000
PRUNE_INTERVAL = 128
MAX_EVENT_BYTES = 64 * 1024
JSONL_BYTES = 32 * 1024 * 1024


def bounded_event(row):
    """Limit diagnostic copies only; never truncate actual game responses."""

    def compact(value):
        if isinstance(value, list):
            return [compact(item) for item in value[:100]]
        if isinstance(value, dict):
            result = {key: compact(item) for key, item in value.items()}
            counts = {
                key: len(item)
                for key, item in value.items()
                if isinstance(item, list) and len(item) > 100
            }
            if counts:
                result["truncatedListCounts"] = counts
            return result
        return value[:1024] if isinstance(value, str) else value

    result = compact(row)
    for key in ("balances", "gifts", "receivedItems", "request"):
        if (
            len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
            <= MAX_EVENT_BYTES
        ):
            break
        if key in result:
            result.pop(key)
            result.setdefault("omittedFields", []).append(key)
    return result


SAFE_IDS = {
    "userId",
    "pcId",
    "selectedPcId",
    "familiaId",
    "pvpId",
    "pvbId",
    "serialPvpId",
    "adventureStageId",
    "pveStageId",
    "stageId",
    "lotteryId",
    "skipCount",
    "count",
    "page",
    "achievementId",
    "guildRaidStageId",
    "guildRaidInformationId",
    "loopCount",
    "gvgId",
    "enemyId",
    "enemyType",
    "pairId",
    "captainUserId",
}
SAFE_IDS.update(
    {
        "gveId",
        "gveStageId",
        "unionRaidInfoId",
        "unionRaidStageId",
        "unionRaidStageIndex",
        "rankingDateAt",
        "totalDamage",
        "maxDamage",
        "turnCount",
        "actionCount",
        "score",
        "medalId",
        "medalAmount",
        "feedId",
    }
)
SAFE_IDS.add("targetEventId")
SAFE_IDS.add("consumeId")
SAFE_IDS.update({"itemId", "itemAmount"})
SAFE_IDS.update(
    {
        "shopProductId",
        "electionId",
        "voteId",
        "voteCount",
        "takarakujiInfoId",
        "pvbStageId",
    }
)
SAFE_IDS.update(
    {
        "elementEventInfoId",
        "elementEventStageId",
        "elementEventStageDifficultyId",
        "pcTotalDamage",
    }
)
SAFE_IDS.update({"underPvpId", "rating", "lastBattleStartAt", "revengeStartAt"})
BOND_TABLES = ("UserLimitBreakItem", "UserGeneralLimitBreakItem")
BOND_FIELDS = (
    "limitBreakItemId",
    "amount",
    "cumulativeAmount",
    "signature",
    "bookEntryState",
)


def request_summary(value):
    result = (
        {
            key: item
            for key, item in value.items()
            if key in SAFE_IDS and type(item) is int
        }
        if isinstance(value, dict)
        else {}
    )
    if isinstance(value, dict) and isinstance(value.get("dpStates"), list):
        result["dpStates"] = [
            {k: r[k] for k in ("pcId", "befDpLv", "aftDpLv") if type(r.get(k)) is int}
            for r in value["dpStates"][:12]
            if isinstance(r, dict)
        ]
    if isinstance(value, dict):
        for key in ("defeat", "isEnemyAnnihilated", "isRevenge"):
            if type(value.get(key)) is bool:
                result[key] = value[key]
        if type(value.get("isTraining")) is bool:
            result["isTraining"] = value["isTraining"]
        if isinstance(value.get("achievementIds"), list):
            result["achievements"] = [
                {
                    k: r[k]
                    for k in ("achievementId", "completedAt")
                    if type(r.get(k)) is int
                }
                for r in value["achievementIds"][:100]
                if isinstance(r, dict)
            ]
        combat = value.get("result")
        if isinstance(combat, dict):
            result["combat"] = {
                key: combat[key]
                for key in ("isWin", "isRetire", "turn", "totalDeathCount")
                if type(combat.get(key)) in (int, bool)
            }
    if isinstance(value, dict) and isinstance(value.get("bosses"), list):
        result["bosses"] = [
            {k: r[k] for k in ("enemyIndex", "damage") if type(r.get(k)) is int}
            for r in value["bosses"][:5]
            if isinstance(r, dict)
        ]
    if isinstance(value, dict) and isinstance(value.get("userGuildRaidScore"), dict):
        score = value["userGuildRaidScore"]
        result["userGuildRaidScore"] = {
            k: score[k] for k in ("guildRaidStageId",) if type(score.get(k)) is int
        }
        damage = score.get("enemyDamage")
        if isinstance(damage, list):
            result["userGuildRaidScore"]["enemyDamage"] = [
                {
                    k: row[k]
                    for k in (
                        "enemyIndex",
                        "damage",
                        "encounterNumber",
                        "enemyId",
                        "enemyPartyId",
                        "domesticHp",
                    )
                    if type(row.get(k)) is int
                }
                for row in damage[:100]
                if isinstance(row, dict)
            ]
    if isinstance(value, dict) and isinstance(value.get("userGvEScore"), dict):
        result["userGvEScore"] = request_summary(value["userGvEScore"])
    if isinstance(value, dict) and isinstance(value.get("userElementEventScore"), dict):
        result["userElementEventScore"] = request_summary(
            value["userElementEventScore"]
        )
    if isinstance(value, dict) and isinstance(value.get("deltas"), list):
        states = []
        statuses = []
        for delta in value["deltas"][:30]:
            puts = delta.get("putItems", {}) if isinstance(delta, dict) else {}
            if isinstance(puts, dict):
                for table in BOND_TABLES:
                    items = puts.get(table)
                    if isinstance(items, list) and items:
                        changes = result.setdefault("bondChanges", {}).setdefault(
                            table, []
                        )
                        changes.extend(
                            (
                                {
                                    k: item[k]
                                    for k in BOND_FIELDS
                                    if type(item.get(k)) is int
                                }
                                for item in items[: max(0, 100 - len(changes))]
                                if isinstance(item, dict)
                            )
                        )
            infos = puts.get("UserInfo", []) if isinstance(puts, dict) else []
            for info in infos[:1] if isinstance(infos, list) else []:
                if not isinstance(info, dict) or not isinstance(
                    info.get("playerStatus"), dict
                ):
                    continue
                safe = {
                    key: info["playerStatus"][key]
                    for key in (
                        "baseSP",
                        "spUpdatedAt",
                        "baseSPUpdatedAt",
                        "totalExp",
                        "level",
                        "underPvpSP",
                        "underPvpSPUpdatedAt",
                        "pvpSP",
                        "pvpSPUpdatedAt",
                        "pvbSP",
                        "pvbSPLastResetAt",
                        "serialPvpSP",
                        "serialPvpSPUpdatedAt",
                    )
                    if type(info["playerStatus"].get(key)) is int
                }
                if type(info.get("userId")) is int:
                    safe["userId"] = info["userId"]
                if safe:
                    statuses.append(safe)
            rows = puts.get("UserAdventureState", []) if isinstance(puts, dict) else []
            for row in rows[:30] if isinstance(rows, list) else []:
                if isinstance(row, dict):
                    safe = request_summary(
                        {
                            "userGuildRaidScore": row.get("userGuildRaidScore"),
                            "userElementEventScore": row.get("userElementEventScore"),
                            "userGvEScore": row.get("userGvEScore"),
                        }
                    )
                    safe["stageId"] = (
                        row.get("stageId") if type(row.get("stageId")) is int else None
                    )
                    states.append(safe)
        if states:
            result["adventureStates"] = states[:30]
        if statuses:
            result["playerStatusChanges"] = statuses[:30]
        if result.get("bondChanges"):
            pcs = []
            for delta in value["deltas"][:30]:
                puts = delta.get("putItems", {}) if isinstance(delta, dict) else {}
                items = puts.get("UserPC", []) if isinstance(puts, dict) else []
                if isinstance(items, list):
                    pcs.extend(
                        (
                            {
                                k: item[k]
                                for k in ("pcId", "limitbreakCount")
                                if type(item.get(k)) is int
                            }
                            for item in items[: max(0, 30 - len(pcs))]
                            if isinstance(item, dict)
                        )
                    )
            if pcs:
                result["limitBreakPcChanges"] = pcs
    return result


def exception_summary(error):
    return dict(
        errorType=type(error).__name__,
        frames=[
            dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
            for frame in traceback.extract_tb(error.__traceback__)[-8:]
        ],
    )


def emit(
    source,
    action,
    *,
    uid=None,
    request_id=None,
    duration_ms=None,
    status=None,
    response=None,
    request=None,
    error=None,
    native=None,
    receipt_id=None,
    root=None,
):
    row = dict(time=time.time(), source=source, action=action, pid=os.getpid())
    if uid is not None:
        row["uid"] = int(uid)
    if request_id is not None:
        row["requestId"] = str(request_id)[:160]
    if receipt_id is not None:
        row["receiptId"] = str(receipt_id)[:160]
    if duration_ms is not None:
        row["milliseconds"] = round(duration_ms, 2)
    if status is not None:
        row["httpStatus"] = status
    row["request"] = request_summary(request)
    if isinstance(response, dict):
        if isinstance(response.get("pvbResult"), dict):
            row["pvbResult"] = {
                k: response["pvbResult"][k]
                for k in (
                    "pvbId",
                    "adventureStageId",
                    "finalScore",
                    "currentRank",
                    "isRecorded",
                )
                if k in response["pvbResult"]
            }
        row["result"] = {
            key: response[key]
            for key in ("code", "result")
            if type(response.get(key)) in (int, bool)
        }
        if type(response.get("skipSPDebit")) is int:
            row["skipSPDebit"] = response["skipSPDebit"]
        if isinstance(response.get("underPvpResult"), dict):
            row["underPvpResult"] = {
                k: v
                for k, v in response["underPvpResult"].items()
                if k in ("underPvpId", "score", "scoreDifference", "gradeId", "isWin")
                and type(v) in (int, bool)
            }
        if "score" in response:
            row["scoreType"] = (
                "object"
                if isinstance(response["score"], dict)
                else type(response["score"]).__name__
            )
            if isinstance(response["score"], dict):
                row["modeScore"] = {
                    key: response["score"][key]
                    for key in (
                        "adventureStageId",
                        "baseScore",
                        "finalScore",
                        "totalDamage",
                        "turnCount",
                        "isRecorded",
                        "defeat",
                    )
                    if type(response["score"].get(key)) in (int, float, bool)
                }
        row_counts = {
            key: len(response[key])
            for key in (
                "logs",
                "ranking",
                "member_daily_ranking",
                "member_total_ranking",
                "underPvpRanking",
                "rivalList",
            )
            if isinstance(response.get(key), list)
        }
        if row_counts:
            row["modeRowCounts"] = row_counts
        combat = response.get("gvgResult")
        if isinstance(combat, dict):
            row["gvgResult"] = {
                key: combat[key]
                for key in (
                    "isWin",
                    "point",
                    "isInside",
                    "isDeathBonus",
                    "isTurnBonus",
                    "defenseCount",
                    "isCaptain",
                    "isSubCaptain",
                )
                if type(combat.get(key)) in (int, bool)
            }
        data = response.get("data", {})
        if isinstance(data, dict):
            infos = data.get("UserInfo")
            if isinstance(infos, dict):
                infos = [infos]
            if isinstance(infos, list):
                row["playerStatusAfter"] = request_summary(
                    {"deltas": [{"putItems": {"UserInfo": infos}}]}
                ).get("playerStatusChanges", [])
            for table, fields in (
                ("UserLimitBreakItem", BOND_FIELDS),
                ("UserGeneralLimitBreakItem", BOND_FIELDS),
                ("UserSkipTicket", ("skipTicketId", "amount")),
                (
                    "UserUnderPvpRevengeTicket",
                    ("underPvpRevengeTicketId", "amount", "reservedAmount"),
                ),
                ("UserAchievement", ("achievementId", "state", "progressParam")),
            ):
                items = data.get(table)
                if isinstance(items, list):
                    if len(items) > 100:
                        row.setdefault("balances", {}).setdefault(
                            "truncatedListCounts", {}
                        )[table] = len(items)
                    row.setdefault("balances", {})[table] = [
                        {key: item[key] for key in fields if type(item.get(key)) is int}
                        for item in items[:100]
                        if isinstance(item, dict)
                    ]
            tickets = data.get("UserGachaTicket")
            if isinstance(tickets, list):
                if len(tickets) > 100:
                    row.setdefault("balances", {}).setdefault(
                        "truncatedListCounts", {}
                    )["UserGachaTicket"] = len(tickets)
                row.setdefault("balances", {})["UserGachaTicket"] = [
                    dict(
                        gachaTicketId=item["gachaTicketId"],
                        amount=len(item["dateList"]),
                    )
                    for item in tickets[:100]
                    if isinstance(item, dict)
                    and type(item.get("gachaTicketId")) is int
                    and isinstance(item.get("dateList"), list)
                ]
        received = response.get("receivedItems")
        if isinstance(received, list):
            if len(received) > 100:
                row.setdefault("truncatedListCounts", {})["receivedItems"] = len(
                    received
                )
            row["receivedItems"] = [
                {
                    key: item[key]
                    for key in ("itemId", "amount")
                    if type(item.get(key)) is int
                }
                for item in received[:100]
                if isinstance(item, dict)
            ]
        gifts = response.get(
            "UserGift", data.get("UserGift") if isinstance(data, dict) else None
        )
        if isinstance(gifts, list):
            if len(gifts) > 100:
                row.setdefault("truncatedListCounts", {})["gifts"] = len(gifts)
            row["gifts"] = [
                {
                    key: item[key]
                    for key in ("id", "itemId", "amount", "state")
                    if type(item.get(key)) is int
                }
                for item in gifts[:100]
                if isinstance(item, dict)
            ]
    if error is not None:
        row.update(exception_summary(error))
    if isinstance(native, dict):
        row["native"] = {
            key: value
            for key, value in native.items()
            if key in ("package", "offset", "signal", "failure", "field")
            and isinstance(value, str)
        }
    row = bounded_event(row)
    try:
        folder = Path(root) if root is not None else ROOT
        folder.mkdir(parents=True, exist_ok=True)
        with (
            closing(sqlite3.connect(folder / "test-journal.sqlite3", timeout=5)) as db,
            db,
        ):
            db.execute(
                "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
            )
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "INSERT INTO events(payload) VALUES(?)", (json.dumps(row),)
            )
            row["eventId"] = cursor.lastrowid
            if cursor.lastrowid % PRUNE_INTERVAL == 0:
                db.execute(
                    "DELETE FROM events WHERE id<=?", (cursor.lastrowid - MAX_EVENTS,)
                )
            mirror = folder / "test-journal.jsonl"
            line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
            if mirror.exists() and mirror.stat().st_size + len(line) > JSONL_BYTES:
                mirror.replace(folder / "test-journal.jsonl.1")
            with mirror.open("ab") as output:
                output.write(line)
    except (OSError, sqlite3.Error):
        pass


def response_summary(body):
    try:
        return json.loads(body)
    except (ValueError, TypeError, UnicodeError):
        return None
