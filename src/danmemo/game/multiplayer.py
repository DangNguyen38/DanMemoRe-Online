"""Shared asynchronous test gameplay, using recovered native wire shapes.

Combat remains client simulated. This is a reconstruction, not original server
source or authoritative anti-cheat. Only actual published player parties match.
"""

import copy
import functools
from collections import Counter
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from danmemo.game import arena as arena
from danmemo.game import familia as familia_features
from danmemo.game import familia_economy as familia_economy
from danmemo.game import familia_raid as familia_raid
from danmemo.game import familia_war as familia_war
from danmemo.game import extra_modes as extra_modes
from danmemo.game import union_raid as union_raid
from danmemo.game import casino_lotto as casino_lotto
from danmemo.game import campaign_features as campaign_features
from danmemo.master import reader as master


@functools.cache
def profile_job_ranks():
    from danmemo.master.profile_index import load as load

    cached = load()
    if cached is not None:
        return cached["job_ranks"]
    return {r.number(0): r.number(4) for r in master.rows(master.master_bytes("pcJob"))}


@functools.cache
def profile_initial_jobs():
    from danmemo.master.profile_index import load as load

    cached = load()
    if cached is not None:
        return cached["initial_jobs"]
    return {r.number(0): r.number(14) for r in master.rows(master.master_bytes("pc"))}


@functools.cache
def profile_costumes():
    from danmemo.master.profile_index import load as load

    cached = load()
    if cached is not None:
        return (cached["valid_costumes"], cached["default_costumes"])
    costumes = master.rows(master.master_bytes("characterCostume"))
    valid = {r.number(0): r.text(1) for r in costumes}
    defaults = {}
    for character in master.rows(master.master_bytes("communicationCharacter")):
        candidates = [
            r for r in costumes if r.text(1) == character.text(2) and r.number(8)
        ]
        if candidates:
            defaults[character.number(0)] = min(
                candidates, key=lambda r: r.number(9)
            ).number(0)
    return (valid, defaults)


def profile_costume(favorite, character):
    valid, defaults = profile_costumes()
    default = defaults.get(favorite)
    if default is None:
        raise ValueError("Favorite character has no archived default costume")
    saved = character.get("costumeId", 0)
    return saved if valid.get(saved) == valid[default] else default


def profile_job_rank(pc, leader):
    current = pc.get("currentJobIndex", 1)
    slot = next(
        (s for s in pc.get("jobSlots", []) if s.get("jobSlotNumber") == current), {}
    )
    job_id = slot.get("pcJobId", profile_initial_jobs().get(leader, 0))
    return profile_job_ranks().get(job_id, 0)


@functools.cache
def profile_pc_levels():
    from danmemo.master.profile_index import load as load

    cached = load()
    if cached is not None:
        return (cached["pc_level_groups"], cached["pc_exp_curves"])
    groups = {r.number(0): r.number(47) for r in master.rows(master.master_bytes("pc"))}
    curves = {}
    for row in master.rows(master.master_bytes("pcLevelStep")):
        curves.setdefault(row.number(1), []).append((row.number(3), row.number(2)))
    return (groups, {group: sorted(rows) for group, rows in curves.items()})


def profile_pc_level(pc, leader):
    groups, curves = profile_pc_levels()
    curve = curves.get(groups.get(leader))
    if not curve:
        return pc.get("level", 1)
    exp = pc.get("totalExp", 0)
    return next((level for threshold, level in reversed(curve) if threshold <= exp), 1)


ROUTES = {
    "pvb/player_info/get",
    "friend/get",
    "friend/follow",
    "friend/unfollow",
    "friend/following/list",
    "friend/follower/list",
    "friend/support/list",
    "pvp/rival_list/update",
    "pvp/player_info/get",
    "pvp/friend/ranking/get",
    "serial_pvp/player_info/get",
    "serial_pvp/ranking/get",
    "familia/mystatus",
    "familia/create",
    "familia/get",
    "familia/search/random",
    "familia/search/name",
    "familia/search/option",
    "familia/join/send",
    "familia/join/cancel",
    "familia/join/list/sent",
    "familia/join/list/recieved",
    "familia/join/approve",
    "familia/leave",
    "familia/invited/list",
    "pvb/character/ranking/get",
    "pvp/character/ranking/get",
    "serial_pvp/character/ranking/get",
}
ROUTES.update(familia_features.ROUTES)
ROUTES.update(familia_economy.ROUTES)
ROUTES.update(familia_raid.ROUTES)
ROUTES.update(familia_war.ROUTES)
ROUTES.update(extra_modes.ROUTES)
ROUTES.update(union_raid.ROUTES)
ROUTES.add("achievement/familia/get")
ROUTES.update(casino_lotto.ROUTES)
ROUTES.update(campaign_features.ROUTES)


