"""Build, serve, and install the isolated DanMemoRe Android client."""

from __future__ import annotations
from danmemo import paths as _paths
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import zipfile
import zlib

ROOT = _paths.ROOT
if not __package__ and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONTENTS = _paths.ASSETS
SOURCE_APK = CONTENTS / "com.eu.danmemo_2023-12-22.apk"
WORK = _paths.STATE
TUTORIAL_DRAW_STATE = WORK / "tutorial-draw.json"
PROGRESS_STATE = WORK / "progress.json"
PROGRESS_LOCK = threading.RLock()
MANIFEST_LOCK = threading.RLock()
LOGIN_REFRESH_TOKENS: dict[str, str] = {}
DIST = _paths.OUTPUT
OUTPUT_APK = DIST / "danmemore-offline.apk"
ARM64_XAPK = WORK / "danmemo-15.1.0-arm64.xapk"
ARM64_XAPK_SHA256 = "f783eeca9db7e690f82f4995fd831d6b8f1066f7954efabce0fb379d95affe82"
ARM64_SOURCE = WORK / "xapk"
ARM64_OUTPUT = DIST / "danmemore-arm64"
ARM64_APKS = (
    "com.eu.danmemo.apk",
    "config.arm64_v8a.apk",
    "config.en.apk",
    "config.fr.apk",
    "config.mdpi.apk",
    "AssetPack1.apk",
)
IDENTITY = DIST / "danmemore-offline.json"
ORIGINAL_PACKAGE = "com.eu.danmemo"
PRIVATE_PACKAGE = "com.eu.danmore"
APP_LABEL = "DanMemoRe"
PORT = 28766
ENABLED_LOTTERIES = (
    171002049,
    171002249,
    171000732,
    171000733,
    171002059,
    171002156,
    171002215,
)
ENDPOINT = os.environ.get(
    "DANMEMORE_CONTENT_ORIGIN", f"http://127.0.0.1:{PORT}"
).rstrip("/")
LOCAL_USER_ID = 1000000001
LOCAL_AES_IV = "DanMemoReLocalIV"
LOCAL_AES_KEY = b"JsfcytENstRNhJBfkNQCCKb62jbZYccX"
LOCAL_TOKEN = "00000000000000000000000000000001"
HOME_CHARACTER_TOKEN = "132d52133a41bfb630af4effa24aeb54"
URL_PATCHES = {
    b"https://api-danmemo-eu.wrightflyer.net": f"{ENDPOINT}/danmemore-local".encode(),
    b"https://cdn-danmemo.akamaized.net/eu": f"{ENDPOINT}/content".encode(),
    b"https://tdsdk-danmemo-eu.wrightflyer.net": f"{ENDPOINT}/telemetry".encode(),
    b"https://bn-payment-us.wrightflyer.net": f"{ENDPOINT}/billing".encode(),
    b"https://storage.googleapis.com/argo-ap4zsi86n4q": f"{ENDPOINT}/storage".encode(),
}


def run(command: list[Path | str], *, env: dict[str, str] | None = None) -> str:
    process = subprocess.run(
        [str(value) for value in command], capture_output=True, timeout=180, env=env
    )
    output = (process.stdout + process.stderr).decode(errors="replace")
    if process.returncode:
        raise RuntimeError(output[-4000:])
    return output


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def xml_chunks(data: bytes) -> list[tuple[int, bytearray]]:
    if len(data) < 8 or struct.unpack_from("<HH", data) != (3, 8):
        raise ValueError("Expected a binary Android XML document")
    (size,) = struct.unpack_from("<I", data, 4)
    if size != len(data):
        raise ValueError("Binary XML size mismatch")
    result = []
    at = 8
    while at < len(data):
        if at + 8 > len(data):
            raise ValueError("Truncated XML chunk")
        kind, header, size = struct.unpack_from("<HHI", data, at)
        if header < 8 or size < header or at + size > len(data):
            raise ValueError("Invalid XML chunk bounds")
        result.append((kind, bytearray(data[at : at + size])))
        at += size
    return result


