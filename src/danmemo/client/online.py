"""Online endpoint and SDK compatibility for self-hosted clients."""

import hashlib, struct, zlib

from danmemo.client import build as client

_offline_sdk_patch = client.patch_service_dependencies

_offline_native_patch = client.patch_native_client


def patch_online_native(data, abi):
    data = _offline_native_patch(data, abi)
    if abi == "arm64-v8a":
        from danmemo.client.patches import popularity_patch

        data = popularity_patch(data)
    if abi != "arm64-v8a":
        return data
    data = bytearray(data)
    for offset, url, original_length in (
        (31395808, b"https://api-danmemo-eu.wrightflyer.net", 38),
        (31395828, b"https://cdn-danmemo.akamaized.net/eu", 36),
    ):
        original = 1384120322 | original_length << 5
        if struct.unpack_from("<I", data, offset)[0] != original:
            raise ValueError("Unexpected original endpoint constructor")
        struct.pack_into(
            "<I", data, offset, 1384120322 | len(client.URL_PATCHES[url]) << 5
        )
    return bytes(data)


def patch_online_sdk(data):
    data = bytearray(_offline_sdk_patch(data))
    if data[833280:833284] != bytes.fromhex("13020002"):
        raise ValueError("Unexpected original SDK RSA key generation")
    data[833280:833284] = bytes.fromhex("13020008")
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(data[12:]) & 4294967295)
    return bytes(data)


def configure(
    port=28768,
    instance=1,
    endpoint=None,
    diagnose_json=False,
    diagnose_gacha=False,
    app_label=None,
):
    if instance not in (1, 2):
        raise ValueError("Invalid online client instance")
    old = client.ENDPOINT.encode()
    if endpoint:
        endpoint = endpoint.rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("Endpoint must begin with http:// or https://")
        client.ENDPOINT = endpoint
        api_target = (
            f"{endpoint}/danmemore-local"
            if len(f"{endpoint}/danmemore-local") <= 38
            else f"{endpoint}/api"
        )
        client.URL_PATCHES = {
            b"https://api-danmemo-eu.wrightflyer.net": api_target.encode(),
            b"https://cdn-danmemo.akamaized.net/eu": f"{endpoint}/content".encode(),
            b"https://tdsdk-danmemo-eu.wrightflyer.net": f"{endpoint}/telemetry".encode(),
            b"https://bn-payment-us.wrightflyer.net": f"{endpoint}/billing".encode(),
            b"https://storage.googleapis.com/argo-ap4zsi86n4q": f"{endpoint}/storage".encode(),
        }
    else:
        if not 1024 <= port <= 65535:
            raise ValueError("Invalid local port")
        client.PORT = port
        client.ENDPOINT = f"http://127.0.0.1:{port}"
        client.URL_PATCHES = {
            key: value.replace(old, client.ENDPOINT.encode())
            for key, value in client.URL_PATCHES.items()
        }
    for old_url, new_url in client.URL_PATCHES.items():
        if len(new_url) > len(old_url):
            raise ValueError(
                f"Endpoint URL '{new_url.decode()}' ({len(new_url)} chars) exceeds native binary field width for '{old_url.decode()}' ({len(old_url)} chars)"
            )
    client.PRIVATE_PACKAGE = "com.eu.danmono" if instance == 1 else "com.eu.danmon2"
    client.APP_LABEL = app_label or (
        "DanMemoRe Online" if instance == 1 else "DanMemoRe Online 2"
    )
    client.patch_service_dependencies = patch_online_sdk
    client.patch_native_client = patch_online_native
    output = client.ROOT / (
        "dist/online-client" if instance == 1 else "dist/online-client-2"
    )
    client.DIST = output
    client.OUTPUT_APK = output / "danmemore-local-online.apk"
    client.ARM64_OUTPUT = output / "arm64"
    client.IDENTITY = output / "client.json"
