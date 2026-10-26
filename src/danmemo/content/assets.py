"""Resolve original archived artwork without substituting unrelated images."""

from danmemo import paths as _paths
from pathlib import Path, PurePosixPath

ROOT = _paths.ROOT


def original_path(relative, contents=None):
    parts = PurePosixPath(relative).parts
    if not parts or parts[0] != "files" or any((p in (".", "..") for p in parts)):
        return None
    root = (Path(contents) if contents is not None else _paths.ASSETS).resolve()
    for phase in "31245678":
        candidate = root / phase / Path(*parts)
        if candidate.is_file():
            resolved = candidate.resolve()
            if root in resolved.parents:
                return resolved
    return None


def original_url(relative):
    path = original_path(relative)
    return "/content/" + path.relative_to(_paths.ASSETS).as_posix() if path else None