def patch_manifest(data: bytes) -> bytes:
    old = ORIGINAL_PACKAGE.encode("utf-16le")
    new = PRIVATE_PACKAGE.encode("utf-16le")
    if len(old) != len(new) or data.count(old) != 10:
        raise ValueError("Unexpected package references in AndroidManifest.xml")
    data = data.replace(old, new)
    parts = xml_chunks(data)
    (pool,) = [part for kind, part in parts if kind == 1]
    count, styles, flags, start, style_start = struct.unpack_from("<IIIII", pool, 8)
    if styles or style_start or flags & 256 or (start != 28 + count * 4):
        raise ValueError("Expected an unstyled UTF-16 manifest string pool")
    offsets = list(struct.unpack_from(f"<{count}I", pool, 28))
    strings = []
    for offset in offsets:
        at = start + offset
        (length,) = struct.unpack_from("<H", pool, at)
        end = at + 2 + length * 2
        if length & 32768 or end + 2 > len(pool) or pool[end : end + 2] != b"\x00\x00":
            raise ValueError("Unexpected manifest string encoding")
        strings.append(pool[at + 2 : end].decode("utf-16le"))
    if "usesCleartextTraffic" in strings:
        raise ValueError("Review the existing cleartext policy before patching")
    text = bytearray(pool[start:])
    for value in ("usesCleartextTraffic", APP_LABEL):
        offsets.append(len(text))
        text += struct.pack("<H", len(value)) + value.encode("utf-16le") + b"\x00\x00"
    text += b"\x00" * (-len(text) % 4)
    new_start = 28 + 4 * len(offsets)
    pool[:] = (
        struct.pack(
            "<HHIIIIII",
            1,
            28,
            new_start + len(text),
            len(offsets),
            0,
            flags & ~1,
            new_start,
            0,
        )
        + struct.pack(f"<{len(offsets)}I", *offsets)
        + text
    )
    (resource_map,) = [part for kind, part in parts if kind == 384]
    ids = list(struct.unpack_from(f"<{(len(resource_map) - 8) // 4}I", resource_map, 8))
    if len(ids) > count:
        raise ValueError("Invalid binary XML resource map")
    ids += [0] * (count + 2 - len(ids))
    ids[count] = 16844012
    resource_map[:] = struct.pack("<HHI", 384, 8, 8 + len(ids) * 4) + struct.pack(
        f"<{len(ids)}I", *ids
    )
    applications = 0
    for kind, part in parts:
        if kind != 258:
            continue
        (name,) = struct.unpack_from("<I", part, 20)
        if strings[name] != "application":
            continue
        applications += 1
        attr_start, width, number, id_index, class_index, style_index = (
            struct.unpack_from("<HHHHHH", part, 24)
        )
        if (
            attr_start != 20
            or width != 20
            or id_index
            or class_index
            or style_index
            or (len(part) != 36 + number * 20)
        ):
            raise ValueError("Unexpected application attribute layout")
        attrs = [bytes(part[36 + i * 20 : 56 + i * 20]) for i in range(number)]
        labels = [
            i
            for i, attr in enumerate(attrs)
            if ids[struct.unpack_from("<I", attr, 4)[0]] == 16842753
        ]
        if len(labels) != 1:
            raise ValueError("Expected one application label")
        label_index = labels[0]
        namespace, label = struct.unpack_from("<II", attrs[label_index])
        attrs[label_index] = struct.pack(
            "<IIIHBBI", namespace, label, count + 1, 8, 0, 3, count + 1
        )
        attrs.append(
            struct.pack("<IIIHBBI", namespace, count, 4294967295, 8, 0, 18, 4294967295)
        )
        attrs.sort(key=lambda attr: ids[struct.unpack_from("<I", attr, 4)[0]])
        part[:] = part[:36] + b"".join(attrs)
        struct.pack_into("<I", part, 4, len(part))
        struct.pack_into("<H", part, 28, number + 1)
    if applications != 1:
        raise ValueError("Expected one application element")
    payload = b"".join((part for _, part in parts))
    return struct.pack("<HHI", 3, 8, len(payload) + 8) + payload


def patch_c_string(data: bytes, old: bytes, new: bytes) -> bytes:
    if not new or len(new) > len(old) or data.count(old) != 1:
        raise ValueError(f"Unexpected fixed-width URL patch: {old!r}")
    return data.replace(old, new + b"\x00" * (len(old) - len(new)))


def patch_dex(data: bytes) -> bytes:
    old, new = (ORIGINAL_PACKAGE.encode(), PRIVATE_PACKAGE.encode())
    if data.count(old) != 1 or len(old) != len(new):
        raise ValueError("Unexpected package identity in classes.dex")
    data = bytearray(data.replace(old, new))
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(data[12:]) & 4294967295)
    return bytes(data)


def patch_service_dependencies(data: bytes) -> bytes:
    from danmemo.client.android_alarm_compat import patch as patch_alarms

    data = patch_alarms(data)
    from danmemo.client.android_private_purchase import patch as patch_purchase

    data = patch_purchase(data)
    patches = (
        (
            bytes.fromhex("1a019c097230710017000a076a072425"),
            bytes.fromhex("1a019c097230710017040a076a072425"),
            "Google Play auto-sign-in",
        ),
        (
            bytes.fromhex("0801100054129e07380273016e10b40b0200"),
            bytes.fromhex("0801100054129e07290073016e10b40b0200"),
            "GREE response signature",
        ),
    )
    for old, new, name in patches:
        if data.count(old) != 1:
            raise ValueError(f"Unexpected {name} bytecode")
        data = data.replace(old, new)
    data = bytearray(data)
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(data[12:]) & 4294967295)
    return bytes(data)


