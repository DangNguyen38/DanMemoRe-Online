"""Bounded snapshot/JSON-fragment cache; the ordinary JSON file stays authoritative.

No deferred saves or receipt pruning. Pickle is only a process-local clone of
JSON objects we generated; no pickle bytes are ever read from disk/network.
"""

import hashlib
import json
import os
from pathlib import Path
import pickle
import tempfile
import threading

MAX_CACHE_BYTES = 64 * 1024 * 1024


def version(path):
    stat = path.stat()
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class SnapshotIO:
    def __init__(self, max_bytes=MAX_CACHE_BYTES):
        self.max_bytes = max_bytes
        self.lock = threading.RLock()
        self.clear()

    def clear(self):
        self.path = self.stamp = self.snapshot = self.digest = None
        self.fragments = {}
        self.stats = dict(
            reads=0,
            cacheHits=0,
            writes=0,
            unchangedWrites=0,
            encodedFragments=0,
            reusedFragments=0,
        )

    def load(self, path):
        path = Path(path).resolve()
        with self.lock:
            stamp = version(path)
            if (
                path == self.path
                and stamp == self.stamp
                and (self.snapshot is not None)
            ):
                self.stats["cacheHits"] += 1
                return pickle.loads(self.snapshot)
            for _ in range(3):
                stamp = version(path)
                raw = path.read_bytes()
                after = version(path)
                self.stats["reads"] += 1
                value = json.loads(raw)
                if stamp == after:
                    snapshot = pickle.dumps(value, protocol=5)
                    keep_fragments = path == self.path and after == self.stamp
                    self.path, self.stamp = (path, after)
                    if not keep_fragments:
                        self.fragments = {}
                    self.digest = hashlib.sha256(raw).digest()
                    self.snapshot = (
                        snapshot if len(snapshot) <= self.max_bytes else None
                    )
                    if (
                        len(snapshot)
                        + sum((len(v[1]) for v in self.fragments.values()))
                        > self.max_bytes
                    ):
                        self.fragments = {}
                    return value
            self.path = self.stamp = self.snapshot = self.digest = None
            self.fragments = {}
            return value

    def _encode(self, state, previous):
        fragments = {}

        def fragment(identity, value):
            fingerprint = hashlib.sha256(pickle.dumps(value, protocol=5)).digest()
            old = previous.get(identity)
            if old is not None and old[0] == fingerprint:
                encoded = old[1]
                self.stats["reusedFragments"] += 1
            else:
                encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
                self.stats["encodedFragments"] += 1
            fragments[identity] = (fingerprint, encoded)
            return encoded

        chunks = []
        for key, value in state.items():
            label = (
                json.dumps(key).encode("utf-8")
                if isinstance(key, str)
                else json.dumps({key: None}, separators=(",", ":")).encode("utf-8")[
                    1:-6
                ]
            )
            if (
                key in ("tables", "tokens")
                and isinstance(value, dict)
                and all((isinstance(k, str) for k in value))
            ):
                body = (
                    b"{"
                    + b",".join(
                        (
                            json.dumps(k).encode("utf-8") + b":" + fragment((key, k), v)
                            for k, v in value.items()
                        )
                    )
                    + b"}"
                )
            else:
                body = fragment((key,), value)
            chunks.append(label + b":" + body)
        return (b"{" + b",".join(chunks) + b"}", fragments)

    def save(self, path, state):
        path = Path(path).resolve()
        with self.lock:
            try:
                stamp = version(path)
            except FileNotFoundError:
                stamp = None
            same_file = path == self.path and stamp == self.stamp
            raw, fragments = self._encode(state, self.fragments if same_file else {})
            digest = hashlib.sha256(raw).digest()
            if same_file and stamp is not None and (digest == self.digest):
                self.stats["unchangedWrites"] += 1
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=path.parent, suffix=".tmp", delete=False
                ) as output:
                    temporary = Path(output.name)
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(path)
                temporary = None
                if os.name != "nt":
                    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            self.stats["writes"] += 1
            self.path, self.stamp, self.digest = (path, version(path), digest)
            self.snapshot = None
            self.fragments = (
                fragments
                if sum((len(v[1]) for v in fragments.values())) <= self.max_bytes
                else {}
            )


STORE = SnapshotIO()
