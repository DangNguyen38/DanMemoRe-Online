"""Read archived encrypted FlatBuffers and produce local schedule overlays."""

from danmemo import paths as _paths
import functools
import shutil
import struct
import subprocess
import time
import zipfile
import zlib

ROOT = _paths.ROOT


@functools.cache
def keys():
    with zipfile.ZipFile(_paths.CLIENT_APK) as archive:
        native = archive.read("lib/arm64-v8a/libapp.so")
    numeric = struct.unpack(
        "<4I",
        bytes((native[55196076 + (b ^ 31)] ^ 154 for b in native[55192720:55192736])),
    )
    aes = bytes((native[55492091 + (b ^ 66)] ^ 110 for b in native[55153123:55153155]))
    iv = bytes((native[55492219 + (b ^ 168)] ^ 219 for b in native[55148592:55148608]))
    return (numeric, aes, iv)


def crypt_words(raw: bytes, encode=False) -> bytes:
    if not encode and len(raw) <= 16384:
        return _decoded_words(bytes(raw), keys()[0])
    return _crypt_words(raw, encode, keys()[0])


@functools.lru_cache(maxsize=65536)
def _decoded_words(raw: bytes, key) -> bytes:
    return _crypt_words(raw, False, key)


def _crypt_words(raw: bytes, encode, key) -> bytes:
    mask, delta = (4294967295, 2654435769)
    if encode:
        length = len(raw)
        raw += bytes(-length % 4) + struct.pack("<I", length)
    v = list(struct.unpack("<" + str(len(raw) // 4) + "I", raw))
    n = len(v)
    if n < 2:
        raise ValueError("Invalid encrypted field")
    total = 0 if encode else (6 + 52 // n) * delta & mask

    def mx(z, y, p, e):
        return (
            (z >> 5 ^ y << 2) + (y >> 3 ^ z << 4) ^ (total ^ y) + (key[p & 3 ^ e] ^ z)
        ) & mask

    if encode:
        z = v[-1]
        for _ in range(6 + 52 // n):
            total = total + delta & mask
            e = total >> 2 & 3
            for p in range(n - 1):
                z = v[p] = v[p] + mx(z, v[p + 1], p, e) & mask
            z = v[-1] = v[-1] + mx(z, v[0], n - 1, e) & mask
    else:
        y = v[0]
        while total:
            e = total >> 2 & 3
            for p in range(n - 1, 0, -1):
                y = v[p] = v[p] - mx(v[p - 1], y, p, e) & mask
            y = v[0] = v[0] - mx(v[-1], y, 0, e) & mask
            total = total - delta & mask
    out = struct.pack("<" + str(n) + "I", *v)
    return out if encode else out[: v[-1]]


def aes_bytes(data, encode=False):
    _, key, iv = keys()
    openssl = shutil.which("openssl")
    if not openssl:
        raise FileNotFoundError("OpenSSL is not on PATH")
    command = [openssl, "enc", "-aes-256-cbc", "-K", key.hex(), "-iv", iv.hex()]
    if not encode:
        command += ["-d"]
    result = subprocess.run(command, input=data, capture_output=True, check=True)
    return result.stdout


class Row:
    def __init__(self, data, offset):
        self.data, self.offset = (data, offset)

    def pos(self, field):
        vt = self.offset - struct.unpack_from("<i", self.data, self.offset)[0]
        size = struct.unpack_from("<H", self.data, vt)[0]
        slot = 4 + field * 2
        off = struct.unpack_from("<H", self.data, vt + slot)[0] if slot < size else 0
        return self.offset + off if off else None

    def number(self, field, width=4):
        p = self.pos(field)
        return (
            int.from_bytes(crypt_words(self.data[p : p + width + 4]), "little")
            if p
            else 0
        )

    def text(self, field):
        p = self.pos(field)
        if not p:
            return ""
        p += struct.unpack_from("<I", self.data, p)[0]
        length = struct.unpack_from("<I", self.data, p)[0]
        return crypt_words(self.data[p + 4 : p + 4 + length]).decode().rstrip("\x00")

    def set_number(self, field, value, width=8):
        p = self.pos(field)
        if p is None:
            raise ValueError(f"Master row lacks schedule field {field}")
        self.data[p : p + width + 4] = crypt_words(
            value.to_bytes(width, "little"), True
        )


@functools.cache
def master_bytes(name):
    encrypted = (
        _paths.ASSETS / "1" / "master" / "divide_enc" / (name + ".enc")
    ).read_bytes()
    return zlib.decompress(aes_bytes(encrypted))


def rows(data):
    root = Row(data, struct.unpack_from("<I", data)[0])
    p = root.pos(0)
    p += struct.unpack_from("<I", data, p)[0]
    length = struct.unpack_from("<I", data, p)[0]
    return [
        Row(data, p + 4 + i * 4 + struct.unpack_from("<I", data, p + 4 + i * 4)[0])
        for i in range(length)
    ]


def schedule_overlay(name, schedules, output):
    data = bytearray(master_bytes(name))
    fields = (
        (11, 12)
        if name == "equipmentRecipe"
        else (12, 13)
        if name in ("adventureStage", "shopProduct")
        else (4, 5)
        if name in ("lottery", "shop")
        else (6, 7)
    )
    width = (
        8 if name in ("adventureEpisode", "adventureStage", "equipmentRecipe") else 4
    )
    for row in rows(data):
        identifier = str(row.number(0, width))
        if identifier in schedules:
            period = schedules[identifier]
            grace = (
                max(0, row.number(24, 8) - row.number(7, 8))
                if name == "adventureEpisode" and row.pos(24)
                else None
            )
            for field, value in zip(fields, (period["start"], period["end"])):
                if row.pos(field):
                    row.set_number(field, value)
            if grace is not None:
                row.set_number(24, min(2147483646, period["end"] + grace))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(aes_bytes(zlib.compress(data), True))
    return output


def arena_overlay(name, identifier, fields, output):
    """Reopen one original season without changing its rules or opponents."""
    data = bytearray(master_bytes(name))
    selected = next((r for r in rows(data) if r.number(0, 8) == identifier), None)
    if selected is None:
        raise ValueError(f"Unknown {name} season {identifier}")
    for start, end in zip(fields[::2], fields[1::2]):
        selected.set_number(start, int(time.time()) - 86400)
        selected.set_number(end, 2114356592)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(aes_bytes(zlib.compress(data), True))
    return output
