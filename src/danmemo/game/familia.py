"""Recovered Familia management/chat wire fields with local permission policies.

Uses the caller's shared SQLite transaction; no mutation of player inventories.
See README.md for scope and limitations. for provenance and limits.
"""

import json
import functools
from danmemo.master import reader as master

ROUTES = {
    "familia/invite/search",
    "familia/invite/send",
    "familia/invited/accept",
    "familia/kick",
    "familia/break",
    "familia/manage/appoint_submaster",
    "familia/manage/dismessed_submaster",
    "familia/manage/change_master",
    "familia/manage/change/info",
    "familia/manage/change/god",
    "familia/msg_board/send",
    "familia/msg_board/feed",
    "familia/msg_board/feed_by_no",
}
SCHEMA = "\n    CREATE TABLE IF NOT EXISTS member_roles(uid INTEGER PRIMARY KEY, role INTEGER NOT NULL);\n    CREATE TABLE IF NOT EXISTS familia_invites(familia INTEGER NOT NULL, target INTEGER NOT NULL,\n        sender INTEGER NOT NULL, created INTEGER NOT NULL, PRIMARY KEY(familia,target));\n    CREATE TABLE IF NOT EXISTS familia_messages(no INTEGER PRIMARY KEY AUTOINCREMENT,\n        familia INTEGER NOT NULL, user INTEGER NOT NULL, content TEXT NOT NULL,\n        created INTEGER NOT NULL);\n"
TEXT_POST_TYPE = 40550187


def system_notice(db, fid, uid, profile, message_id, parameters, now):
    content = dict(
        postType=message_id,
        message={"p" + str(i): str(value) for i, value in enumerate(parameters, 1)},
        userName=profile["name"],
        pcId=profile["leaderPc"]["pcId"],
    )
    db.execute(
        "INSERT INTO familia_messages(familia,user,content,created) VALUES(?,?,?,?)",
        (fid, uid, json.dumps(content), now),
    )


@functools.cache
def stamp_ids():
    return {r.number(0, 4) for r in master.rows(master.master_bytes("familiaStamp"))}


def role(db, uid, fid):
    family = db.execute("SELECT owner FROM familias WHERE id=?", (fid,)).fetchone()
    if family and family["owner"] == uid:
        return 1
    saved = db.execute("SELECT role FROM member_roles WHERE uid=?", (uid,)).fetchone()
    return saved["role"] if saved else 3


