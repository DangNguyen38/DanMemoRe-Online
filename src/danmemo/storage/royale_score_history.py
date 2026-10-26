"""Original daily-best aggregation with durable private-server score receipts.

The published daily/tally rules are recovered; tie ordering, legacy import and
transaction receipts are local implementation details, not publisher code.
"""

import json

SCHEMA = "\nCREATE TABLE IF NOT EXISTS royale_score_events(\n season INTEGER NOT NULL,uid INTEGER NOT NULL,attempt TEXT NOT NULL,\n day INTEGER NOT NULL,familia INTEGER NOT NULL,stage INTEGER NOT NULL,\n score INTEGER NOT NULL,party TEXT,stamp INTEGER NOT NULL,source TEXT NOT NULL,\n PRIMARY KEY(uid,attempt));\nCREATE INDEX IF NOT EXISTS royale_score_event_season ON royale_score_events(season,day,uid,stamp);\nCREATE INDEX IF NOT EXISTS royale_score_event_player ON royale_score_events(season,uid,stamp);\nCREATE TABLE IF NOT EXISTS royale_history_imports(\n season INTEGER PRIMARY KEY,unresolved INTEGER NOT NULL);\nCREATE TABLE IF NOT EXISTS royale_join_credits(\n season INTEGER NOT NULL,day INTEGER NOT NULL,uid INTEGER NOT NULL,\n familia INTEGER NOT NULL,joined INTEGER NOT NULL,PRIMARY KEY(season,day,uid));\n"


def day(stamp):
    return (stamp + 32400) // 86400


def tally_cutoff(now):
    start = day(now) * 86400 - 32400
    return max(
        (
            start + hour * 3600
            for hour in (-3, 3, 9, 12, 15, 18, 21)
            if start + hour * 3600 <= now
        )
    )


def record(
    db, season, uid, attempt, familia, stage, score, party, stamp, source="live"
):
    db.execute(
        "INSERT OR IGNORE INTO royale_score_events VALUES(?,?,?,?,?,?,?,?,?,?)",
        (season, uid, attempt, day(stamp), familia, stage, score, party, stamp, source),
    )


def import_legacy(db, season, stages):
    if db.execute(
        "SELECT 1 FROM royale_history_imports WHERE season=?", (season,)
    ).fetchone():
        return
    unresolved = 0
    for receipt in db.execute(
        "SELECT uid,attempt,signature,response FROM extra_mode_receipts WHERE kind='royale'"
    ).fetchall():
        try:
            request = json.loads(receipt["signature"])
            response = json.loads(receipt["response"])
            if response.get("isRecorded") is False:
                continue
            metrics = request.get("userGvEScore") or request
            stage = stages.get(metrics.get("gveStageId", response.get("gveStageId")))
            if not stage or stage["season"] != season:
                continue
            stamp = int(receipt["attempt"].rsplit(":", 1)[1])
            score = response.get("finalScore")
            if type(score) is not int or score < 0 or stamp < 0:
                raise ValueError("Invalid historical score")
            old = db.execute(
                "SELECT familia,party FROM royale_scores WHERE season=? AND uid=? AND stage=?",
                (season, receipt["uid"], stage["id"]),
            ).fetchone()
            record(
                db,
                season,
                receipt["uid"],
                receipt["attempt"],
                old["familia"] if old else 0,
                stage["id"],
                score,
                old["party"] if old else None,
                stamp,
                "legacy-receipt",
            )
        except (ValueError, TypeError, KeyError):
            unresolved += 1
    db.execute(
        "INSERT OR IGNORE INTO royale_history_imports VALUES(?,?)", (season, unresolved)
    )


def adopt_joined_players(db, season, now):
    db.execute(
        "INSERT OR IGNORE INTO royale_join_credits SELECT DISTINCT e.season,e.day,e.uid,m.familia,m.joined FROM royale_score_events e JOIN members m ON m.uid=e.uid WHERE e.season=? AND e.familia=0 AND m.joined<=? AND (m.joined+32400)/86400=e.day",
        (season, now),
    )


JOIN = " LEFT JOIN royale_join_credits c ON c.season=e.season AND c.day=e.day AND c.uid=e.uid "
FAMILY = "CASE WHEN e.familia<>0 THEN e.familia ELSE COALESCE(c.familia,0) END"


def player_totals(db, season, now, familia=None, daily=False):
    adopt_joined_players(db, season, now)
    where = "e.season=? AND e.stamp<=?"
    args = [season, now]
    if familia is not None:
        where += " AND " + FAMILY + "=?"
        args.append(familia)
    if daily:
        where += " AND e.day=?"
        args.append(day(now))
    rows = db.execute(
        "SELECT e.uid,e.day,MAX(e.score) score FROM royale_score_events e "
        + JOIN
        + " WHERE "
        + where
        + " GROUP BY e.uid,e.day",
        args,
    )
    totals = {}
    for row in rows:
        totals[row["uid"]] = totals.get(row["uid"], 0) + row["score"]
    return sorted(totals.items(), key=lambda row: (-row[1], row[0]))


def family_totals(db, season, now, final=False):
    adopt_joined_players(db, season, now)
    cutoff = now if final else tally_cutoff(now)
    rows = db.execute(
        "SELECT e.uid,e.day,"
        + FAMILY
        + " familia,MAX(e.score) score FROM royale_score_events e "
        + JOIN
        + " WHERE e.season=? AND e.stamp<=? AND (e.familia<>0 OR c.joined<=?) GROUP BY e.uid,e.day,"
        + FAMILY,
        (season, cutoff, cutoff),
    )
    totals = {}
    for row in rows:
        if row["familia"] > 0:
            totals[row["familia"]] = totals.get(row["familia"], 0) + row["score"]
    return sorted(totals.items(), key=lambda row: (-row[1], row[0]))


def best(db, season, uid, now):
    row = db.execute(
        "SELECT stage,score,party FROM royale_score_events WHERE season=? AND uid=? ORDER BY score DESC,stamp,attempt LIMIT 1",
        (season, uid),
    ).fetchone()
    legacy = db.execute(
        "SELECT stage,score,party FROM royale_scores WHERE season=? AND uid=? ORDER BY score DESC,stage LIMIT 1",
        (season, uid),
    ).fetchone()
    if legacy and (row is None or legacy["score"] > row["score"]):
        row = legacy
    daily = db.execute(
        "SELECT COALESCE(MAX(score),0) FROM royale_score_events WHERE season=? AND uid=? AND day=?",
        (season, uid, day(now)),
    ).fetchone()[0]
    return (row, daily)
