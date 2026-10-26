"""Native selectedPcId1/2/3 validation and reconstructed chosen ticket pools."""


def selected_pools(banner, request):
    selections = banner.get("selection", [])
    if not selections:
        return (banner["pools"]["7"], banner["pools"].get("25", []), {})
    groups = {}
    receipt = {}
    chosen = set()
    for slot in selections:
        key = f"selectedPcId{slot['slot']}"
        pid = request.get(key)
        if type(pid) is not int or pid not in slot["pcs"] or pid in chosen:
            raise ValueError(
                "Select a valid distinct character for each ticket lineup slot"
            )
        chosen.add(pid)
        receipt[key] = pid
        weight, pcs = groups.setdefault(slot["weightGroupId"], (slot["weight"], []))
        pcs.append(pid)
    selected = [[weight, pcs] for weight, pcs in groups.values()]
    fixed = selected
    milestones = banner.get("selectionFixedDraws")
    if milestones is not None:
        count = request.get("progressCount")
        if type(count) is not int or count < 0:
            raise ValueError("Invalid selected-lineup draw progress")
        if count + 1 not in milestones:
            fixed = banner["baseFixedPool"]
    return (banner["primaryPool"] + selected, fixed, receipt)
