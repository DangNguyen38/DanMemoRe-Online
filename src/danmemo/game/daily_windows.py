"""Recurring LoginTimeBonus windows from the archived daily clock offsets.

Daily recurrence is reconstructed server policy; the two original windows,
IDs, objective and stamina rewards are retained. No reward is auto-claimed.
"""

import time


def mission_day(stamp):
    """Match the current region-5 client and observed native UTC+9 clock.

    Original 0x2a39600..0x2a39630 selects +32400 for region 5;
    the former PST8 implementation incorrectly selected the region-2 branch.
    MuMu globals 0x4330790/0x455d1e0 confirmed region 5/+32400.
    """
    return (int(stamp) + 32400) // 86400


def mission_day_start(stamp):
    return mission_day(stamp) * 86400 - 32400


def mission_month(stamp):
    return time.strftime("%Y-%m", time.gmtime(int(stamp) + 18000))


def mission_month_start(stamp):
    import calendar

    parts = time.gmtime(int(stamp) + 18000)
    return calendar.timegm((parts.tm_year, parts.tm_mon, 1, 0, 0, 0)) - 18000


def reset_mission_month(row, now, locked=False):
    if mission_month(row.get("lastResetAt", 0)) == mission_month(now):
        return False
    completed = row.get("completedAt", 0)
    if (
        row.get("state") in (3, 4)
        and completed
        and (mission_month(completed) == mission_month(now))
    ):
        row["lastResetAt"] = mission_month_start(now)
    else:
        row.update(
            state=1 if locked else 2,
            completedAt=0,
            progressParam=0,
            lastResetAt=now,
            readState=1,
        )
    return True


def reset_mission_day(row, now):
    if mission_day(row.get("lastResetAt", 0)) == mission_day(now):
        return False
    completed = row.get("completedAt", 0)
    if (
        row.get("state") in (3, 4)
        and completed
        and (mission_day(completed) == mission_day(now))
    ):
        row["lastResetAt"] = mission_day_start(now)
    else:
        row.update(
            state=2, completedAt=0, progressParam=0, lastResetAt=now, readState=1
        )
    return True


def login_window(rule, now):
    from danmemo.game.reward_delivery import num as num

    start, end = (num(rule, 13), num(rule, 14))
    if not 0 < end - start < 86400:
        raise ValueError("Unsupported original daily login window")
    shift = ((int(now) - end) // 86400 + 1) * 86400
    return (start + shift, end + shift)


def is_login_bonus(rule):
    from danmemo.game.reward_delivery import num as num

    return num(rule, 2) == 1 and num(rule, 4) == 602 and (num(rule, 20) == 1)


def catalog_cycle(now=None):
    import time
    from danmemo.game.reward_delivery import decoded as decoded, num as num

    rule = next((r for r in decoded("achievement") if is_login_bonus(r)))
    return (int(time.time() if now is None else now) - num(rule, 14)) // 86400
