"""Local account registry; content and APKs remain shared between accounts."""

import json
from pathlib import Path
import tempfile


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp"
    ) as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        temporary = Path(output.name)
    temporary.replace(path)
