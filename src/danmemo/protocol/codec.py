"""Native userdata MessagePack encoding, preserving the existing wire bytes.

Keep minimal integer widths, float64 and dictionary insertion order: userdata
checksums depend on the encoded representation, not just decoded values.
Only immutable short-string bytes are cached; account containers are not.
"""

from functools import lru_cache
import struct


@lru_cache(maxsize=512)
def msgpack_string(value: str) -> bytes:
    raw = value.encode("utf-8")
    size = len(raw)
    if size <= 31:
        return bytes((160 | size,)) + raw
    if size <= 255:
        return bytes((217, size)) + raw
    raise ValueError("User-data table name is too long")


def msgpack_value(value: object) -> bytes:
    output = bytearray()
    _append(output, value)
    return bytes(output)


def _append(output, value):
    if value is None:
        output.append(192)
    elif isinstance(value, bool):
        output.append(195 if value else 194)
    elif isinstance(value, int):
        if 0 <= value <= 127:
            output.append(value)
        elif 0 <= value <= 255:
            output.extend(b"\xcc" + struct.pack(">B", value))
        elif 0 <= value <= 65535:
            output.extend(b"\xcd" + struct.pack(">H", value))
        elif 0 <= value <= 4294967295:
            output.extend(b"\xce" + struct.pack(">I", value))
        elif value >= 0:
            output.extend(b"\xcf" + struct.pack(">Q", value))
        elif value >= -32:
            output.append(value & 255)
        elif value >= -128:
            output.extend(b"\xd0" + struct.pack(">b", value))
        elif value >= -32768:
            output.extend(b"\xd1" + struct.pack(">h", value))
        elif value >= -2147483648:
            output.extend(b"\xd2" + struct.pack(">i", value))
        else:
            output.extend(b"\xd3" + struct.pack(">q", value))
    elif isinstance(value, float):
        output.extend(b"\xcb" + struct.pack(">d", value))
    elif isinstance(value, str):
        if len(value) <= 63:
            raw = msgpack_string(value)
            output.extend(raw)
        else:
            raw = value.encode("utf-8")
            if len(raw) <= 255:
                output.extend(msgpack_string(value))
            elif len(raw) <= 65535:
                output.extend(b"\xda" + struct.pack(">H", len(raw)) + raw)
            else:
                output.extend(b"\xdb" + struct.pack(">I", len(raw)) + raw)
    elif isinstance(value, list):
        size = len(value)
        if size <= 15:
            output.append(144 | size)
        elif size <= 65535:
            output.extend(b"\xdc" + struct.pack(">H", size))
        else:
            output.extend(b"\xdd" + struct.pack(">I", size))
        for item in value:
            _append(output, item)
    elif isinstance(value, dict):
        size = len(value)
        if size <= 15:
            output.append(128 | size)
        elif size <= 65535:
            output.extend(b"\xde" + struct.pack(">H", size))
        else:
            output.extend(b"\xdf" + struct.pack(">I", size))
        for key, item in value.items():
            _append(output, key)
            _append(output, item)
    else:
        raise ValueError("Unsupported local MessagePack value")