class SharedWorld:
    def __init__(self, path, uid):
        self.path, self.uid = (Path(path), int(uid))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                "\n                CREATE TABLE IF NOT EXISTS players(uid INTEGER PRIMARY KEY, profile TEXT NOT NULL,\n                    party TEXT, updated INTEGER NOT NULL);\n                CREATE TABLE IF NOT EXISTS follows(actor INTEGER NOT NULL, target INTEGER NOT NULL,\n                    created INTEGER NOT NULL, PRIMARY KEY(actor,target));\n                CREATE TABLE IF NOT EXISTS scores(kind TEXT NOT NULL, season INTEGER NOT NULL,\n                    uid INTEGER NOT NULL, score INTEGER NOT NULL, detail TEXT NOT NULL,\n                    PRIMARY KEY(kind,season,uid));\n                CREATE TABLE IF NOT EXISTS familias(id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    owner INTEGER NOT NULL, name TEXT UNIQUE NOT NULL, info TEXT NOT NULL);\n                CREATE TABLE IF NOT EXISTS members(uid INTEGER PRIMARY KEY, familia INTEGER NOT NULL,\n                    joined INTEGER NOT NULL);\n                CREATE TABLE IF NOT EXISTS join_requests(uid INTEGER PRIMARY KEY,\n                    familia INTEGER NOT NULL, created INTEGER NOT NULL);\n                CREATE TABLE IF NOT EXISTS unit_usage(kind TEXT NOT NULL, season INTEGER NOT NULL,\n                    uid INTEGER NOT NULL, party TEXT NOT NULL, PRIMARY KEY(kind,season,uid));\n                CREATE TABLE IF NOT EXISTS supporters(uid INTEGER PRIMARY KEY, leader TEXT NOT NULL);\n                CREATE TABLE IF NOT EXISTS player_profile_details(uid INTEGER PRIMARY KEY,\n                    detail TEXT NOT NULL);\n            "
            )
            db.executescript(familia_features.SCHEMA)
            db.executescript(familia_economy.SCHEMA)
            db.executescript(familia_raid.SCHEMA)
            db.executescript(familia_war.SCHEMA)
            db.executescript(extra_modes.SCHEMA)
            db.executescript(union_raid.SCHEMA)
            db.executescript(campaign_features.SCHEMA)
            db.executescript(
                "\n                CREATE INDEX IF NOT EXISTS economy_pending ON familia_economy_receipts(uid,applied);\n                CREATE INDEX IF NOT EXISTS raid_pending ON familia_raid_attempts(uid,applied);\n                CREATE INDEX IF NOT EXISTS union_pending ON union_battle_receipts(uid,applied);\n                CREATE INDEX IF NOT EXISTS war_mission_counts ON familia_war_battles(actor,season,finished,started);\n                CREATE INDEX IF NOT EXISTS royale_mission_scores ON royale_scores(familia,season);\n            "
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def publish(self, state, request=None):
        tables = state["tables"]
        info = next(iter(tables.get("UserInfo", [])), {})
        pcs = {p["pcId"]: p for p in tables.get("UserPC", [])}
        active = info.get("activePartyId", 1) or 1
        party_row = next(
            (p for p in tables.get("UserParty", []) if p.get("id") == active), {}
        )
        leader = next(
            (s.get("pcId") for s in party_row.get("slots", []) if s.get("pcId")), 0
        )
        pc = pcs.get(leader, {})
        profile = dict(
            userId=self.uid,
            name=info.get("name", f"Player {self.uid}"),
            description=next(iter(tables.get("UserProfile", [])), {}).get(
                "introduction", ""
            ),
            rank=info.get("playerStatus", {}).get("level", 1),
            totalExp=info.get("playerStatus", {}).get("totalExp", 0),
            lastLoginedAt=info.get("lastLoginedDateAt", 0),
            leaderPc=dict(
                pcId=leader,
                level=profile_pc_level(pc, leader),
                jobRank=profile_job_rank(pc, leader),
                heroSublimateLevel=pc.get("heroSublimateLevel", 0),
                isUnlockedHeroSublimate=pc.get("isUnlockedHeroSublimate", False),
            ),
        )
        settings = next(iter(tables.get("UserProfile", [])), {})
        profile["totalStatusPoint"] = settings.get("totalStatusPoint", 0)
        favorite = settings.get("favoriteCommunicationCharacterId", 100000001)
        character = next(
            (
                r
                for r in tables.get("UserCommunicationCharacter", [])
                if r.get("characterId") == favorite
            ),
            {},
        )
        affinity = next(
            (
                r
                for r in tables.get("UserCharacter", [])
                if r.get("characterId") == favorite
            ),
            {},
        )
        hero_type = info.get("heroType", 0)
        cp_field = (
            ("friendship", "friendshipAis", "friendshipLef")[hero_type]
            if hero_type in (0, 1, 2)
            else "friendship"
        )
        detail = dict(
            playerInfo=dict(
                userId=self.uid,
                name=profile["name"],
                description=profile["description"],
                favoriteCharacterId=favorite,
                favoriteCharacterFriendship=affinity.get(cp_field, 0),
                favoriteCharacterCostumeId=profile_costume(favorite, character),
                totalExp=profile["totalExp"],
                lastLoginedAt=profile["lastLoginedAt"],
                heroType=info.get("heroType", 0),
                totalStatusPoint=settings.get("totalStatusPoint", 0),
                dispFlag=settings.get("dispFlag", False),
                exploitIds=[],
                titleBadgeId=info.get("titleBadgeId", 0),
                subTitleBadgeId=info.get("subTitleBadgeId", 0),
                familiaName=settings.get("familiaName", ""),
            ),
            pvpRecord=copy.deepcopy(tables.get("UserPvPRecord", [])),
            familiaName=settings.get("familiaName", ""),
            substitutedSpineIds=[],
        )
        adventure_level = next(iter(tables.get("UserAdventureLevelInfo", [])), None)
        if adventure_level is not None:
            detail["userAdventureLevelInfo"] = copy.deepcopy(adventure_level)
        candidates = [request.get("userParty")] if isinstance(request, dict) else []
        candidates += tables.get("UserPvPParty", [])
        defense = None
        for party in candidates:
            if (
                isinstance(party, dict)
                and party.get("userId") == self.uid
                and any(
                    ((s.get("pc") or {}).get("pcId") for s in party.get("slots", []))
                )
            ):
                defense = arena.native_party(party)
                break
        with self.connect() as db:
            db.execute(
                "INSERT INTO players VALUES(?,?,?,?) ON CONFLICT(uid) DO UPDATE SET\n                profile=excluded.profile, party=COALESCE(excluded.party,players.party), updated=excluded.updated\n                WHERE players.profile<>excluded.profile OR players.updated<>excluded.updated\n                   OR (excluded.party IS NOT NULL AND players.party IS NOT excluded.party)",
                (
                    self.uid,
                    json.dumps(profile),
                    json.dumps(defense) if defense else None,
                    int(time.time()),
                ),
            )
            db.execute(
                "INSERT INTO player_profile_details VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET detail=excluded.detail WHERE detail<>excluded.detail",
                (self.uid, json.dumps(detail)),
            )
            support = self.support_snapshot(tables, party_row)
            if support:
                db.execute(
                    "INSERT INTO supporters VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET leader=excluded.leader WHERE leader<>excluded.leader",
                    (self.uid, json.dumps(support)),
                )
            else:
                db.execute("DELETE FROM supporters WHERE uid=?", (self.uid,))
            settings = None
            for kind, table, key in [
                ("pvb", "UserPvBRecord", "pvbId"),
                ("pvp", "UserPvPRecord", "pvpId"),
                ("serial", "UserSerialPvpRecord", "serialPvpId"),
                ("under", "UserUnderPvPRecord", "underPvpId"),
            ]:
                for row in tables.get(table, []):
                    from danmemo.config import modes as admin_game_modes

                    mode = {
                        "pvp": "war_game",
                        "pvb": "record_buster",
                        "serial": "ultimate_war_game",
                        "under": "telskyura",
                    }[kind]
                    if settings is None:
                        settings = admin_game_modes.config("online")["modes"]
                    period = settings.get(mode)
                    target = (
                        arena.season("pvbInformation", row[key]).number(2, 8)
                        if kind == "pvb"
                        else row[key]
                    )
                    if (
                        period
                        and target == admin_game_modes.selected_season(mode, period)
                        and (time.time() >= period["end"])
                    ):
                        continue
                    score = row.get("scoreInt64", row.get("score", 0))
                    if type(score) is not int or score < 0:
                        continue
                    detail = copy.deepcopy(row)
                    saved = state.get("arena", {}).get(kind, {}).get(str(row[key]), {})
                    used_under = saved.get("lastParty") if kind == "under" else None
                    under_leader = next(
                        (
                            (s.get("pc") or {}).get("pcId", 0)
                            for s in (used_under or {}).get("slots", [])
                            if (s.get("pc") or {}).get("pcId")
                        ),
                        leader,
                    )
                    detail.update(
                        pcId=under_leader
                        if kind == "under"
                        else saved.get("pcId", leader),
                        difficulty=saved.get("difficulty", 0),
                    )
                    db.execute(
                        "INSERT INTO scores VALUES(?,?,?,?,?) ON CONFLICT(kind,season,uid)\n                        DO UPDATE SET score=excluded.score, detail=excluded.detail\n                        WHERE scores.score<>excluded.score OR scores.detail<>excluded.detail",
                        (kind, row[key], self.uid, score, json.dumps(detail)),
                    )
                    party_table = {
                        "pvb": "UserPvBParty",
                        "pvp": "UserPvPParty",
                        "serial": "UserSerialPvpParty",
                        "under": "UserUnderPvPParty",
                    }[kind]
                    used_party = (
                        used_under
                        if kind == "under"
                        else saved.get("lastParty")
                        if kind == "pvp"
                        else saved.get("bestParty")
                        if kind in ("pvb", "serial") and saved.get("bestParty")
                        else next(iter(tables.get(party_table, [])), None)
                    )
                    participated = (
                        row.get("winCount", 0) + row.get("loseCount", 0) > 0
                        if kind in ("pvp", "under")
                        else score > 0
                    )
                    if (
                        participated
                        and isinstance(used_party, dict)
                        and (used_party.get("userId") == self.uid)
                    ):
                        proven = saved.get("lastParty") or saved.get("bestParty")
                        if (
                            not proven
                            and db.execute(
                                "SELECT 1 FROM unit_usage WHERE kind=? AND season=? AND uid=?",
                                (kind, row[key], self.uid),
                            ).fetchone()
                        ):
                            continue
                        db.execute(
                            "INSERT INTO unit_usage VALUES(?,?,?,?)\n                            ON CONFLICT(kind,season,uid) DO UPDATE SET party=excluded.party\n                            WHERE unit_usage.party<>excluded.party",
                            (kind, row[key], self.uid, json.dumps(used_party)),
                        )
                    elif not participated:
                        db.execute(
                            "DELETE FROM unit_usage WHERE kind=? AND season=? AND uid=?",
                            (kind, row[key], self.uid),
                        )
            for stage_id, saved in state.get("arena", {}).get("pve", {}).items():
                entry = saved.get("best")
                if not isinstance(entry, dict) or entry.get("userId") != self.uid:
                    continue
                score = entry.get("score")
                if type(score) is not int or score < 0:
                    continue
                detail = copy.deepcopy(entry)
                detail["_recent"] = copy.deepcopy(saved.get("recent", [])[:3])
                db.execute(
                    "INSERT INTO scores VALUES(?,?,?,?,?) ON CONFLICT(kind,season,uid)\n                    DO UPDATE SET score=excluded.score, detail=excluded.detail\n                    WHERE scores.score<>excluded.score OR scores.detail<>excluded.detail",
                    ("pve", int(stage_id), self.uid, score, json.dumps(detail)),
                )

    @staticmethod
    def support_snapshot(tables, party):
        """Recovered wire fields; first active-party slot is a local selection policy."""
        pcs = {p["pcId"]: p for p in tables.get("UserPC", [])}
        jobs = {p["pcId"]: p for p in tables.get("UserPCJobSet", [])}
        slot = next(
            (
                s
                for s in party.get("slots", [])
                if s.get("pcId") in pcs
                and s.get("pcId") in jobs
                and (s.get("assistingPcId") in pcs)
                and (s.get("assistingPcId") in jobs)
            ),
            None,
        )
        if not slot:
            return None
        blank = party.get("supporterPartySlot", {})
        result = {
            key: slot.get(key, blank.get(key, 0))
            for key in (
                "friendship",
                "friendshipAis",
                "friendshipLef",
                "assistingFriendship",
                "assistingFriendshipAis",
                "assistingFriendshipLef",
            )
        }
        for field, key in (("userPc", "pcId"), ("assistingPc", "assistingPcId")):
            result[field] = copy.deepcopy(pcs[slot[key]])
            result["userPcJobSet" if field == "userPc" else "assistingPcJobSet"] = (
                copy.deepcopy(jobs[slot[key]])
            )
        for field, index in (
            ("weapon", "equippedWeaponIndex"),
            ("armor", "equippedArmorIndex"),
        ):
            equipment = next(
                (
                    e
                    for e in tables.get("UserEquipment", [])
                    if e.get("slotIndex") == result["userPc"].get(index)
                    and e.get("slotIndex")
                ),
                blank.get(field),
            )
            if not isinstance(equipment, dict):
                return None
            result[field] = copy.deepcopy(equipment)
        return result

    def unit_popularity(self, kind, season):
        """Current published season teams; original aggregation policy is unavailable."""
        counts = {"normal": Counter(), "assist": Counter()}
        with self.connect() as db:
            parties = db.execute(
                "SELECT party FROM unit_usage WHERE kind=? AND season=?", (kind, season)
            ).fetchall()
        for row in parties:
            slots = json.loads(row["party"]).get("slots", [])
            for role, key in (("normal", "pc"), ("assist", "assistingPc")):
                counts[role].update(
                    {
                        (slot.get(key) or {}).get("pcId")
                        for slot in slots
                        if (slot.get(key) or {}).get("pcId")
                    }
                )
        return {
            role: [
                dict(rank=n, pcId=pid)
                for n, (pid, _) in enumerate(
                    sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:10],
                    1,
                )
            ]
            for role, counter in counts.items()
        }

    def relation(self, db, target):
        following = bool(
            db.execute(
                "SELECT 1 FROM follows WHERE actor=? AND target=?", (self.uid, target)
            ).fetchone()
        )
        follower = bool(
            db.execute(
                "SELECT 1 FROM follows WHERE actor=? AND target=?", (target, self.uid)
            ).fetchone()
        )
        return int(following) + 2 * int(follower)

    def profile(self, db, uid):
        row = db.execute("SELECT profile FROM players WHERE uid=?", (uid,)).fetchone()
        if not row:
            raise ValueError("Player has not entered this shared server")
        profile = json.loads(row["profile"])
        profile.setdefault("totalStatusPoint", 0)
        if not profile.get("leaderPc", {}).get("pcId"):
            raise ValueError("Player profile has not initialized its leader")
        return dict(profile, relationship=self.relation(db, uid))

    def profile_detail(self, target):
        """Common native Profile payload, including Under/Abyss Overall links.

        Their UserProfileHelper callbacks parse playerInfo before the mode party;
        the smaller combat player-info object is not a substitute.
        """
        with self.connect() as db:
            saved = db.execute(
                "SELECT detail FROM player_profile_details WHERE uid=?", (target,)
            ).fetchone()
            if not saved:
                raise ValueError("Target must publish its detailed profile")
            detail = json.loads(saved["detail"])
            if (
                not isinstance(detail.get("playerInfo"), dict)
                or detail["playerInfo"].get("userId") != target
            ):
                raise ValueError("Published profile identity is invalid")
            member = db.execute(
                "SELECT f.name FROM members m JOIN familias f ON f.id=m.familia WHERE m.uid=?",
                (target,),
            ).fetchone()
            detail["playerInfo"]["familiaName"] = member["name"] if member else ""
            detail["familiaName"] = member["name"] if member else ""
            return detail

    def ranking(self, kind, season, friends=False):
        with self.connect() as db:
            rows = db.execute(
                "SELECT s.*,p.profile,u.party AS season_party\n                FROM scores s JOIN players p USING(uid)\n                LEFT JOIN unit_usage u ON u.uid=s.uid AND u.kind=s.kind AND u.season=s.season\n                WHERE s.kind=? AND s.season=? ORDER BY s.score DESC,s.uid",
                (kind, season),
            ).fetchall()
            result = []
            for rank, row in enumerate(rows, 1):
                if (
                    friends
                    and row["uid"] != self.uid
                    and (not self.relation(db, row["uid"]) & 1)
                ):
                    continue
                detail = json.loads(row["detail"])
                p = json.loads(row["profile"])
                value = dict(
                    userId=row["uid"],
                    name=p["name"],
                    pcId=detail.get("pcId", 0),
                    leaderPcId=p["leaderPc"]["pcId"],
                    gradeId=detail.get("gradeId", 0),
                    rank=rank,
                    score=row["score"],
                    difficulty=detail.get("difficulty", 0),
                    totalExp=p["totalExp"],
                )
                if kind == "serial":
                    value["party"] = (
                        json.loads(row["season_party"])
                        if row["season_party"]
                        else arena.native_party(dict(userId=row["uid"], slots=[]))
                    )
                result.append(value)
            return result

    def rank_for_score(self, kind, season, score):
        with self.connect() as db:
            ahead = db.execute(
                "SELECT COUNT(*) FROM scores WHERE kind=? AND season=?\n                AND uid<>? AND (score>? OR (score=? AND uid<?))",
                (kind, season, self.uid, score, score, self.uid),
            ).fetchone()[0]
        return ahead + 1

    def rivals(self, gid, serial=False, target=None, *, kind=None, season=None):
        with self.connect() as db:
            rows = (
                db.execute("SELECT * FROM players WHERE uid=?", (target,)).fetchall()
                if target is not None
                else db.execute(
                    "SELECT * FROM players WHERE uid<>? AND party IS NOT NULL ORDER BY uid",
                    (self.uid,),
                ).fetchall()
            )
            result = []
            for n, row in enumerate(rows):
                published = (
                    db.execute(
                        "SELECT party FROM unit_usage WHERE uid=? AND kind=? AND season=?",
                        (row["uid"], kind, season),
                    ).fetchone()
                    if kind == "under"
                    else None
                )
                party = (
                    json.loads(published["party"])
                    if published
                    else json.loads(row["party"])
                    if row["party"]
                    else arena.native_party(dict(userId=row["uid"], slots=[]))
                )
                p = (
                    self.profile(db, row["uid"])
                    if target is not None
                    else json.loads(row["profile"])
                )
                leader = next(
                    (
                        (s.get("pc") or {}).get("pcId", 0)
                        for s in party.get("slots", [])
                        if (s.get("pc") or {}).get("pcId")
                    ),
                    0,
                )
                if not leader and target is None:
                    continue
                info = dict(
                    userId=row["uid"],
                    name=p["name"],
                    totalExp=p["totalExp"],
                    gradeId=gid,
                    score=0,
                    rank=n + 1,
                    preRank=n + 1,
                    leaderPcId=leader,
                    titleBadgeId=0,
                    subTitleBadgeId=0,
                    isDummy=False,
                    friendRelationShip=self.relation(db, row["uid"]),
                )
                if serial:
                    party.update(
                        partyType=1,
                        index=n,
                        partyStrength=sum(
                            (
                                s["pc"]["physicalAttackPower"]
                                + s["pc"]["magicAttackPower"]
                                for s in party["slots"]
                            )
                        ),
                    )
                    for slot in party["slots"]:
                        slot.setdefault("commandFlow", "")
                result.append(
                    {
                        "serialPvpPlayerInfo" if serial else "pvpPlayerInfo": info,
                        "serialPvpParty" if serial else "pvpParty": party,
                    }
                )
            return result

    def war_rivals(self, season, target=None, *, recent=()):
        """Real current-season metadata; original matchmaking is unrecovered."""
        arena.season("pvpInformation", season)
        candidates = self.rivals(arena.grade(season), target=target)
        with self.connect() as db:
            scored = db.execute(
                "SELECT uid,score FROM scores WHERE kind='pvp' AND season=? ORDER BY score DESC,uid",
                (season,),
            ).fetchall()
        records = {
            row["uid"]: (row["score"], rank) for rank, row in enumerate(scored, 1)
        }
        participating = [
            r for r in candidates if r["pvpPlayerInfo"]["userId"] in records
        ]
        result = participating or candidates
        if target is None:
            real_unplayed = [
                r
                for r in candidates
                if r["pvpPlayerInfo"]["userId"] not in records
                and (
                    self.path.parent
                    / str(r["pvpPlayerInfo"]["userId"])
                    / "progress.json"
                ).is_file()
            ]
            if participating and (
                not any(
                    (r["pvpPlayerInfo"]["userId"] not in recent for r in participating)
                )
            ):
                result = participating + [
                    r
                    for r in real_unplayed
                    if r["pvpPlayerInfo"]["userId"] not in recent
                ]
            elif not participating and real_unplayed:
                result = real_unplayed
        for rival in result:
            info = rival["pvpPlayerInfo"]
            score, rank = records.get(info["userId"], (1000, 0))
            info.update(
                score=score,
                rank=rank,
                preRank=rank,
                gradeId=arena.grade_for_score(season, score, rank or None),
            )
        return result

    def dispatch(self, action, request, oc=None, receipt=None):
        if action not in ROUTES:
            return None
        if action in campaign_features.ROUTES:
            return campaign_features.dispatch(self, request, oc, receipt)
        if action in casino_lotto.ROUTES:
            return casino_lotto.dispatch(self, action, request, oc)
        if action in extra_modes.ROUTES:
            return extra_modes.dispatch(self, action, request, oc, receipt)
        if action in union_raid.ROUTES:
            return union_raid.dispatch(self, action, request, oc, receipt)
        if action == "achievement/familia/get":
            return self.familia_home_missions()
        if action in familia_economy.ROUTES:
            return familia_economy.dispatch(self, action, request, oc, receipt)
        if action in familia_raid.ROUTES:
            return familia_raid.dispatch(self, action, request, oc, receipt)
        if action in familia_war.ROUTES:
            return familia_war.dispatch(self, action, request, oc, receipt)
        if action.endswith("/character/ranking/get"):
            kind, key, master_name = {
                "serial_pvp": ("serial", "serialPvpId", "serialPvpInformation"),
                "pvp": ("pvp", "pvpId", "pvpInformation"),
                "pvb": ("pvb", "pvbId", "pvbInformation"),
            }[action.split("/")[0]]
            identifier = request.get(key)
            arena.season(master_name, identifier)
            return dict(code=0, **self.unit_popularity(kind, identifier))
        if action == "pvb/player_info/get":
            identifier, target = (request.get("pvbId"), request.get("targetUserId"))
            arena.season("pvbInformation", identifier)
            if type(target) is not int or target <= 0:
                raise ValueError("Invalid Record Buster profile target")
            with self.connect() as db:
                detail = db.execute(
                    "SELECT detail FROM player_profile_details WHERE uid=?", (target,)
                ).fetchone()
                scored = db.execute(
                    "SELECT detail FROM scores WHERE kind='pvb' AND season=? AND uid=?",
                    (identifier, target),
                ).fetchone()
                team = db.execute(
                    "SELECT party FROM unit_usage WHERE kind='pvb' AND season=? AND uid=?",
                    (identifier, target),
                ).fetchone()
                if not detail:
                    raise ValueError("Record Buster player has no published profile")
                info = json.loads(detail["detail"])["playerInfo"]
                member = db.execute(
                    "SELECT f.name FROM members m JOIN familias f ON f.id=m.familia WHERE m.uid=?",
                    (target,),
                ).fetchone()
                info["familiaName"] = member["name"] if member else ""
                record = json.loads(scored["detail"]) if scored else None
                if record:
                    record["userId"] = target
                return dict(
                    code=0,
                    playerInfo=info,
                    pvbParty=json.loads(team["party"]) if team else {},
                    pvbRecord=[record] if record else [],
                )
        with self.connect() as db:
            if action.startswith("friend/"):
                if action == "friend/support/list":
                    rows = db.execute(
                        "SELECT s.* FROM supporters s JOIN players p USING(uid) WHERE uid<>? AND json_extract(p.profile,'$.leaderPc.pcId')>0 ORDER BY uid",
                        (self.uid,),
                    ).fetchall()
                    return dict(
                        code=0,
                        is_fullset=True,
                        supporters=[
                            dict(
                                self.profile(db, r["uid"]),
                                leaderPc=json.loads(r["leader"]),
                            )
                            for r in rows
                        ],
                    )
                if action in ("friend/get", "friend/follow", "friend/unfollow"):
                    target = request.get("userId")
                    if type(target) is not int or target <= 0:
                        raise ValueError("Invalid other player")
                    if action in ("friend/get", "friend/follow") and (
                        not db.execute(
                            "SELECT 1 FROM players WHERE uid=?", (target,)
                        ).fetchone()
                    ):
                        return dict(code=0, result=10000003)
                    if target == self.uid and action != "friend/get":
                        if action == "friend/follow":
                            return dict(code=0, result=1)
                        raise ValueError("Invalid other player")
                    try:
                        profile = self.profile(db, target)
                    except ValueError:
                        return dict(code=0, result=10000003)
                    if action == "friend/follow":
                        db.execute(
                            "INSERT OR IGNORE INTO follows VALUES(?,?,?)",
                            (self.uid, target, int(time.time())),
                        )
                    elif action == "friend/unfollow":
                        db.execute(
                            "DELETE FROM follows WHERE actor=? AND target=?",
                            (self.uid, target),
                        )
                    profile["relationship"] = self.relation(db, target)
                    return dict(
                        code=0,
                        result=0,
                        user=profile,
                        followingCount=db.execute(
                            "SELECT COUNT(*) FROM follows WHERE actor=?", (self.uid,)
                        ).fetchone()[0],
                        followersCount=db.execute(
                            "SELECT COUNT(*) FROM follows WHERE target=?", (self.uid,)
                        ).fetchone()[0],
                    )
                page = request.get("page", 1)
                if type(page) is not int or page < 1:
                    raise ValueError("Invalid friend page")
                following = [
                    self.profile(db, r["target"])
                    for r in db.execute(
                        "SELECT f.target FROM follows f JOIN players p ON p.uid=f.target WHERE f.actor=? AND json_extract(p.profile,'$.leaderPc.pcId')>0 ORDER BY f.target",
                        (self.uid,),
                    )
                ]
                followers = [
                    dict(self.profile(db, r["actor"]), followedAt=r["created"])
                    for r in db.execute(
                        "SELECT f.actor,f.created FROM follows f JOIN players p ON p.uid=f.actor WHERE f.target=? AND json_extract(p.profile,'$.leaderPc.pcId')>0 ORDER BY f.actor",
                        (self.uid,),
                    )
                ]
                values = following if action.endswith("following/list") else followers
                return dict(
                    code=0,
                    followingCount=len(following),
                    followersCount=len(followers),
                    current=page,
                    pageCount=(len(values) + 19) // 20,
                    following=following[(page - 1) * 20 : page * 20],
                    followers=followers[(page - 1) * 20 : page * 20],
                    followerListLastViewedAt=int(time.time()),
                )
        if action.startswith("familia/"):
            return self.familia(action, request)
        serial = action.startswith("serial_pvp/")
        key = "serialPvpId" if serial else "pvpId"
        identifier = request.get(key)
        if identifier is not None:
            arena.season(
                "serialPvpInformation" if serial else "pvpInformation", identifier
            )
        gid = (
            next(iter(arena.indexed("serialPvpGrade")))
            if serial
            else arena.grade(identifier)
        )
        if action.endswith("ranking/get"):
            rows = self.ranking(
                "serial" if serial else "pvp", identifier, "/friend/" in action
            )
            if serial:
                return dict(
                    code=0,
                    result=0,
                    hasNextPage=False,
                    serialPvpTotalRanking=rows,
                    serialPvpUserRanking=rows,
                    serialPvpRanking=rows,
                )
            return dict(
                code=0,
                hasNextPage=False,
                **{"serialPvpRanking" if serial else "pvpFriendRanking": rows},
            )
        if action.endswith("player_info/get"):
            target = request.get("targetUserId", request.get("userId"))
            if (
                type(target) is not int
                or target <= 0
                or (
                    "targetUserId" in request
                    and "userId" in request
                    and (request["userId"] != target)
                )
            ):
                raise ValueError("Invalid profile target identity")
            rivals = (
                self.rivals(gid, True, target=target)
                if serial
                else self.war_rivals(identifier, target=target)
            )
            rival = next(
                (
                    r
                    for r in rivals
                    if r["serialPvpPlayerInfo" if serial else "pvpPlayerInfo"]["userId"]
                    == target
                ),
                None,
            )
            if rival is None:
                raise ValueError("Target has no published combat profile")
            response = dict(code=0, result=0, **rival)
            response.update(self.profile_detail(target))
            return response
        return dict(
            code=0,
            result=0,
            rivalList=self.rivals(gid, True) if serial else self.war_rivals(identifier),
        )

    def familia_home_missions(self):
        now = int(time.time())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS familia_login_days(\n                uid INTEGER NOT NULL, familia INTEGER NOT NULL, joined INTEGER NOT NULL,\n                day INTEGER NOT NULL, PRIMARY KEY(uid,familia,joined,day))"
            )
            member = db.execute(
                "SELECT * FROM members WHERE uid=?", (self.uid,)
            ).fetchone()
            records = {"3001": 0, "3101": 0, "3102": 0}
            if member:
                fid = member["familia"]
                db.execute(
                    "INSERT OR IGNORE INTO familia_login_days VALUES(?,?,?,?)",
                    (self.uid, fid, member["joined"], now // 86400),
                )
                days = db.execute(
                    "SELECT COUNT(*) FROM familia_login_days WHERE uid=? AND familia=? AND joined=?",
                    (self.uid, fid, member["joined"]),
                ).fetchone()[0]
                family = db.execute(
                    "SELECT info FROM familias WHERE id=?", (fid,)
                ).fetchone()
                info = json.loads(family["info"])
                count = db.execute(
                    "SELECT COUNT(*) FROM members WHERE familia=?", (fid,)
                ).fetchone()[0]
                info["maximumMembers"] = max(info.get("maximumMembers", 0), count)
                db.execute(
                    "UPDATE familias SET info=? WHERE id=?", (json.dumps(info), fid)
                )
                records = {
                    "3001": days,
                    "3101": info["maximumMembers"],
                    "3102": info.get("level", 1),
                }
            return dict(code=0, records=records, daily_achievements=[])

    def familia(self, action, request):
        now = int(time.time())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            member = db.execute(
                "SELECT * FROM members WHERE uid=?", (self.uid,)
            ).fetchone()
            handled, extra = familia_features.dispatch(
                self, db, action, request, member, now
            )
            if handled:
                return extra
            if action == "familia/create":
                if member:
                    raise ValueError("Already in a Familia")
                name = request.get("name")
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 32:
                    raise ValueError("Invalid Familia name")
                info = {
                    k: request.get(k, 0)
                    for k in (
                        "godId",
                        "style",
                        "subStyle",
                        "activeTime",
                        "subActiveTime",
                    )
                }
                info.update(
                    name=name,
                    description=str(request.get("description", ""))[:300],
                    requireApproval=bool(request.get("requireApproval", False)),
                    contributionPoint=0,
                    valis=0,
                    skillPoint=0,
                    displayApproved=False,
                    bgmId=0,
                    backgroundImageId=0,
                )
                fid = db.execute(
                    "INSERT INTO familias(owner,name,info) VALUES(?,?,?)",
                    (self.uid, name, json.dumps(info)),
                ).lastrowid
                db.execute("INSERT INTO members VALUES(?,?,?)", (self.uid, fid, now))
                member = db.execute(
                    "SELECT * FROM members WHERE uid=?", (self.uid,)
                ).fetchone()
            elif action == "familia/join/send":
                if member:
                    raise ValueError("Already in a Familia")
                fid = request.get("familiaId")
                row = db.execute("SELECT * FROM familias WHERE id=?", (fid,)).fetchone()
                if not row:
                    raise ValueError("Unknown Familia")
                if json.loads(row["info"])["requireApproval"]:
                    db.execute(
                        "INSERT OR REPLACE INTO join_requests VALUES(?,?,?)",
                        (self.uid, fid, now),
                    )
                else:
                    db.execute(
                        "INSERT INTO members VALUES(?,?,?)", (self.uid, fid, now)
                    )
                    db.execute("DELETE FROM join_requests WHERE uid=?", (self.uid,))
                    member = db.execute(
                        "SELECT * FROM members WHERE uid=?", (self.uid,)
                    ).fetchone()
            elif action == "familia/join/approve":
                pending = db.execute(
                    "SELECT * FROM join_requests WHERE uid=?", (request.get("userId"),)
                ).fetchone()
                if not pending or not member:
                    raise ValueError("No pending join request")
                family = db.execute(
                    "SELECT * FROM familias WHERE id=?", (pending["familia"],)
                ).fetchone()
                if family["owner"] != self.uid:
                    raise ValueError("Only the master can approve")
                if request.get("approve", True):
                    db.execute(
                        "INSERT INTO members VALUES(?,?,?)",
                        (pending["uid"], pending["familia"], now),
                    )
                db.execute("DELETE FROM join_requests WHERE uid=?", (pending["uid"],))
            elif action == "familia/join/cancel":
                db.execute("DELETE FROM join_requests WHERE uid=?", (self.uid,))
            elif action == "familia/leave":
                if member:
                    family = db.execute(
                        "SELECT * FROM familias WHERE id=?", (member["familia"],)
                    ).fetchone()
                    if family["owner"] == self.uid:
                        raise ValueError(
                            "Master must transfer leadership before leaving"
                        )
                    db.execute("DELETE FROM members WHERE uid=?", (self.uid,))
                    db.execute("DELETE FROM member_roles WHERE uid=?", (self.uid,))
                    member = None
            familias = []
            for row in db.execute("SELECT * FROM familias ORDER BY id"):
                info = dict(json.loads(row["info"]), id=row["id"])
                for field in (
                    "latestGuildRaidRank",
                    "latestGvgRank",
                    "latestGvgLeagueId",
                ):
                    info.setdefault(field, 0)
                info.setdefault("unionStatus", 0)
                count = db.execute(
                    "SELECT COUNT(*) FROM members WHERE familia=?", (row["id"],)
                ).fetchone()[0]
                if count > info.get("maximumMembers", 0):
                    info["maximumMembers"] = count
                    db.execute(
                        "UPDATE familias SET info=? WHERE id=?",
                        (json.dumps(info), row["id"]),
                    )
                familias.append(
                    dict(
                        familia=info,
                        master=self.profile(db, row["owner"]),
                        memberCount=count,
                    )
                )
            if action.startswith("familia/search/"):
                name = request.get("name", "")
                matches = [
                    f
                    for f in familias
                    if str(name).lower() in f["familia"]["name"].lower()
                ]
                if action == "familia/search/option":
                    if "style" in request:
                        style = request["style"]
                        if type(style) is not int or not 21000001 <= style <= 21000005:
                            raise ValueError("Invalid Familia search style")
                        matches = [
                            f
                            for f in matches
                            if style
                            in (f["familia"].get("style"), f["familia"].get("subStyle"))
                        ]
                    if "requireApproval" in request:
                        approval = request["requireApproval"]
                        if type(approval) is not bool:
                            raise ValueError("Invalid Familia search approval")
                        matches = [
                            f
                            for f in matches
                            if f["familia"].get("requireApproval") == approval
                        ]
                return dict(code=0, result=0, familias=matches, requests=[])
            if action == "familia/invited/list":
                invitations = db.execute(
                    "SELECT * FROM familia_invites WHERE target=? ORDER BY familia",
                    (self.uid,),
                ).fetchall()
                invited_ids = {r["familia"] for r in invitations}
                return dict(
                    code=0,
                    familias=[f for f in familias if f["familia"]["id"] in invited_ids],
                    requests=[
                        dict(
                            userId=self.uid,
                            familiaId=r["familia"],
                            createdAt=r["created"],
                        )
                        for r in invitations
                    ],
                )
            if action in ("familia/join/list/sent", "familia/join/list/recieved"):
                pending = (
                    db.execute(
                        "SELECT * FROM join_requests WHERE uid=?", (self.uid,)
                    ).fetchall()
                    if action.endswith("/sent")
                    else db.execute(
                        "SELECT * FROM join_requests WHERE familia=?",
                        (member["familia"] if member else 0,),
                    ).fetchall()
                )
                if action.endswith("/recieved"):
                    return dict(
                        code=0,
                        familias=[],
                        requests=[
                            dict(
                                self.profile(db, r["uid"]),
                                familiaId=r["familia"],
                                createdAt=r["created"],
                            )
                            for r in pending
                        ],
                    )
                pending_ids = {r["familia"] for r in pending}
                return dict(
                    code=0,
                    familias=[],
                    requests=[f for f in familias if f["familia"]["id"] in pending_ids],
                )
            fid = request.get("familiaId", member["familia"] if member else 0)
            family = next((f for f in familias if f["familia"]["id"] == fid), None)
            response = dict(
                code=0,
                result=0,
                status=dict(
                    familiaId=member["familia"] if member else 0,
                    recievedRequest=bool(
                        member
                        and db.execute(
                            "SELECT 1 FROM join_requests WHERE familia=?",
                            (member["familia"],),
                        ).fetchone()
                    ),
                ),
                data={},
                dataTokens={},
                operations=[],
            )
            if action == "familia/join/send":
                response["state"] = 1 if member else 0
            if family:
                response.update(family)
                response["status"].update(
                    displayApproved=bool(
                        family["familia"].get("displayApproved", False)
                    ),
                    joinedAt=member["joined"] if member else 0,
                    canLeave=family["master"]["userId"] != self.uid,
                    canUnion=False,
                    canReceiveFirstReward=False,
                )
                response["familia"].update(
                    joinedAt=member["joined"] if member else 0,
                    canLeave=family["master"]["userId"] != self.uid,
                    canUnion=False,
                    unionStatus=0,
                    unionCondition=0,
                    unionResult=0,
                    unionWithFamiliaName="",
                    unionRegisteredAt=0,
                    unionTimeDuring=0,
                    canReceiveFirstReward=False,
                )
                response["members"] = [
                    dict(
                        self.profile(db, r["uid"]),
                        role=familia_features.role(db, r["uid"], fid),
                        post={1: 3, 2: 2, 3: 1}[
                            familia_features.role(db, r["uid"], fid)
                        ],
                        joinedAt=r["joined"],
                    )
                    for r in db.execute(
                        "SELECT * FROM members WHERE familia=? ORDER BY uid", (fid,)
                    )
                ]
                response["sentRequest"] = bool(
                    db.execute(
                        "SELECT 1 FROM join_requests WHERE uid=? AND familia=?",
                        (self.uid, fid),
                    ).fetchone()
                )
                response["purchasedProduct"] = family["familia"].get(
                    "purchasedProduct", []
                )
                for player in response["members"]:
                    contribution = db.execute(
                        "SELECT points FROM familia_contributions WHERE familia=? AND uid=?",
                        (fid, player["userId"]),
                    ).fetchone()
                    player["contributionPoint"] = (
                        contribution["points"] if contribution else 0
                    )
            return response
