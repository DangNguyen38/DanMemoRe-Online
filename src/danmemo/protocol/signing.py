"""Original SDK signing verification; game signing is a static reconstruction.

Nonce consumption and account binding belong to the gateway's transaction.
No application credentials or device private keys are written or logged here.
"""

from __future__ import annotations
from danmemo import paths as _paths
import base64
from dataclasses import dataclass
import hashlib
import hmac
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import quote, unquote, urlsplit
import zipfile

ROOT = _paths.ROOT
MAX_BODY = 64 * 1024 * 1024


@dataclass(frozen=True, repr=False)
class AppCredentials:
    consumer_key: str
    secret: str


@dataclass(frozen=True)
class VerifiedOAuth:
    uuid: str | None
    timestamp: int
    nonce: str


@dataclass(frozen=True)
class VerifiedGame:
    timestamp: int
    nonce: str
    signature: str


def _openssl(args: list[str], data: bytes = b"") -> bytes:
    executable = shutil.which("openssl")
    if not executable:
        raise ValueError("OpenSSL is unavailable")
    try:
        result = subprocess.run(
            [executable, *args],
            input=data,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("OpenSSL operation failed") from error
    if result.returncode:
        raise ValueError("Invalid cryptographic material or signature")
    return result.stdout


def _native(apk_path: str | Path | None = None) -> bytes:
    path = Path(apk_path) if apk_path is not None else _paths.CLIENT_APK
    with zipfile.ZipFile(path) as archive:
        return archive.read("lib/arm64-v8a/libapp.so")


def load_app_credentials(apk_path: str | Path | None = None) -> AppCredentials:
    native = _native(apk_path)

    def cstring(offset):
        end = native.find(b"\x00", offset, offset + 256)
        if end < 0:
            raise ValueError("Unsupported original credential layout")
        return native[offset:end]

    key = cstring(55491783)
    if len(key) != 16:
        raise ValueError("Unsupported original credential layout")
    decoded = []
    for offset, expected in ((55491552, 15), (55491585, 32)):
        try:
            encrypted = bytes.fromhex(cstring(offset).decode("ascii"))
            value = _openssl(["enc", "-d", "-aes-128-ecb", "-K", key.hex()], encrypted)
            if len(value) != expected:
                raise ValueError("Unsupported original credential layout")
            decoded.append(value.decode("ascii"))
        except (UnicodeError, ValueError) as error:
            raise ValueError("Unsupported original credential layout") from error
    return AppCredentials(*decoded)


def load_game_salt(apk_path: str | Path | None = None) -> bytes:
    native = _native(apk_path)
    try:
        return bytes(
            (native[55152995 + (x ^ 66)] ^ 110 for x in native[55153123:55153155])
        )
    except IndexError as error:
        raise ValueError("Unsupported original game signature layout") from error


def validate_public_key(pem: str) -> str:
    if (
        not isinstance(pem, str)
        or len(pem) > 8192
        or (
            not re.fullmatch(
                "-----BEGIN PUBLIC KEY-----\\r?\\n[A-Za-z0-9+/=\\r\\n]+\\r?\\n-----END PUBLIC KEY-----\\r?\\n?",
                pem,
            )
        )
    ):
        raise ValueError("Invalid public key")
    try:
        encoded = pem.encode("ascii")
    except UnicodeError as error:
        raise ValueError("Invalid public key") from error
    text = _openssl(["pkey", "-pubin", "-text", "-noout"], encoded).decode("ascii")
    bits = re.search("Public-Key: \\((\\d+) bit\\)", text)
    if not bits or not 2048 <= int(bits.group(1)) <= 4096 or "Modulus:" not in text:
        raise ValueError(
            "An RSA public key of 2048-4096 bits is required (received "
            + (bits.group(1) if bits else "unknown")
            + " bits)"
        )
    _openssl(["pkey", "-pubin", "-pubcheck", "-noout"], encoded)
    return _openssl(["pkey", "-pubin", "-outform", "PEM"], encoded).decode("ascii")


def _base64(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError("Invalid signature encoding")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid signature encoding") from error


def _verify_rsa(public_key: str, signature: str, data: bytes) -> None:
    pem = validate_public_key(public_key)
    raw = _base64(signature)
    if not 256 <= len(raw) <= 512:
        raise ValueError("Invalid RSA signature length")
    with tempfile.TemporaryDirectory(prefix="danmemo-signature-") as folder:
        public = Path(folder) / "public.pem"
        proof = Path(folder) / "signature.bin"
        public.write_text(pem, encoding="ascii")
        proof.write_bytes(raw)
        _openssl(
            [
                "dgst",
                "-sha1",
                "-verify",
                str(public),
                "-signature",
                str(proof),
                "-sigopt",
                "rsa_padding_mode:pkcs1",
            ],
            data,
        )


def parse_authorization(value: str) -> dict[str, str]:
    if (
        not isinstance(value, str)
        or len(value) > 16384
        or (not value.startswith("OAuth "))
    ):
        raise ValueError("Invalid OAuth Authorization")
    remaining = value[6:]
    result = {}
    while remaining:
        match = re.match(
            '([A-Za-z_][A-Za-z0-9_-]*)="([^"\\r\\n]*)"(?:,\\s*|$)', remaining
        )
        if not match:
            raise ValueError("Malformed OAuth Authorization")
        key, raw = match.group(1, 2)
        if key in result or re.search("%(?![0-9A-Fa-f]{2})", raw):
            raise ValueError("Duplicate or malformed OAuth parameter")
        try:
            result[key] = unquote(raw, encoding="utf-8", errors="strict")
        except UnicodeError as error:
            raise ValueError("Invalid OAuth encoding") from error
        remaining = remaining[match.end() :]
        if match.group(0).rstrip().endswith(",") and (not remaining):
            raise ValueError("Malformed OAuth Authorization")
    if not result:
        raise ValueError("Empty OAuth Authorization")
    return result


def sdk_signature_base(method: str, url: str, parameters: dict[str, str]) -> bytes:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.fragment
        or parsed.username
    ):
        raise ValueError("Invalid signing URL")
    authority = parsed.netloc
    try:
        if (parsed.scheme, parsed.port) in (("http", 80), ("https", 443)):
            authority = authority.rsplit(":", 1)[0]
    except ValueError as error:
        raise ValueError("Invalid signing URL") from error
    normalized = parsed.scheme + "://" + authority + (parsed.path or "/")
    values = dict(parameters)
    for pair in parsed.query.split("&") if parsed.query else []:
        key, separator, value = pair.partition("=")
        values[key] = value if separator else ""
    encode = lambda value: quote(value, safe="-._~", encoding="utf-8", errors="strict")
    pairs = "&".join(
        (
            encode(k) + "=" + encode(v)
            for k, v in sorted(values.items())
            if k != "oauth_signature"
        )
    )
    return "&".join((encode(x) for x in (method, normalized, pairs))).encode("ascii")


def _fresh(value: str, now: int | None) -> int:
    if not isinstance(value, str) or not re.fullmatch("[0-9]{1,12}", value):
        raise ValueError("Invalid signing timestamp")
    timestamp = int(value)
    if abs(timestamp - (int(time.time()) if now is None else now)) > 300:
        raise ValueError("Expired signing timestamp")
    return timestamp


def verify_sdk_request(
    method: str,
    url: str,
    body: bytes,
    authorization: str,
    public_key: str | None = None,
    credentials: AppCredentials | None = None,
    now: int | None = None,
) -> VerifiedOAuth:
    if not isinstance(body, bytes) or len(body) > MAX_BODY:
        raise ValueError("Invalid request entity")
    credentials = credentials or load_app_credentials()
    values = parse_authorization(authorization)
    required = {
        "oauth_body_hash",
        "oauth_consumer_key",
        "oauth_nonce",
        "oauth_timestamp",
        "oauth_signature_method",
        "oauth_version",
        "oauth_signature",
    }
    if not required <= values.keys() or values["oauth_version"] != "1.0":
        raise ValueError("Incomplete OAuth Authorization")
    if not hmac.compare_digest(
        values["oauth_consumer_key"].encode("utf-8"),
        credentials.consumer_key.encode("utf-8"),
    ):
        raise ValueError("Invalid OAuth consumer")
    timestamp = _fresh(values["oauth_timestamp"], now)
    nonce = values["oauth_nonce"]
    if not re.fullmatch("-?[0-9]{1,20}", nonce):
        raise ValueError("Invalid OAuth nonce")
    if not hmac.compare_digest(
        _base64(values["oauth_body_hash"]), hashlib.sha1(body).digest()
    ):
        raise ValueError("Invalid entity hash")
    query = {}
    for pair in urlsplit(url).query.split("&") if urlsplit(url).query else []:
        name, separator, value = pair.partition("=")
        query[name] = value if separator else ""
    reserved = required | {"xoauth_as_hash", "xoauth_requestor_id"}
    if reserved & query.keys():
        raise ValueError("Query collides with OAuth Authorization")
    if any((name in values and values[name] != value for name, value in query.items())):
        raise ValueError("Query conflicts with OAuth Authorization")
    canonical = sdk_signature_base(method, url, values)
    algorithm = values["oauth_signature_method"]
    if algorithm == "HMAC-SHA1":
        if method != "POST" or not urlsplit(url).path.endswith(
            ("/v1.0/auth/initialize", "/v1.0/migration/auth/initialize")
        ):
            raise ValueError("HMAC signing is restricted to initialization")
        if not values.keys() <= required | query.keys():
            raise ValueError("Unexpected initialize OAuth parameters")
        expected = hmac.new(
            credentials.secret.encode("utf-8"), canonical, hashlib.sha1
        ).digest()
        if not hmac.compare_digest(_base64(values["oauth_signature"]), expected):
            raise ValueError("Invalid HMAC signature")
        return VerifiedOAuth(None, timestamp, nonce)
    if algorithm != "RSA-SHA1" or not public_key:
        raise ValueError("Unsupported OAuth signing method or missing key")
    if not reserved <= values.keys() or not values.keys() <= reserved | query.keys():
        raise ValueError("Unexpected authenticated OAuth parameters")
    identity = values["xoauth_requestor_id"]
    if not re.fullmatch("[A-Za-z0-9_-]{1,128}", identity):
        raise ValueError("Invalid SDK UUID")
    _verify_rsa(
        public_key,
        values["xoauth_as_hash"],
        (credentials.secret + values["oauth_timestamp"]).encode("utf-8"),
    )
    _verify_rsa(public_key, values["oauth_signature"], canonical)
    return VerifiedOAuth(identity, timestamp, nonce)


def verify_game_request(
    body_json: bytes,
    headers,
    public_key: str,
    *,
    sdk_xuid: str,
    now: int | None = None,
    salt: bytes | None = None,
) -> VerifiedGame:
    """Verify plaintext JSON canonical bytes; caller handles transport AES first."""
    if not isinstance(body_json, bytes) or len(body_json) > MAX_BODY:
        raise ValueError("Invalid plaintext game entity")
    if not isinstance(sdk_xuid, str) or not re.fullmatch("[0-9]{1,20}", sdk_xuid):
        raise ValueError("Invalid SDK XUID")
    values = {}
    required = (
        "asset-version",
        "master-data-version",
        "user-data-version",
        "client-timestamp",
        "one-time-token",
        "has-root",
        "signature",
    )
    for name, value in headers.items():
        key = name.lower().removeprefix("x-argo-")
        if key not in required:
            continue
        if key in values:
            raise ValueError("Duplicate game signing header")
        values[key] = value
    if any((k not in values for k in required)):
        raise ValueError("Missing game signing header")
    if any(
        (
            not isinstance(values[k], str)
            or len(values[k]) > 8192
            or "\r" in values[k]
            or ("\n" in values[k])
            for k in required
        )
    ):
        raise ValueError("Invalid game signing header")
    timestamp = _fresh(values["client-timestamp"], now)
    if values["has-root"] not in ("0", "1"):
        raise ValueError("Invalid game root flag")
    if len(values["one-time-token"]) > 512:
        raise ValueError("Invalid game token length")
    canonical = (
        body_json
        + sdk_xuid.encode("ascii")
        + (load_game_salt() if salt is None else salt)
        + "".join((values[k] for k in required[:6])).encode("utf-8")
    )
    _verify_rsa(public_key, values["signature"], canonical)
    return VerifiedGame(timestamp, values["one-time-token"], values["signature"])
