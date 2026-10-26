"""Separate immutable inputs, server state and generated output."""

import os
from pathlib import Path

_source_root = Path(__file__).resolve().parents[2]
ROOT = Path(
    os.environ.get(
        "DANMEMO_ROOT",
        str(
            _source_root if (_source_root / "pyproject.toml").is_file() else Path.cwd()
        ),
    )
).resolve()
_data = Path(__file__).resolve().parent / "_data"


def path(name, default):
    return Path(os.environ.get(name, str(default))).expanduser().resolve()


ASSETS = path("DANMEMO_ASSETS", ROOT / "static/bundles")
CLIENT_APK = path("DANMEMO_CLIENT_APK", ROOT / "static/client/config.arm64_v8a.apk")
STATE = path("DANMEMO_STATE", ROOT / "var")
OUTPUT = path("DANMEMO_OUTPUT", ROOT / "output")
METADATA = (
    ROOT / "master/metadata"
    if (ROOT / "master/metadata").is_dir()
    else _data / "master/metadata"
)
MIGRATIONS = (
    ROOT / "migrations" if (ROOT / "migrations").is_dir() else _data / "migrations"
)
SCHEDULE = path("DANMEMO_SCHEDULE", ROOT / "config/schedule.json")
