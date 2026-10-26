"""Restore missing own-party snapshots from accepted native start requests.

The old generic save key collapsed UserOtherParty rows to userId. Never replace
an existing typed party or infer a lineup from another game mode.
"""

import copy
import hashlib


def recover_missing(oc):
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        parties = state["tables"].get("UserOtherParty", [])
        known = {(p.get("userId"), p.get("partyType"), p.get("index")) for p in parties}
        recovered = {}
        for mode, party_type, table, season_key in (
            ("serial", 9, "UserSerialPvpGameStatus", "serialPvpId"),
            ("under", 7, "UserUnderPvPGameStatus", "underPvpId"),
        ):
            stamps = {
                str(r.get(season_key)): r.get("lastBattleStartAt", 0)
                for r in state["tables"].get(table, [])
            }
            seasons = state.get("arena", {}).get(mode, {})
            for sid, saved in sorted(
                seasons.items(), key=lambda pair: stamps.get(pair[0], 0)
            ):
                requests = [
                    r.get("request", {})
                    for r in saved.get("startReceipts", {}).values()
                ]
                if saved.get("activeBattle"):
                    requests.append(saved["activeBattle"].get("request", {}))
                for request in requests:
                    party = request.get("userParty", {})
                    if (
                        not isinstance(party, dict)
                        or party.get("userId") != oc.LOCAL_USER_ID
                        or party.get("partyType") != party_type
                        or (type(party.get("index")) is not int)
                        or (party["index"] < 0)
                        or (not isinstance(party.get("slots"), list))
                    ):
                        continue
                    key = (oc.LOCAL_USER_ID, party_type, party["index"])
                    if key not in known:
                        recovered[key] = copy.deepcopy(party)
        if not recovered:
            return 0
        state["tables"]["UserOtherParty"] = parties + list(recovered.values())
        state["tokens"]["UserOtherParty"] = hashlib.md5(
            oc.msgpack_value(state["tables"]["UserOtherParty"])
        ).hexdigest()
        oc.write_progress(state)
        return len(recovered)
