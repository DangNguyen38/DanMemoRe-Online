"""Recover nonempty milestone pools from archived lotteryStep rows.

Filtering field 4 to value 3 lost the explicitly stored original groups
on rows with values 7 and 9. Zero-group rows do not
declare a replacement pool and retain the banner/step-up fixed pool.
"""

from danmemo import paths as _paths
import functools
import gzip
import hashlib
import json
from danmemo.master import reader as master


@functools.cache
def catalog():
    cached = _paths.METADATA / "offline_lottery_guarantees_catalog.json.gz"
    if cached.exists():
        saved = json.loads(gzip.decompress(cached.read_bytes()))
        if saved.get("sources") == source_hashes():
            return saved["banners"]
    return recover_catalog()


def source_hashes():
    return {
        name: hashlib.sha256(
            (
                master.ROOT / "contents/1/master/divide_enc" / (name + ".enc")
            ).read_bytes()
        ).hexdigest()
        for name in ("lotteryWeightGroup", "lotteryCharacter", "lotteryStep")
    }


def recover_catalog():
    weights = {
        r.number(0): r.number(1)
        for r in master.rows(master.master_bytes("lotteryWeightGroup"))
    }
    groups = {}
    for r in master.rows(master.master_bytes("lotteryCharacter")):
        groups.setdefault(r.number(1), {}).setdefault(r.number(3), []).append(
            r.number(2)
        )
    result = {}
    for r in master.rows(master.master_bytes("lotteryStep")):
        group = groups.get(r.number(3), {})
        if not group or any((w not in weights for w in group)):
            continue
        result.setdefault(str(r.number(1)), {})[str(r.number(2))] = [
            [weights[w], ids] for w, ids in group.items()
        ]
    return result


def apply(data):
    for lid, milestones in catalog().items():
        banner = data["lotteries"].get(lid)
        if banner is None:
            continue
        for count, pool in milestones.items():
            if any((str(pid) not in data["pcs"] for _, ids in pool for pid in ids)):
                raise ValueError(
                    f"Missing character in original guarantee pool: {lid}/{count}"
                )
            banner.setdefault("guarantees", {})[count] = pool
    return data
