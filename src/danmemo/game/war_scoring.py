"""Master-backed WG score reconstruction, not recovered publisher backend code.

Original help proves score-gap variation, grade-group floors and previous-season
win bonuses. The association of score-condition kind 3 with (rival-own) and
grade slot 11 with the base loss is reconstructed from the archived tables.
See README.md for scope and limitations. for limits/evidence.
"""

from functools import cache
from danmemo.master import reader as master

POLICY = "master-wg-v1"


def signed(row, field):
    value = row.number(field)
    return value - 4294967296 if value >= 2147483648 else value


@cache
def tables():
    return {
        name: tuple(master.rows(master.master_bytes(name)))
        for name in (
            "pvpGrade",
            "pvpScoreCondition",
            "pvpHighClassPass",
            "pvpInformation",
        )
    }


def previous_grade(state, identifier):
    info = next((r for r in tables()["pvpInformation"] if r.number(0, 8) == identifier))
    previous = info.number(13, 8)
    record = next(
        (
            r
            for r in state["tables"].get("UserPvPRecord", [])
            if r.get("pvpId") == previous
        ),
        {},
    )
    gid = record.get("gradeId", 0)
    return (
        gid
        if any(
            (
                r.number(0, 8) == gid and r.number(1, 8) == previous
                for r in tables()["pvpGrade"]
            )
        )
        else 0
    )


def calculate(
    identifier, *, score, rival_score, grade_id, won, wins_before=0, previous_grade_id=0
):
    grades = [r for r in tables()["pvpGrade"] if r.number(1, 8) == identifier]
    grade = next((r for r in grades if r.number(0, 8) == grade_id))
    gap = rival_score - score
    conditions = [
        r
        for r in tables()["pvpScoreCondition"]
        if r.number(1, 8) == identifier
        and r.number(2) == 3
        and (signed(r, 3) <= gap <= signed(r, 4))
    ]
    if len(conditions) != 1:
        raise ValueError("Missing or ambiguous original War Game score band")
    condition = conditions[0]
    base = (
        signed(condition, 5) + signed(grade, 10)
        if won
        else signed(condition, 6) + signed(grade, 11)
    )
    bonus = 0
    if won:
        award = next(
            (
                r
                for r in tables()["pvpHighClassPass"]
                if r.number(1, 8) == identifier and r.number(2, 8) == previous_grade_id
            ),
            None,
        )
        if award is not None and wins_before < award.number(4):
            bonus = award.number(3)
    floor = min((r.number(7) for r in grades if r.number(4) == grade.number(4)))
    updated = max(min(score, floor), score + base + bonus)
    return dict(
        policy=POLICY,
        score=updated,
        difference=updated - score,
        base=base,
        bonus=bonus,
        floor=floor,
        rivalScore=rival_score,
        previousGradeId=previous_grade_id,
    )
