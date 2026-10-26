"""Upstream a75d0e8 GREE DEX patches for free private-server Purchase.

Retains server product/order validation; replaces only the retired Play-store
hop. Exact byte guards reject other SDK revisions. Original offsets/classes3.dex
come from upstream tools/offline_client.py::patch_service_dependencies.
"""

import hashlib
import struct
import zlib

PATCHES = (
    (
        914212,
        bytes.fromhex("5430b10d2201c4087030512a31041f007a08"),
        bytes.fromhex("2200740370109c0d00007220ba2304000e00"),
    ),
    (890562, bytes.fromhex("39000a00"), bytes.fromhex("00000000")),
    (911744, bytes.fromhex("38011000"), bytes.fromhex("00000000")),
    (
        917228,
        bytes.fromhex("71007d2400000a0038003400"),
        bytes.fromhex("12007220ba2306000e00") + bytes(136),
    ),
)


def patch(data):
    if not data.startswith(b"dex\n"):
        raise ValueError("Expected original SDK DEX")
    result = bytearray(data)
    for offset, before, after in PATCHES:
        if result[offset : offset + len(after)] == after:
            continue
        if result[offset : offset + len(before)] != before:
            raise ValueError(f"Unexpected original Purchase bytecode at {offset:#x}")
        result[offset : offset + len(after)] = after
    result[12:32] = hashlib.sha1(result[32:]).digest()
    struct.pack_into("<I", result, 8, zlib.adler32(result[12:]) & 4294967295)
    return bytes(result)
