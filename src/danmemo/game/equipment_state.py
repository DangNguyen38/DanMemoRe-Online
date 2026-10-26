"""Repair invalid references left by the former equipment-ID snapshot key.

Native gear instances use slotIndex (lookup 0x2583748). Missing instances
cannot be reconstructed from their definition ID or rerolled abilities.
"""

import hashlib


def repair_links(oc):
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        slots = {r["slotIndex"] for r in state["tables"].get("UserEquipment", [])}
        changed = 0
        for pc in state["tables"].get("UserPC", []):
            for field in ("equippedWeaponIndex", "equippedArmorIndex"):
                index = pc.get(field, 0)
                if index and index not in slots:
                    state.setdefault("missingEquipmentLinks", {}).setdefault(
                        str(pc["pcId"]), {}
                    )[field] = index
                    pc[field] = 0
                    changed += 1
        if changed:
            state["tokens"]["UserPC"] = hashlib.md5(
                oc.msgpack_value(state["tables"]["UserPC"])
            ).hexdigest()
            oc.write_progress(state)
        return changed
