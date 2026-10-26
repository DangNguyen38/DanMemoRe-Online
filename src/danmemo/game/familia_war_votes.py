"""Master test voting: reconstructed quotas, recovered native response fields."""

import json
import time
from danmemo.game.daily_windows import mission_day as mission_day

ROUTES = {"gvg/tournament/vote", "gvg/tournament/support/vote"}
SCHEMA = "\nCREATE TABLE IF NOT EXISTS familia_war_votes(\n season INTEGER NOT NULL, uid INTEGER NOT NULL, familia INTEGER NOT NULL,\n votes INTEGER NOT NULL DEFAULT 0, support INTEGER NOT NULL DEFAULT 0,\n PRIMARY KEY(season,uid,familia));\nCREATE TABLE IF NOT EXISTS familia_war_vote_receipts(\n season INTEGER NOT NULL, uid INTEGER NOT NULL, receipt TEXT NOT NULL,\n signature TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(season,uid,receipt));\nCREATE TABLE IF NOT EXISTS familia_war_vote_days(\n season INTEGER NOT NULL,uid INTEGER NOT NULL,day INTEGER NOT NULL,\n familia INTEGER NOT NULL,enemy INTEGER NOT NULL,votes INTEGER NOT NULL,\n support INTEGER NOT NULL,stamp INTEGER NOT NULL,\n PRIMARY KEY(season,uid,day,familia));\n"


def decorate(world, db, season, value):
    from danmemo.config import modes as modes

    now = int(time.time())
    day = mission_day(now)
    period = modes.config("online")["modes"].get("master_familia_war_game")
    active = bool(
        period
        and season == modes.selected_season("master_familia_war_game", period)
        and (period["start"] <= now < period["end"])
    )
    used = db.execute(
        "SELECT COALESCE(SUM(votes),0),COALESCE(SUM(support),0) FROM familia_war_vote_days WHERE season=? AND uid=? AND day=?",
        (season, world.uid, day),
    ).fetchone()
    supported = db.execute(
        "SELECT familia FROM familia_war_vote_days WHERE season=? AND uid=? AND day=? AND support>0",
        (season, world.uid, day),
    ).fetchone()
    selected = next(
        (e for e in value["familias"] if supported and e["familiaId"] == supported[0]),
        None,
    )
    value["user"].update(
        supporterTicketCount=max(0, 1 - used[1]),
        voteTicketCount=max(0, 5 - used[0]),
        supportPairId=selected["matching"]["pairId"] if selected else None,
        supportFamiliaId=supported[0] if supported else None,
        canSupport=active and bool(value["familias"]) and (used[1] < 1),
        canVote=active and bool(value["familias"]) and (used[0] < 5),
        canSpectate=False,
    )
    for entry in value["familias"]:
        entry["userVoteCount"] = db.execute(
            "SELECT COALESCE(SUM(votes),0) FROM familia_war_votes WHERE season=? AND familia=?",
            (season, entry["familiaId"]),
        ).fetchone()[0]
    return value


def dispatch(world, db, action, request, receipt):
    from danmemo.game import familia_war as war
    from danmemo.config import modes as modes

    sid = war.season(request)
    period = modes.config("online")["modes"].get("master_familia_war_game")
    selected = modes.selected_season("master_familia_war_game", period)
    if sid != selected or not isinstance(receipt, str) or (not receipt):
        raise ValueError("Master voting requires a transport receipt")
    signature = json.dumps(dict(action=action, request=request), sort_keys=True)
    prior = db.execute(
        "SELECT * FROM familia_war_vote_receipts WHERE season=? AND uid=? AND receipt=?",
        (sid, world.uid, receipt),
    ).fetchone()
    if prior:
        if prior["signature"] != signature:
            raise ValueError("Master vote receipt changed")
        return json.loads(prior["response"])
    now = int(time.time())
    if not period or not period["start"] <= now < period["end"]:
        raise ValueError("Master voting period has ended")
    current = war.tournament(world, db)
    target = request.get("familiaId")
    if type(target) is not int or not any(
        (e["familiaId"] == target for e in current["familias"])
    ):
        raise ValueError("Familia is not in the Master bracket")
    support = action.endswith("/support/vote")
    count = 1 if support else request.get("voteCount")
    stock = current["user"]["supporterTicketCount" if support else "voteTicketCount"]
    if type(count) is not int or not 1 <= count <= stock:
        raise ValueError("Insufficient Master test voting tickets")
    db.execute(
        "INSERT INTO familia_war_votes VALUES(?,?,?,?,?) ON CONFLICT(season,uid,familia) DO UPDATE SET votes=votes+excluded.votes, support=support+excluded.support",
        (sid, world.uid, target, 0 if support else count, int(support)),
    )
    entry = next((e for e in current["familias"] if e["familiaId"] == target))
    enemy = entry["matching"]["enemyFamilia"]["familiaId"]
    db.execute(
        "INSERT INTO familia_war_vote_days VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(season,uid,day,familia) DO UPDATE SET votes=votes+excluded.votes, support=support+excluded.support",
        (
            sid,
            world.uid,
            mission_day(now),
            target,
            enemy,
            0 if support else count,
            int(support),
            now,
        ),
    )
    response = war.tournament(world, db)
    db.execute(
        "INSERT INTO familia_war_vote_receipts VALUES(?,?,?,?,?)",
        (sid, world.uid, receipt, signature, json.dumps(response)),
    )
    return response