def patch_native_client(data: bytes, abi: str) -> bytes:
    if abi != "arm64-v8a":
        return data
    offset = 39778324
    old, new = (bytes.fromhex("e1030032"), bytes.fromhex("01018052"))
    if data[offset : offset + 4] != old:
        raise ValueError("Unexpected ARM64 initial asset phase")
    data = data[:offset] + new + data[offset + 4 :]
    offset = 38452592
    old = bytes.fromhex("e103152a")
    new = bytes.fromhex("8800a317")
    if data[offset : offset + len(old)] != old:
        raise ValueError("Unexpected ARM64 party-selection code")
    data = data[:offset] + new + data[offset + len(old) :]
    for offset, old, new in (
        (39783116, bytes.fromhex("f403012a"), bytes.fromhex("2ded9d17")),
        (14073728, bytes(12), bytes.fromhex("3f00007134149f1ad2126214")),
        (14073744, bytes(12), bytes.fromhex("bf020071a1169f1a77ff5c14")),
    ):
        if data[offset : offset + len(old)] != old:
            raise ValueError(f"Unexpected ARM64 active party code at {offset:#x}")
        data = data[:offset] + new + data[offset + len(old) :]
    assert_offset = 36107560
    assert_old = bytes.fromhex("16170034")
    assert_new = bytes.fromhex("d6160034")
    if data[assert_offset : assert_offset + len(assert_old)] != assert_old:
        raise ValueError("Unexpected ARM64 PartyController assertion code")
    data = data[:assert_offset] + assert_new + data[assert_offset + len(assert_old) :]
    guard_offset = 36110352
    guard_old = bytes.fromhex(
        "681641f9000140f9aef3fe970000221e28208052e9030032e00300bde80b0079e91f0039e1630091e2030091e00313aaa1030094"
    )
    guard_new = bytes.fromhex(
        "681641f9880100b4000140f9adf3fe970000221ee00300bd2820805208010832e80700b9e1630091e2030091e00313aaa1030094"
    )
    if data[guard_offset : guard_offset + len(guard_old)] != guard_old:
        raise ValueError("Unexpected ARM64 PartyController leader-setup code")
    data = data[:guard_offset] + guard_new + data[guard_offset + len(guard_old) :]
    patches = (
        (25217648, bytes.fromhex("fef6ff97"), bytes.fromhex("18ccf497")),
        (
            22280912,
            bytes(36),
            bytes.fromhex(
                "fd7bbda9e00701a9e0031faae2031faae3031f2a35343d94e00741a9fd7bc3a8de2a0b14"
            ),
        ),
        (39281228, bytes.fromhex("ff8302d1"), bytes.fromhex("8d26bf17")),
        (39279296, bytes.fromhex("ff8301d1"), bytes.fromhex("7828bf17")),
        (
            22281344,
            bytes(20),
            bytes.fromhex("e07bbfa93b000094e07bc1a8ff8302d170d94014"),
        ),
        (
            22281376,
            bytes(20),
            bytes.fromhex("e07bbfa933000094e07bc1a8ff8301d185d74014"),
        ),
        (
            22281584,
            bytes(72),
            bytes.fromhex(
                "f37bbca9e80702a9f30300aa37ed2d94000040f9613240b9e843009118994594e80b40f9a80000b4600201918c462e94e007c03d6012803de00313aae80742a9f37bc4a8c0035fd6"
            ),
        ),
        (39057524, bytes.fromhex("c0035fd6"), bytes.fromhex("cbb1a017")),
        (14073760, bytes(16), bytes.fromhex("e07bbfa99d515f94e07bc1a8c0035fd6")),
        (25149524, bytes.fromhex("88424039"), bytes.fromhex("dbbfd517")),
        (
            14073792,
            bytes(24),
            bytes.fromhex("e07bbfa9c5cd57942cce5794e07bc1a88842403921402a14"),
        ),
        (
            22281472,
            bytes(108),
            bytes.fromhex(
                "f37bbfa9f30300aa747e3894082040f9690640f9680200b40a1140f93f010aebc00000546b000054080540f9faffff17080140f9f8ffff17001540f9200100b4080c40f9880000b400810191f37bc1a82c7d381420008052f37bc1a8c0035fd6e00313aaf37bc1a8695d3814"
            ),
        ),
        (24981220, bytes.fromhex("0a112e94"), bytes.fromhex("87b3f597")),
        (30301756, bytes.fromhex("34c51994"), bytes.fromhex("b167e197")),
        (30301932, bytes.fromhex("08c51994"), bytes.fromhex("8567e197")),
        (
            22281920,
            bytes(20),
            bytes.fromhex("550000b534510514e00314aae10315aa30510514"),
        ),
        (23675784, bytes.fromhex("e00314aa"), bytes.fromhex("ceaefa17")),
        (
            22281984,
            bytes(20),
            bytes.fromhex("570000b5bd510514e00316aae10317aab9510514"),
        ),
        (23676396, bytes.fromhex("e00316aa"), bytes.fromhex("45aefa17")),
        (
            22282016,
            bytes(20),
            bytes.fromhex("560000b5e3510514e00315aae10316aadf510514"),
        ),
        (23676580, bytes.fromhex("e00315aa"), bytes.fromhex("1faefa17")),
        (
            22281856,
            bytes(48),
            bytes.fromhex(
                "fd7bbea9e00b00f9fd03009101400091e0031faae2031f2ae3031f2a06333b94e00b40f9fd7bc2a8ffc303d16e463b14"
            ),
        ),
        (37820512, bytes.fromhex("ffc303d1"), bytes.fromhex("88b9c417")),
        (
            14073568,
            bytes(28),
            bytes.fromhex("08a00891082540a91f0109eb41000054c0035fd6ff4302d1f4135414"),
        ),
        (
            14073600,
            bytes(32),
            bytes.fromhex(
                "08a00891082540a91f0109eb6100005400008052c0035fd6fd7bbfa959155414"
            ),
        ),
        (36114116, bytes.fromhex("ff4302d1"), bytes.fromhex("07ecab17")),
        (36115580, bytes.fromhex("fd7bbfa9"), bytes.fromhex("a1eaab17")),
        (
            14073632,
            bytes(20),
            bytes.fromhex("e0030091e103142a92105694e203009105305614"),
        ),
        (36667200, bytes.fromhex("e2030091"), bytes.fromhex("f8cfa917")),
        (
            14073664,
            bytes(24),
            bytes.fromhex("e00315aae1030091e303142a420e5694e203009112265614"),
        ),
        (36657048, bytes.fromhex("e2030091"), bytes.fromhex("ead9a917")),
        (
            14073696,
            bytes(24),
            bytes.fromhex("e00315aae1030091e303142a3a0e5694e2030091b62a5614"),
        ),
        (36661832, bytes.fromhex("e2030091"), bytes.fromhex("46d5a917")),
        (
            44206160,
            bytes.fromhex(
                "205c009000042491e803009141018052e2c30091f31b00f9f303009145d0a597"
            ),
            bytes.fromhex(
                "002f805293e08c97f40300aa51f80d9408808052883200b9883600b9daffff17"
            ),
        ),
    )
    for offset, expected, replacement in patches:
        if data[offset : offset + len(expected)] != expected:
            raise ValueError(f"Unexpected ARM64 empty-party guard code at {offset:#x}")
        data = data[:offset] + replacement + data[offset + len(expected) :]
    for offset, old, new in (
        (25095616, bytes.fromhex("60400054"), bytes.fromhex("3042f517")),
        (
            22279808,
            bytes(32),
            bytes.fromhex(
                "40000054d0bd0a14ff4300d1f30700f9e0030091a7f60a94ff430091ccbf0a14"
            ),
        ),
    ):
        if data[offset : offset + len(old)] != old:
            raise ValueError(
                f"Unexpected ARM64 mission acknowledgement code at {offset:#x}"
            )
        data = data[:offset] + new + data[offset + len(old) :]
    for offset, old, new in (
        (25095328, bytes.fromhex("480040f9"), bytes.fromhex("3842f597")),
        (
            22279552,
            bytes(66),
            bytes.fromhex(
                "f37bbfa9a00240f96101001074caf99709004039290d00123f05007161000054a0831bf8b52301d1a80240f9f37bc1a8c0035fd6726577617264446973706c617900"
            ),
        ),
    ):
        if data[offset : offset + len(old)] != old:
            raise ValueError(
                f"Unexpected ARM64 reward presentation code at {offset:#x}"
            )
        data = data[:offset] + new + data[offset + len(old) :]
    for offset, old, new in (
        (30314664, bytes.fromhex("74ca41f9"), bytes.fromhex("1658e117")),
        (
            22278400,
            bytes.fromhex(
                "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
            ),
            bytes.fromhex(
                "b600805274ca41f921e300f021583891e0630a91f6e302b94272f997bf031af8bf0317f8bf0314f8fffb0af9f6074091d6421791a88303d1e2830b91e3630a91640c8052a60302d1a7c302d1e00313aae10314aae5031faae85b00a915902294e0fb4af9df0200eba0000054e00000b4080040f9081540f903000014080040f9081140f900013fd6a00354f8a88303d11f0100eba0000054e00000b4080040f9081540f903000014080040f9081140f900013fd6a00357f8a8c302d11f0100eba0000054e00000b4080040f9081540f903000014080040f9081140f900013fd6a0035af8a80302d11f0100eba0000054e00000b4080040f9081540f903000014080040f9081140f900013fd6e8634a3968000036e05741f96a6be0973600805274ca41f9a2a71e14"
            ),
        ),
        (30229556, bytes.fromhex("615e42f9"), bytes.fromhex("b3abe117")),
        (
            22278912,
            bytes.fromhex(
                "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
            ),
            bytes.fromhex(
                "615e42f9a96201f0680c805229c12e91b48302d1e80b00b9a94f36a9b40318f8e3230091a48302d1e00313aae2031f2ae5031f2af3802294a00358f89f0200eb00030054400300b4080040f9081540f916000014080040f9081140f900013fd6616642f9c96201b0e8031f3229c11891f4830091e80b00b9e94f02a9f42300f9e2070032e3230091e4830091e00313aae5031f2adb802294e02340f9cb561e14080040f9081140f900013fd6615e42f922541e14"
            ),
        ),
    ):
        if data[offset : offset + len(old)] != old:
            raise ValueError(f"Unexpected ARM64 Home menu code at {offset:#x}")
        data = data[:offset] + new + data[offset + len(old) :]
    offset = 22280000
    replacement = bytes.fromhex(
        "f353bba9fd7b01a9f30300aa010001d0216c0091e0830091b270f997e00313aae18300915f310b94f40300aae883403968000036e01b40f9126ae097140600b45c312794142840f9b40500b4e00313aa010001d021440091f1c9f997e1031faa4cccf997802200b9e00313aa010001d0216c0091eac9f997e1031faa45ccf997007c4093801600f9e00313aa010001d021a80091e2c9f997e1031faa3dccf997803200b9e00313aa010001d021d00091dbc9f997e1031faa36ccf997803600b9e00313aa010001d021000191d4c9f997e1031faa2fccf997803a00b9e00313aaa1fe00f0217c2a91cdc9f997e1031faa28ccf997007c4093802200f9fd7b41a9f353c5a8c0035fd61b010035e80340f9f5a304a9e0230191e1031f2a29000094e00315aab7ffff97e00315aaa5000094e1fb00f0f6e13c14"
    )
    if data[offset : offset + len(replacement)] != bytes(len(replacement)):
        raise ValueError("Unexpected ARM64 PvE result helper padding")
    data = data[:offset] + replacement + data[offset + len(replacement) :]
    offset = 22280960
    replacement = bytes.fromhex(
        "f353baa9f55b01a9fd7b02a9f30300aa010001d021d00191e0c30091c16ff997e00313aae1c300916e300b94f40300aad40400b4e00313aa010001d021d0019107c9f997f30300aa6a302794143040f9d40300b415000090b5023d91a80240b9480300340100009021003d9121c0288be00313aafac8f997b61a4039e1031faadf060071600000547292fa97020000141693fa97a80a40798802088bdf220071a0000054df060071a0000054000100b904000014000100f90200001400010039b5220091e6ffff17fd7b42a9f55b41a9f353c6a8c0035fd6"
    )
    if data[offset : offset + len(replacement)] != bytes(len(replacement)):
        raise ValueError("Unexpected ARM64 PvB result helper padding")
    data = data[:offset] + replacement + data[offset + len(replacement) :]
    offset = 22282048
    replacement = bytes.fromhex(
        "29dbf70118000800d110000220000400dfbef601240004003e11000228000400461100022c000400541100023000080063110002380008003089e3014000040079110002440004005f6bfd014800080048c5ef015000040055c5ef01540004003828e3015800080087a6e301600008008d110002680001000000000000000000"
    )
    if data[offset : offset + len(replacement)] != bytes(len(replacement)):
        raise ValueError("Unexpected ARM64 PvB result helper padding")
    data = data[:offset] + replacement + data[offset + len(replacement) :]
    for offset, old, new in (
        (22787444, bytes.fromhex("cf615294"), bytes.fromhex("6310fe17")),
        (
            22279936,
            bytes.fromhex(
                "0000000000000000000000000000000000000000000000000000000000000000"
            ),
            bytes.fromhex(
                "3f0000716b0000543f00196b4b000054e1031f2a619203b96651549497ef0114"
            ),
        ),
    ):
        if data[offset : offset + len(old)] != old:
            raise ValueError(f"Unexpected ARM64 party tab code at {offset:#x}")
        data = data[:offset] + new + data[offset + len(old) :]
    for offset, old, new in (
        (38079708, bytes.fromhex("082040f9"), bytes.fromhex("41bcc317")),
        (38078532, bytes.fromhex("a90e44f8"), bytes.fromhex("6fbdc317")),
        (
            22281216,
            bytes.fromhex(
                "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
            ),
            bytes.fromhex(
                "f353bca9f57b01a9f30300aa93ed2d94000040f9612e40f9e8830091ea9a4594e92b42a9090200b4742640f9692a04a9b40100b48822009109fd5fc82a0500d10afd0bc8abffff35e90000b5880240f9e00314aa080940f900013fd6e00314aab4a1e097e00313aaf57b41a9f353c4a8c0035fd6"
            ),
        ),
        (
            22281696,
            bytes.fromhex("00000000000000000000000000000000"),
            bytes.fromhex("e00313aa87ffff97682240f9bd433c14"),
        ),
        (
            22281728,
            bytes.fromhex("00000000000000000000000000000000"),
            bytes.fromhex("e00313aa7fffff97a90e44f88f423c14"),
        ),
    ):
        if data[offset : offset + len(old)] != old:
            raise ValueError(f"Unexpected ARM64 stage refresh code at {offset:#x}")
        data = data[:offset] + new + data[offset + len(old) :]
    for offset, old, new in (
        (30236392, bytes.fromhex("3e6e2494"), bytes.fromhex("cea7e197")),
        (
            22281760,
            bytes(24),
            bytes.fromhex("fd7bbfa9fd0300916ec64294ef8b1e94fd7bc1a8c0035fd6"),
        ),
    ):
        if data[offset : offset + len(old)] != old:
            raise ValueError(f"Unexpected ARM64 Auto Move save code at {offset:#x}")
        data = data[:offset] + new + data[offset + len(old) :]
    for offset, old, new in (
        (33867516, bytes.fromhex("63fc2491"), bytes.fromhex("d1cdd397")),
        (
            22281792,
            bytes(24),
            bytes.fromhex("88024039830a40f9890600911f0100722301839ac0035fd6"),
        ),
    ):
        if data[offset : offset + len(old)] != old:
            raise ValueError(f"Unexpected ARM64 layout diagnostic code at {offset:#x}")
        data = data[:offset] + new + data[offset + len(old) :]
    for offset, old, new in (
        (34233748, bytes.fromhex("603240f9"), bytes.fromhex("bb66d217")),
        (
            22280320,
            bytes(52),
            bytes.fromhex(
                "e903433949010036e96740f93f9100f1e3000054e16b40f92100098b219000d1e0a30291020480522568fa97603240f93a992d14"
            ),
        ),
    ):
        if data[offset : offset + len(old)] != old:
            raise ValueError(f"Unexpected ARM64 voice cache code at {offset:#x}")
        data = data[:offset] + new + data[offset + len(old) :]
    offset = 22280448
    replacement = bytes.fromhex(
        "ff4307d1fc6f17a9fa6718a9f85f19a9f6571aa9f44f1ba9fd7b1ca9fd0307915bd03bd5681740f9f50300aaf603012a010001b0a8031af8000040f921743e9187c9f997e10300aaa04303d1b7b3f997a04303d1e1031faac98afa9781f100f021e01d917ec9f997e1031faaf992fa97f40300aac3a53c94685001d008a10b91e8530da9e8830391e143039193ad3c94b30240f9a00240f921f300f0218835916fc9f997f60300aae05b00f9e0c30291ff6700f95392fa97a00302d1b60318f8bf8319f861caf997e87340f916810091e0c30291a10302d11ccaf997e0030037e0c302917b92fa97e10300aae0e300918eb3f997e0c30191e10316aac69a0a94f76347a9ff0218eba0010054e80240f9191540f9e0e30091e1031faacd92fa973f0300eb61000054e00240f9eef03c94f74200911f0317eba1feff54e0c30191ee9a0a94e0e30091cab2f997e0c302919594fa97dfffff17f37740f9b30100b46822009109fd5fc82a0500d10afd0bc8abffff35e90000b5680240f9e00313aa080940f900013fd6e00313aa29a2e097a04303d1b7b2f997681740f9a9035af81f0109eb81d89654fd7b5ca9f44f5ba9f6575aa9f85f59a9fa6758a9fc6f57a9ff430791c0035fd6"
    )
    if data[offset : offset + len(replacement)] != bytes(len(replacement)):
        raise ValueError("Unexpected ARM64 cached-task helper padding")
    data = data[:offset] + replacement + data[offset + len(replacement) :]
    for offset, old, new in (
        (38240328, bytes.fromhex("218200d0"), bytes.fromhex("2e1dc317")),
        (
            22279424,
            bytes(32),
            bytes.fromhex(
                "d200001400000000000000000000000000000000000000000000000000000000"
            ),
        ),
        (30416312, bytes.fromhex("5f7c00a9"), bytes.fromhex("e2f5e017")),
        (
            22279488,
            bytes(40),
            bytes.fromhex(
                "15010035e07bbea9e20b00f9e00302aae1031f2aeb000094e20b40f9e07bc2a85f7c00a9160a1f14"
            ),
        ),
        (36635244, bytes.fromhex("54eeff97"), bytes.fromhex("e53cc997")),
        (
            22279680,
            bytes.fromhex(
                "00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
            ),
            bytes.fromhex(
                "ffc303d1fa670aa9f85f0ba9f6570ca9f44f0da9fd7b0ea9fd83039159d03bd5f30308aa281740f9a8831bf8f403032af503022af70300aa200840b9e82d0894f60300aae80301917d9d3694e02340f9a1e30091e203142ae9594394ffee2d94000040f9e103019153ca4594aeb13614"
            ),
        ),
    ):
        if data[offset : offset + len(old)] != old:
            raise ValueError(f"Unexpected ARM64 completion code at {offset:#x}")
        data = data[:offset] + new + data[offset + len(old) :]
    from danmemo.client.patches import rush_result_patch as patch_rush_result
    from danmemo.client.patches import (
        request_timestamp_patch as patch_request_timestamp,
    )
    from danmemo.client.patches import phase_bootstrap_patch as patch_phase_bootstrap
    from danmemo.client.patches import manifest_cache_patch as patch_manifest_cache
    from danmemo.client.patches import (
        manifest_storage_root_patch as patch_manifest_storage_root,
    )
    from danmemo.client.patches import (
        familia_membership_patch as patch_familia_membership,
    )
    from danmemo.client.patches import player_exp_patch as patch_player_exp
    from danmemo.client.patches import familia_refresh_patch as patch_familia_refresh
    from danmemo.client.patches import outfit_shop_patch as patch_outfit_shop
    from danmemo.client.patches import royale_result_patch as patch_royale_result
    from danmemo.client.patches import union_result_patch as patch_union_result

    return patch_union_result(
        patch_royale_result(
            patch_outfit_shop(
                patch_familia_refresh(
                    patch_player_exp(
                        patch_familia_membership(
                            patch_manifest_storage_root(
                                patch_manifest_cache(
                                    patch_phase_bootstrap(
                                        patch_request_timestamp(patch_rush_result(data))
                                    )
                                )
                            )
                        )
                    )
                )
            )
        )
    )


