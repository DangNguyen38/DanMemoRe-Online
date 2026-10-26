"""Adapt the native Scout clock to the private server's configured cadence.

Original login parser: 0x20770a0..0x207721c. Original Scout predicates:
0x232e6c4, 0x232e960 and 0x232ea24; message formatter: 0x232ef68.
The daily cutoff is the battle-start clock, not a new battle/roster mutation.
"""

import time
from danmemo.config import modes as modes


def selected(settings, now):
    for key in ("familia_war_game", "master_familia_war_game"):
        period = settings.get(key)
        if (
            period
            and (not period.get("closed"))
            and (period.get("lifecycle") == "original-contract-v1")
            and (period.get("roundPreparationSeconds", 0) > 0)
            and (period["start"] <= now < period["end"])
        ):
            return (key, period)
    return None


def login_fields(oc):
    world = getattr(oc, "ONLINE_SHARED", None)
    server = "online" if world is not None else "offline"
    choice = selected(modes.config(server)["modes"], int(time.time()))
    if choice is None:
        return {}
    key, period = choice
    cutoff = int(period.get("battleStart", period["start"]))
    if world is not None:
        from danmemo.game import familia_war_lifecycle as life

        with world.connect() as db:
            state = life.window(db, modes.selected_season(key, period))
            if state and state.get("cadence"):
                cutoff = state["cadence"]["anchor_start"]
    return dict(gvgScoutExpiredTime=cutoff, gvgScoutOwnTeamExpiredTime=cutoff)


def messages(settings, now):
    choice = selected(settings, now)
    if choice is None:
        return {}
    key, period = choice
    prep = period["roundPreparationSeconds"] / 3600
    battle = period.get("roundBattleSeconds", 86400) / 3600
    text = f"Each round has {prep:g} hours of Prep followed by {battle:g} hours of Battle.\nDuring Prep, you can scout your enemy's and your ally's parties after matching.\nScout closes when Battle begins at {{2}}:{{3:02d}} (game time).\nCheck the phase countdown for the time remaining."
    return {40590171: text, 40590172: text} if key == "familia_war_game" else {}
