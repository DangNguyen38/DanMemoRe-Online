"""Cache actual archive checksums; original manifests include stale hashes."""

from danmemo.storage.atomic import write_json as atomic_write_json

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from danmemo.game import engine as oc


def main():
    cached = oc.archive_checksums().copy()
    files = []
    for phase in "12345678":
        manifest = json.loads(
            (oc.CONTENTS / phase / "project.manifest").read_text("utf-8")
        )
        files.extend(
            (
                (phase + "/" + relative, oc.CONTENTS / phase / relative)
                for relative in manifest["assets"]
            )
        )

    def check(pair):
        key, path = pair
        if not path.is_file():
            return (key, None)
        stat = path.stat()
        if key in cached and cached[key][:2] == [stat.st_size, stat.st_mtime_ns]:
            return (key, cached[key])
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "md5").hexdigest()
        return (key, [stat.st_size, stat.st_mtime_ns, digest])

    with ThreadPoolExecutor(max_workers=12) as pool:
        for index, (key, value) in enumerate(pool.map(check, files), 1):
            if value:
                cached[key] = value
            if index % 5000 == 0:
                print(f"Checked {index}/{len(files)} assets", flush=True)
    atomic_write_json(oc.WORK / "archive-checksums.json", cached)
    print(f"Cached {len(cached)} archive checksums", flush=True)


if __name__ == "__main__":
    main()