def find_android_tools():
    windows = os.name == "nt"
    exe = ".exe" if windows else ""
    sdk = next(
        (
            Path(os.environ[k]).expanduser()
            for k in ("ANDROID_SDK_ROOT", "ANDROID_HOME")
            if os.environ.get(k)
        ),
        None,
    )
    if sdk is None or not sdk.is_dir():
        raise FileNotFoundError("Set ANDROID_SDK_ROOT to the Android SDK")
    versions = sorted(
        (
            p
            for p in (sdk / "build-tools").iterdir()
            if (p / ("zipalign" + exe)).is_file()
        ),
        reverse=True,
    )
    if not versions:
        raise FileNotFoundError("Install Android SDK build-tools")
    tools = versions[0]
    java = (
        Path(os.environ["JAVA_HOME"]) / "bin" / ("java" + exe)
        if os.environ.get("JAVA_HOME")
        else Path(shutil.which("java") or "")
    )
    keytool = java.with_name("keytool" + exe)
    result = (
        tools / ("zipalign" + exe),
        tools / ("aapt" + exe),
        tools / "lib/apksigner.jar",
        java,
        keytool,
        sdk / "platform-tools" / ("adb" + exe),
    )
    if missing := [str(p) for p in result if not p.is_file()]:
        raise FileNotFoundError("Missing build tools: " + ", ".join(missing))
    return result


