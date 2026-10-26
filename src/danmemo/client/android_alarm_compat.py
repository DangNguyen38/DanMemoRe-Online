"""Redirect original exact notification timers to permission-free timers.

The original DEX has an AlarmManager.setExact(I,J,PendingIntent) reference.
Reuse its method slot and an existing 'set' string; no code/register changes.
"""

import hashlib
import struct
import zlib


def patch(data):
    blob = bytearray(data)

    def uleb(pos):
        value = shift = 0
        for _ in range(5):
            byte = blob[pos]
            pos += 1
            value |= (byte & 127) << shift
            shift += 7
            if byte < 128:
                return (value, pos)
        raise ValueError("Invalid DEX string length")

    count, offset = struct.unpack_from("<II", blob, 56)
    strings = []
    for i in range(count):
        pos = struct.unpack_from("<I", blob, offset + i * 4)[0]
        _, pos = uleb(pos)
        strings.append(bytes(blob[pos : blob.index(0, pos)]).decode("utf-8", "replace"))
    if "setExact" not in strings:
        return data
    count, offset = struct.unpack_from("<II", blob, 64)
    types = [
        strings[struct.unpack_from("<I", blob, offset + i * 4)[0]] for i in range(count)
    ]
    count, offset = struct.unpack_from("<II", blob, 88)
    found = []
    for i in range(count):
        cls, proto, name = struct.unpack_from("<HHI", blob, offset + i * 8)
        if types[cls] == "Landroid/app/AlarmManager;" and strings[name] == "setExact":
            found.append((i, proto))
    if not found:
        return data
    if len(found) != 1 or "set" not in strings:
        raise ValueError("Unexpected notification AlarmManager references")
    idx, proto = found[0]
    _, protos_offset = struct.unpack_from("<II", blob, 72)
    shorty = struct.unpack_from("<I", blob, protos_offset + proto * 12)[0]
    if strings[shorty] != "VIJL":
        raise ValueError("Unexpected exact alarm signature")
    struct.pack_into("<I", blob, offset + idx * 8 + 4, strings.index("set"))
    keys = [struct.unpack_from("<HHI", blob, offset + i * 8) for i in range(count)]
    ordered = [(c, n, p) for c, p, n in keys]
    if ordered != sorted(ordered):
        raise ValueError("Alarm compatibility changes DEX method order")
    blob[12:32] = hashlib.sha1(blob[32:]).digest()
    struct.pack_into("<I", blob, 8, zlib.adler32(blob[12:]) & 4294967295)
    return bytes(blob)
