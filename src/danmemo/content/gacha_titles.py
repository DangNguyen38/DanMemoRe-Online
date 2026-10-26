"""Evidence-backed display corrections; archived master bytes remain intact."""

from danmemo import paths as _paths
import functools
import json


@functools.cache
def catalog():
    return json.loads(
        (_paths.METADATA / "gacha_title_corrections.json").read_text("utf-8")
    )


def display_title(lottery_id, original):
    row = catalog().get(str(lottery_id))
    return row["displayTitle"] if row and row["masterTitle"] == original else original