def patch_and_sign_apk(
    source_apk: Path,
    output_apk: Path,
    primary: bool,
    tools: tuple[Path, Path, Path, Path, Path],
    keystore: Path,
    password: str,
) -> tuple[list[dict[str, str]], str]:
    zipalign, aapt, signer, java, _keytool = tools
    with tempfile.TemporaryDirectory(prefix="apk-", dir=WORK) as temp_name:
        temp = Path(temp_name)
        unsigned, aligned, signed = (
            temp / "unsigned.apk",
            temp / "aligned.apk",
            temp / "signed.apk",
        )
        changes = []
        with (
            zipfile.ZipFile(source_apk) as source,
            zipfile.ZipFile(unsigned, "w") as target,
        ):
            for entry in source.infolist():
                name = entry.filename
                if name == "stamp-cert-sha256" or (
                    name.startswith("META-INF/")
                    and (
                        name == "META-INF/MANIFEST.MF"
                        or name.endswith((".RSA", ".DSA", ".EC", ".SF"))
                    )
                ):
                    continue
                data = source.read(entry)
                before = hashlib.sha256(data).hexdigest()
                if name == "AndroidManifest.xml":
                    if primary:
                        data = patch_manifest(data)
                    else:
                        old, new = (
                            ORIGINAL_PACKAGE.encode("utf-16le"),
                            PRIVATE_PACKAGE.encode("utf-16le"),
                        )
                        if data.count(old) != 1:
                            raise ValueError(
                                f"Unexpected split package identity in {source_apk.name}"
                            )
                        data = data.replace(old, new)
                elif name == "resources.arsc":
                    old, new = (
                        ORIGINAL_PACKAGE.encode("utf-16le"),
                        PRIVATE_PACKAGE.encode("utf-16le"),
                    )
                    if data.count(old) not in (0, 1):
                        raise ValueError(
                            "Unexpected package identity in resources.arsc"
                        )
                    data = data.replace(old, new)
                elif name == "classes.dex":
                    data = patch_dex(data)
                elif name == "classes3.dex":
                    data = patch_service_dependencies(data)
                elif name.endswith("/libapp.so"):
                    for old, new in URL_PATCHES.items():
                        data = patch_c_string(data, old, new)
                    data = patch_native_client(data, Path(name).parent.name)
                after = hashlib.sha256(data).hexdigest()
                if before != after:
                    changes.append(
                        {"member": name, "before_sha256": before, "after_sha256": after}
                    )
                target.writestr(entry, data)
            if primary:
                from danmemo.client.patches import (
                    phase_bootstrap_bootstrap_resources as bootstrap_resources,
                )

                for member, payload in bootstrap_resources(ROOT):
                    if member in source.namelist():
                        raise ValueError(f"Duplicate phase bootstrap: {member}")
                    target.writestr(member, payload)
                    changes.append(
                        {
                            "member": member,
                            "before_sha256": "",
                            "after_sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    )
                for lottery_id in dict.fromkeys(
                    (*ENABLED_LOTTERIES, 171000142, 171000143)
                ):
                    relative = f"files/lottery/background/{lottery_id}.png"
                    payload = (CONTENTS / "3" / relative).read_bytes()
                    member = "assets/" + relative
                    if member in source.namelist():
                        raise ValueError(f"Duplicate bundled banner: {member}")
                    target.writestr(member, payload)
                    changes.append(
                        {
                            "member": member,
                            "before_sha256": "",
                            "after_sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    )
        run([zipalign, "-P", "16", "4", unsigned, aligned])
        env = os.environ | {"DANMEMORE_KEY_PASSWORD": password}
        run(
            [
                java,
                "-jar",
                signer,
                "sign",
                "--min-sdk-version",
                "19",
                "--ks",
                keystore,
                "--ks-pass",
                "env:DANMEMORE_KEY_PASSWORD",
                "--v1-signing-enabled",
                "true",
                "--v2-signing-enabled",
                "true",
                "--v3-signing-enabled",
                "true",
                "--v4-signing-enabled",
                "false",
                "--out",
                signed,
                aligned,
            ],
            env=env,
        )
        verification = run(
            [
                java,
                "-jar",
                signer,
                "verify",
                "--min-sdk-version",
                "19",
                "--verbose",
                "--print-certs",
                signed,
            ]
        )
        tree = run([aapt, "dump", "xmltree", signed, "AndroidManifest.xml"])
        required = (
            f'package="{PRIVATE_PACKAGE}"',
            "usesCleartextTraffic(0x010104ec)=(type 0x12)0xffffffff",
            f'="{APP_LABEL}"',
        )
        if any((value not in tree for value in required)):
            if primary or f'package="{PRIVATE_PACKAGE}"' not in tree:
                raise ValueError(
                    f"Patched manifest did not validate: {source_apk.name}"
                )
        output_apk.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(signed, output_apk)
    signer_digest = next(
        (
            line.rsplit(":", 1)[1].strip()
            for line in verification.splitlines()
            if "certificate SHA-256 digest:" in line
        )
    )
    return (changes, signer_digest)


def build() -> None:
    if not SOURCE_APK.is_file():
        raise FileNotFoundError(f"Original APK not found: {SOURCE_APK}")
    WORK.mkdir(exist_ok=True)
    DIST.mkdir(exist_ok=True)
    zipalign, aapt, signer, java, keytool, _adb = find_android_tools()
    password_path, keystore = (WORK / "local-key.password", WORK / "local-key.p12")
    if password_path.exists() != keystore.exists():
        raise ValueError("Incomplete local signing identity in the state directory")
    if not keystore.exists():
        password_path.write_text(secrets.token_hex(24) + "\n", encoding="ascii")
        env = os.environ | {"DANMEMORE_KEY_PASSWORD": password_path.read_text().strip()}
        run(
            [
                keytool,
                "-genkeypair",
                "-keystore",
                keystore,
                "-storetype",
                "PKCS12",
                "-alias",
                "danmemore-local",
                "-storepass:env",
                "DANMEMORE_KEY_PASSWORD",
                "-keyalg",
                "RSA",
                "-keysize",
                "2048",
                "-validity",
                "3650",
                "-dname",
                "CN=DanMemoRe Local Client",
            ],
            env=env,
        )
    password = password_path.read_text(encoding="ascii").strip()
    tools = (zipalign, aapt, signer, java, keytool)
    changes, signer_digest = patch_and_sign_apk(
        SOURCE_APK, OUTPUT_APK, True, tools, keystore, password
    )
    arm64_outputs = []
    if ARM64_XAPK.is_file():
        if sha256(ARM64_XAPK) != ARM64_XAPK_SHA256:
            raise ValueError("Unexpected APKPure arm64 XAPK hash")
        ARM64_SOURCE.mkdir(exist_ok=True)
        with zipfile.ZipFile(ARM64_XAPK) as bundle:
            for name in ARM64_APKS:
                (ARM64_SOURCE / name).write_bytes(bundle.read(name))
        for name in ARM64_APKS:
            source = ARM64_SOURCE / name
            output = ARM64_OUTPUT / name
            split_changes, split_signer = patch_and_sign_apk(
                source, output, name == "com.eu.danmemo.apk", tools, keystore, password
            )
            if split_signer != signer_digest:
                raise ValueError("Split APK signer mismatch")
            arm64_outputs.append(
                {
                    "file": str(output.relative_to(ROOT)),
                    "bytes": output.stat().st_size,
                    "sha256": sha256(output),
                    "changed_members": split_changes,
                }
            )
    identity = {
        "project": "DanMemoRe",
        "purpose": "isolated local routing client; unknown game routes fail closed",
        "source_apk": {
            "bytes": SOURCE_APK.stat().st_size,
            "sha256": sha256(SOURCE_APK),
        },
        "output_apk": {
            "bytes": OUTPUT_APK.stat().st_size,
            "sha256": sha256(OUTPUT_APK),
        },
        "package": PRIVATE_PACKAGE,
        "endpoint": ENDPOINT,
        "signer_sha256": signer_digest,
        "patched_urls": {
            old.decode(): new.decode() for old, new in URL_PATCHES.items()
        },
        "changed_members": changes,
        "arm64_outputs": arm64_outputs,
    }
    IDENTITY.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(identity, indent=2))