def positive(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid Familia identifier")
    return value


def dispatch(world, db, action, request, member, now):
    if action not in ROUTES:
        return (False, None)
    if action == "familia/invited/accept":
        fid = positive(request.get("familiaId"))
        if (
            member
            or not db.execute("SELECT 1 FROM familias WHERE id=?", (fid,)).fetchone()
        ):
            raise ValueError("Cannot accept Familia invitation")
        if not db.execute(
            "SELECT 1 FROM familia_invites WHERE familia=? AND target=?",
            (fid, world.uid),
        ).fetchone():
            raise ValueError("No invitation for this account")
        db.execute("INSERT INTO members VALUES(?,?,?)", (world.uid, fid, now))
        db.execute("DELETE FROM familia_invites WHERE target=?", (world.uid,))
        db.execute("DELETE FROM join_requests WHERE uid=?", (world.uid,))
        return (
            True,
            dict(
                code=0,
                result=0,
                joinableAt=0,
                status=dict(familiaId=fid, recievedRequest=False),
            ),
        )
    if not member:
        raise ValueError("Familia membership required")
    fid = member["familia"]
    if "familiaId" in request and positive(request["familiaId"]) != fid:
        raise ValueError("Cannot manage another Familia")
    own_role = role(db, world.uid, fid)
    family = db.execute("SELECT * FROM familias WHERE id=?", (fid,)).fetchone()
    if not family:
        raise ValueError("Unknown Familia")
    if action.startswith("familia/msg_board/"):
        if action.endswith("/send"):
            post = request.get("postType")
            message = request.get("message")
            valid = type(post) is int and (
                post == TEXT_POST_TYPE
                and isinstance(message, str)
                and (1 <= len(message.strip()) <= 300)
                or (post == 0 and type(message) is int and (message in stamp_ids()))
            )
            if not valid:
                raise ValueError("Unsupported Familia message type or content")
            profile = world.profile(db, world.uid)
            content = dict(
                postType=post,
                message=message,
                userName=profile["name"],
                pcId=profile["leaderPc"]["pcId"],
            )
            db.execute(
                "INSERT INTO familia_messages(familia,user,content,created) VALUES(?,?,?,?)",
                (fid, world.uid, json.dumps(content), now),
            )
        rows = db.execute(
            "SELECT * FROM familia_messages WHERE familia=? AND created>? ORDER BY no DESC LIMIT 100",
            (fid, now - 30 * 86400),
        ).fetchall()
        if action.endswith("/feed_by_no"):
            number = request.get("no", 0)
            condition = request.get("cond", 0)
            limit = request.get("resultCount", 100)
            if (
                type(number) is not int
                or number < 0
                or type(condition) is not int
                or (condition not in (0, 1))
                or (type(limit) is not int)
                or (not 1 <= limit <= 100)
            ):
                raise ValueError("Invalid Familia feed cursor")
            rows = [
                r
                for r in rows
                if (r["no"] < number if condition == 0 else r["no"] > number)
            ][:limit]
        return (
            True,
            dict(
                code=0,
                result=0,
                messages=[
                    dict(
                        json.loads(r["content"]),
                        feedNo=r["no"],
                        userId=r["user"],
                        postedAt=r["created"],
                        expireAt=r["created"] + 30 * 86400,
                    )
                    for r in reversed(rows)
                ],
            ),
        )
    if own_role not in (1, 2):
        raise ValueError("Familia management permission required")
    if action == "familia/invite/search":
        rows = db.execute(
            "SELECT uid FROM players WHERE uid NOT IN (SELECT uid FROM members) AND json_extract(profile,'$.leaderPc.pcId')>0 ORDER BY uid"
        ).fetchall()
        limit = request.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid Familia candidate limit")
        return (
            True,
            dict(code=0, users=[world.profile(db, r["uid"]) for r in rows[:limit]]),
        )
    if action == "familia/invite/send":
        target = positive(request.get("userId"))
        world.profile(db, target)
        if db.execute("SELECT 1 FROM members WHERE uid=?", (target,)).fetchone():
            raise ValueError("Player already has a Familia")
        db.execute(
            "INSERT OR IGNORE INTO familia_invites VALUES(?,?,?,?)",
            (fid, target, world.uid, now),
        )
    elif action == "familia/kick":
        target = positive(request.get("userId"))
        target_member = db.execute(
            "SELECT familia FROM members WHERE uid=?", (target,)
        ).fetchone()
        if (
            not target_member
            or target_member["familia"] != fid
            or role(db, target, fid) <= own_role
        ):
            raise ValueError("Cannot remove this Familia member")
        db.execute("DELETE FROM members WHERE uid=?", (target,))
        db.execute("DELETE FROM member_roles WHERE uid=?", (target,))
    else:
        if own_role != 1:
            raise ValueError("Only the Familia master can perform this action")
        if action in (
            "familia/manage/appoint_submaster",
            "familia/manage/dismessed_submaster",
            "familia/manage/change_master",
        ):
            target = positive(
                request.get("dismissedUserId")
                if action.endswith("dismessed_submaster")
                else request.get("appointeeUserId")
            )
            if (
                target == world.uid
                or not db.execute(
                    "SELECT 1 FROM members WHERE uid=? AND familia=?", (target, fid)
                ).fetchone()
            ):
                raise ValueError("Appointee must be another Familia member")
            if action.endswith("change_master"):
                db.execute("UPDATE familias SET owner=? WHERE id=?", (target, fid))
                db.execute(
                    "DELETE FROM member_roles WHERE uid IN (?,?)", (target, world.uid)
                )
            elif action.endswith("appoint_submaster"):
                db.execute("INSERT OR REPLACE INTO member_roles VALUES(?,2)", (target,))
            else:
                db.execute("DELETE FROM member_roles WHERE uid=?", (target,))
        elif action == "familia/break":
            for table in (
                "familia_messages",
                "familia_invites",
                "join_requests",
                "familia_contributions",
            ):
                db.execute(f"DELETE FROM {table} WHERE familia=?", (fid,))
            db.execute(
                "DELETE FROM member_roles WHERE uid IN (SELECT uid FROM members WHERE familia=?)",
                (fid,),
            )
            db.execute("DELETE FROM members WHERE familia=?", (fid,))
            db.execute("DELETE FROM familias WHERE id=?", (fid,))
        elif action in ("familia/manage/change/info", "familia/manage/change/god"):
            info = json.loads(family["info"])
            for key in (
                "name",
                "description",
                "godId",
                "style",
                "subStyle",
                "activeTime",
                "subActiveTime",
                "requireApproval",
            ):
                if key not in request:
                    continue
                value = request[key]
                if key in ("name", "description"):
                    if (
                        not isinstance(value, str)
                        or len(value) > (32 if key == "name" else 300)
                        or (key == "name" and (not value.strip()))
                    ):
                        raise ValueError("Invalid Familia text")
                elif key == "requireApproval":
                    if type(value) is not bool:
                        raise ValueError("Invalid approval setting")
                elif type(value) is not int or value < 0:
                    raise ValueError("Invalid Familia setting")
                info[key] = value
            db.execute(
                "UPDATE familias SET name=?,info=? WHERE id=?",
                (info["name"], json.dumps(info), fid),
            )
        else:
            raise ValueError("Unsupported Familia management action")
    return (
        True,
        dict(
            code=0,
            result=0,
            joinableAt=0,
            data={},
            dataTokens={},
            operations=[],
            status=dict(
                familiaId=0 if action == "familia/break" else fid, recievedRequest=False
            ),
        ),
    )
