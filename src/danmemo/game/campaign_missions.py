"""Link original campaign missions without substituting another season's IDs."""

from danmemo.master import reader as master


def periods(settings, rules):
    from danmemo.game.reward_delivery import num as num

    result = {}
    vote = settings.get("character_vote")
    if vote:
        from danmemo.game.campaign_features import catalog as catalog
        from danmemo.config.modes import selected_season as selected_season

        event = next(
            (r for r in catalog() if r["id"] == selected_season("character_vote", vote))
        )
        labels = {
            r.text(1)
            for r in master.rows(master.master_bytes("voteItem"))
            if r.number(0) == event["item"]
        }
        for rule in rules:
            if num(rule, 2) == 3 and (num(rule, 4) == 1101 or rule.get("16") in labels):
                if (
                    event["start"] < (num(rule, 14) or 2147483647)
                    and num(rule, 13) < event["end"]
                ):
                    result["__campaign_achievement__:" + str(num(rule, 0))] = vote
    return result
