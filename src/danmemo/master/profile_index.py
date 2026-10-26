"""Checksum-validated cache of immutable profile lookups from archived masters."""

from danmemo import paths as _paths
import functools
import gzip
import hashlib
import json

ROOT = _paths.ROOT
CATALOG = _paths.METADATA / "offline_profile_catalog.json.gz"
NAMES = ("pcJob", "pc", "characterCostume", "communicationCharacter", "pcLevelStep")


def fingerprints():
    return {
        name: hashlib.sha256(
            (
                _paths.ASSETS / "1" / "master" / "divide_enc" / (name + ".enc")
            ).read_bytes()
        ).hexdigest()
        for name in NAMES
    }


@functools.cache
def load():
    try:
        data = json.loads(gzip.decompress(CATALOG.read_bytes()))
        if data.get("schema") != 2 or data["sources"] != fingerprints():
            return None
        return {
            name: {int(key): value for key, value in data[name].items()}
            for name in (
                "job_ranks",
                "initial_jobs",
                "valid_costumes",
                "default_costumes",
                "pc_level_groups",
                "pc_exp_curves",
            )
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None
