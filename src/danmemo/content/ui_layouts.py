"""Small documented layout overlays; retain the original archived assets."""

import json
import hashlib
from pathlib import Path

FAMILIA_NOTICE = "files/ui_atlas/home_familia/familia_member_auto_message.json"
POPULARITY_ROWS = tuple(
    (f"files/ui_atlas/home_arena/use_ranking_scroll_cell_{n:02}.json" for n in (1, 2))
)


def overlays(contents: Path, work: Path, phase: str):
    result = {}
    for index, relative in enumerate(POPULARITY_ROWS):
        source = contents / phase / relative
        if not source.is_file():
            continue
        layout = json.loads(source.read_text(encoding="utf-8"))
        root = layout["root"]
        slots = {node["tag"]: node for node in root["childs"]}
        if not all((tag in slots for tag in range(1, 6))):
            raise ValueError("Unexpected original popularity row slots")
        pitch = root["size"]["width"] / 5
        for tag in range(1, 6):
            node = slots[tag]
            if index == 1:
                node["size"] = dict(width=181, height=226)
            node["position"]["x"] = pitch * (tag - 0.5) - node["size"]["width"] / 2
        if index == 1:
            root["size"]["height"] = 242
            if 6 in slots:
                slots[6]["position"]["x"] = root["size"]["width"] + 10
        encoded = json.dumps(layout, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        digest = hashlib.sha256(encoded).hexdigest()[:16]
        output = work / "ui-layout-overlays" / phase / digest / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.exists():
            output.write_bytes(encoded)
        result[relative] = output
    source = contents / phase / FAMILIA_NOTICE
    if not source.is_file():
        return result
    layout = json.loads(source.read_text(encoding="utf-8"))
    labels = {child["tag"]: child for child in layout["root"]["childs"]}
    labels[1]["dimension"] = dict(width=421, height=60)
    labels[2]["position"] = dict(x=449, y=67)
    labels[2]["dimension"] = dict(width=166, height=60)
    labels[2]["font_size"] = labels[1]["font_size"]
    output = work / "ui-layout-overlays" / phase / FAMILIA_NOTICE
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(layout, ensure_ascii=False, separators=(",", ":"))
    if not output.is_file() or output.read_text(encoding="utf-8") != encoded:
        output.write_text(encoded, encoding="utf-8")
    result[FAMILIA_NOTICE] = output
    return result
