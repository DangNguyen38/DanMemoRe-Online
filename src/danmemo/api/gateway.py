"""Local native account gateway; gameplay rules reuse reconstructed offline backends."""

from __future__ import annotations
import hashlib
import ipaddress
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit
import urllib.request
import zlib
from danmemo.game import engine as game
from danmemo.protocol import signing as signing
from danmemo.services.game_workers import (
    GameWorkers as GameWorkers,
    WorkerError as WorkerError,
)
from danmemo.storage.identity import (
    IdentityStore as IdentityStore,
    decode_sdk_password as decode_sdk_password,
    digest as digest,
)
from danmemo.api.http import Handler as AccountHandler
from danmemo.game.multiplayer import ROUTES as SHARED_ROUTES
from danmemo.services import journal as journal
from danmemo.content import catalogs as online_event_content

ARCHIVE_API_PREFIXES = (
    "/api/asset/manifest/",
    "/api/manifests/us/pkm/",
    "/api/contents/",
    "/api/resources/",
)


def game_action(request_path):
    """Specific native routes must win over shared suffixes such as familia/get."""
    path = urlsplit(request_path).path.rstrip("/")
    routes = (*game.API_ACTIONS, *SHARED_ROUTES, "user/migration/confirm")
    return max(
        (route for route in routes if path.endswith("/" + route)), key=len, default=None
    )


class Gateway:
    def __init__(self, data: Path, origin: str, content: str, trusted_proxy=False):
        self.store = IdentityStore(data / "identity.sqlite3")
        self.workers = GameWorkers(data / "accounts", origin)
        self.origin, self.content = (origin.rstrip("/"), content.rstrip("/"))
        self.trusted_proxy = trusted_proxy
        online_event_content.CONTENT_ORIGIN = self.content
        self.credentials = signing.load_app_credentials()
        self.salt = signing.load_game_salt()
        self.locks, self.lock = ({}, threading.Lock())
        with self.store.connect() as db:
            db.executescript(
                "\n                CREATE TABLE IF NOT EXISTS game_tokens (\n                    uid INTEGER PRIMARY KEY, generation INTEGER NOT NULL,\n                    token TEXT NOT NULL, previous TEXT);\n                CREATE TABLE IF NOT EXISTS game_replies (\n                    uid INTEGER NOT NULL, generation INTEGER NOT NULL, fingerprint TEXT NOT NULL,\n                    path TEXT NOT NULL, content_type TEXT NOT NULL, body BLOB NOT NULL,\n                    token TEXT NOT NULL, expires INTEGER NOT NULL,\n                    PRIMARY KEY(uid,generation,fingerprint));\n                CREATE TABLE IF NOT EXISTS migration_previews (\n                    actor TEXT PRIMARY KEY, source INTEGER NOT NULL, expires INTEGER NOT NULL);\n                CREATE INDEX IF NOT EXISTS game_replies_expiry ON game_replies(expires);\n            "
            )
        from danmemo.services import reward_scheduler as online_reward_scheduler

        online_reward_scheduler.start(self)

    def account_lock(self, uid):
        with self.lock:
            return self.locks.setdefault(uid, threading.RLock())

    def close(self):
        from danmemo.services import reward_scheduler as online_reward_scheduler

        online_reward_scheduler.stop(self)
        self.workers.close()

    def sdk(self, method, path, body, authorization, payment_header=None):
        values = signing.parse_authorization(authorization)
        actor = values.get("xoauth_requestor_id")
        device = self.store.device(actor) if actor else None
        verified = signing.verify_sdk_request(
            method,
            self.origin + path,
            body,
            authorization,
            device["public_key"] if device else None,
            self.credentials,
        )
        self.store.consume_nonce(
            "sdk:" + (actor or "initialize"), verified.nonce, verified.timestamp + 301
        )
        route = urlsplit(path).path
        request = json.loads(body) if body else {}
        if not isinstance(request, dict):
            raise ValueError("Invalid SDK entity")
        if route in (
            "/billing/v1.0/auth/initialize",
            "/billing/v1.0/migration/auth/initialize",
        ):
            if verified.uuid is not None:
                raise ValueError("Initialization requires application signing")
            key = signing.validate_public_key(request.get("token"))
            account = self.store.initialize_device(request.get("device_id"), key)
            return {"result": "OK", "uuid": account["uuid"]}
        if not device or verified.uuid != device["uuid"]:
            raise ValueError("Authenticated device required")
        if route == "/billing/v1.0/auth/authorize":
            return {"result": "OK"}
        if route == "/billing/v1.0/auth/x_uid":
            return {
                "result": "OK",
                "x_uid": str(device["userId"]),
                "x_app_id": self.credentials.consumer_key,
            }
        if route == "/billing/v1.0/migration/code":
            if method != "GET":
                raise ValueError("Invalid SDK method")
            renew = parse_qs(urlsplit(path).query).get("renew", ["0"])[0]
            if renew not in ("0", "1"):
                raise ValueError("Invalid renewal flag")
            token = self.store.device_session(actor)
            try:
                return {
                    "result": "OK",
                    "migration_code": self.store.import_code(token, renew == "1"),
                }
            finally:
                self.store.logout(token)
        if route == "/billing/v1.0/migration/password/register":
            token = self.store.device_session(actor)
            try:
                self.store.set_import(
                    token, decode_sdk_password(request.get("migration_password", ""))
                )
                return {"result": "OK"}
            finally:
                self.store.logout(token)
        if route == "/billing/v1.0/migration/code/verify":
            result = self.store.verify_import(
                request.get("migration_code"),
                decode_sdk_password(request.get("migration_password", "")),
            )
            with self.store.connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO migration_previews VALUES(?,?,?)",
                    (actor, int(result["src_x_uid"]), int(time.time()) + 300),
                )
            progress = self.workers.root / result["src_x_uid"] / "progress.json"
            saved = (
                json.loads(progress.read_text("utf-8"))
                if progress.exists()
                else {"tables": {}}
            )
            free = next(
                (
                    r["amount"]
                    for r in saved["tables"].get("UserCurrency", [])
                    if r["currencyId"] == 205000001
                ),
                0,
            )
            return {
                "result": "OK",
                **result,
                "balance_charge_gem": 0,
                "balance_free_gem": free,
                "balance_total_gem": free,
            }
        if route == "/billing/v1.0/migration":
            if request.get("dst_uuid", actor) != actor:
                raise ValueError("Invalid destination UUID")
            self.store.transfer_device(
                request.get("migration_token", ""),
                request.get("src_uuid"),
                actor,
                request.get("device_id"),
                signing.validate_public_key(request.get("token")),
            )
            with self.store.connect() as db:
                db.execute("DELETE FROM migration_previews WHERE actor=?", (actor,))
            return {"result": "OK"}
        if route == "/billing/v1.0/moderate/keywordlist":
            return {
                "result": "OK",
                "entry": {
                    "timestamp": "0",
                    "keywords": [
                        {
                            "id": 0,
                            "type": 0,
                            "rank": 0,
                            "keyword": "__danmemore_never_match__",
                        }
                    ],
                },
            }
        if route == "/billing/v1.0/moderate/filtering/commit":
            if method != "POST" or not isinstance(request.get("results"), list):
                raise ValueError("Invalid moderation report")
            if len(request["results"]) > 1000 or not all(
                (isinstance(row, dict) for row in request["results"])
            ):
                raise ValueError("Invalid moderation report")
            return {"result": "OK"}
        if route in (
            "/billing/v1.0/payment/productlist",
            "/billing/v1.0/payment/subscription/productlist",
        ):
            if route.endswith("/payment/productlist"):
                from danmemo.game import private_purchase as private_purchase

                return dict(
                    result="OK",
                    entry=dict(
                        products=private_purchase.products()
                        if private_purchase.unlocked()
                        else [],
                        welcome=0,
                    ),
                )
            return {"result": "OK", "entry": {"products": []}}
        if route in (
            "/billing/v1.0/payment/purchase",
            "/billing/v1.0/payment/purchase/alert/setting",
            "/billing/v1.0/payment/purchase/alert/setting/update",
        ):
            from danmemo.game import private_purchase as private_purchase

            token = (
                private_purchase.payment_token(payment_header)
                if route.endswith("/purchase")
                else None
            )
            with self.account_lock(device["userId"]):
                _, response = self.workers.call(
                    device["userId"],
                    "__billing",
                    json.dumps(
                        dict(method=method, path=route, request=request, token=token)
                    ).encode(),
                )
            value = json.loads(response)
            if "error" in value:
                raise ValueError(value["error"])
            return value
        if route == "/billing/v1.0/payment/subscription/status":
            return {"result": "OK", "entry": []}
        if route == "/billing/v1.0/payment/subscription/history":
            return {
                "result": "OK",
                "entry": {
                    "offset": 0,
                    "limit": 0,
                    "has_next": False,
                    "subscriptions": [],
                },
            }
        if route in ("/billing/v1.0/payment/balance", "/billing/v1.0/payment/history"):
            with self.account_lock(device["userId"]):
                _, response = self.workers.call(
                    device["userId"],
                    "__billing",
                    json.dumps(
                        dict(
                            method=method,
                            path=route,
                            query=parse_qs(urlsplit(path).query),
                        )
                    ).encode(),
                )
            value = json.loads(response)
            if "error" in value:
                raise ValueError(value["error"])
            return value
        raise ValueError("Unsupported SDK route")

    def game_request(self, path, body, headers):
        action = game_action(path)
        if not action:
            raise ValueError("Unknown game route")
        user = headers.get("X-ARGO-USER", "")
        if not user.isdecimal():
            raise ValueError("Missing account identity")
        with self.store.connect() as db:
            row = db.execute(
                "SELECT uuid FROM accounts WHERE uid=?", (int(user),)
            ).fetchone()
        if not row:
            raise ValueError("Unknown account identity")
        device = self.store.device(row["uuid"])
        if not body and action in (
            "matching_user/game_user_id",
            "user/login",
            "user_data/confirm",
            "user/migration/confirm",
            "familia/search/random",
            "familia/invited/list",
            "familia/join/list/sent",
            "friend/support/list",
            "familia/skill/get",
            "achievement/familia/get",
            "familia/mystatus",
            "familia/leave",
            "familia/break",
        ):
            plaintext = b""
        elif action in (
            "matching_user/game_user_id",
            "user/login",
            "user/migration/confirm",
        ):
            if body.lstrip().startswith(b"{"):
                if len(body) > 16384 or not isinstance(json.loads(body), dict):
                    raise ValueError("Invalid matching entity")
                plaintext = body
            else:
                try:
                    plaintext = inflate(body)
                except zlib.error:
                    plaintext = decrypt(body)
        else:
            plaintext = decrypt(body)
        verified = signing.verify_game_request(
            plaintext, headers, device["public_key"], sdk_xuid=user, salt=self.salt
        )
        fingerprint = digest(verified.signature + hashlib.sha256(plaintext).hexdigest())
        uid, generation = (device["userId"], device["generation"])
        with self.account_lock(uid):
            with self.store.connect() as db:
                prior = db.execute(
                    "SELECT * FROM game_replies WHERE uid=? AND generation=? AND fingerprint=?",
                    (uid, generation, fingerprint),
                ).fetchone()
                if prior:
                    if prior["path"] != path:
                        raise ValueError("Signed request reused on another endpoint")
                    return (prior["content_type"], prior["body"], prior["token"], uid)
                session = db.execute(
                    "SELECT * FROM game_tokens WHERE uid=? AND generation=?",
                    (uid, generation),
                ).fetchone()
            valid_tokens = (
                {session["token"], session["previous"]}
                if session
                else {game.LOCAL_TOKEN, "0", ""}
            )
            if action == "matching_user/game_user_id":
                valid_tokens.update((game.LOCAL_TOKEN, "0", ""))
            if verified.nonce not in valid_tokens:
                raise ValueError("Game token expired")
            request_id = (
                headers.get("X-ARGO-REQUEST-ID", "")
                + ":"
                + headers.get("X-ARGO-REQUEST-SEQUENCE", "")
            )
            if action == "user/migration/confirm":
                content_type, response = (
                    "application/json",
                    self.migration_preview(device, json.loads(plaintext)),
                )
            else:
                from danmemo.game.familia_economy import MUTATIONS as FAMILIA_MUTATIONS

                receipt_id = (
                    fingerprint
                    if action
                    in (
                        "adventure/stage/complete",
                        "pve/stage/complete",
                        "pvb/stage/complete",
                        *FAMILIA_MUTATIONS,
                    )
                    else request_id or fingerprint
                )
                journal.emit(
                    "online-authenticated",
                    action,
                    uid=uid,
                    request_id=request_id,
                    receipt_id=receipt_id,
                    request=json.loads(plaintext) if plaintext else {},
                )
                content_type, response = self.workers.call(
                    uid, action, body, receipt_id
                )
            if self.store.device(device["uuid"])["generation"] != generation:
                raise ValueError("Device revoked during request")
            token = secrets.token_hex(16)
            with self.store.connect() as db:
                db.execute(
                    "DELETE FROM game_replies WHERE expires<=?", (int(time.time()),)
                )
                db.execute(
                    "INSERT INTO game_replies VALUES(?,?,?,?,?,?,?,?)",
                    (
                        uid,
                        generation,
                        fingerprint,
                        path,
                        content_type,
                        response,
                        token,
                        int(time.time()) + 600,
                    ),
                )
                db.execute(
                    "INSERT OR REPLACE INTO game_tokens VALUES(?,?,?,?)",
                    (
                        uid,
                        generation,
                        token,
                        session["token"] if session else game.LOCAL_TOKEN,
                    ),
                )
            return (content_type, response, token, uid)

    def migration_preview(self, device, request):
        try:
            uid = int(request["userId"])
        except (KeyError, ValueError, TypeError):
            raise ValueError("Invalid transfer preview") from None
        with self.store.connect() as db:
            row = db.execute(
                "SELECT a.* FROM accounts a JOIN migration_previews p ON a.uid=p.source\n                WHERE p.actor=? AND p.source=? AND p.expires>?",
                (device["uuid"], uid, int(time.time())),
            ).fetchone()
        if not row:
            raise ValueError("No verified transfer preview")
        progress = self.workers.root / str(uid) / "progress.json"
        saved = (
            json.loads(progress.read_text("utf-8"))
            if progress.exists()
            else {"tables": {}}
        )
        name = next(
            (r.get("name") for r in saved["tables"].get("UserInfo", [])), row["name"]
        )
        return json.dumps(
            {
                "code": 0,
                "gamelibUser": {"createdAt": row["created"]},
                "userMeta": {
                    "updatedAt": int(progress.stat().st_mtime)
                    if progress.exists()
                    else row["created"]
                },
                "userInfo": {"name": name, "userId": uid},
            }
        ).encode()


def decrypt(body):
    if not body or len(body) % 16:
        raise ValueError("Invalid game ciphertext")
    decrypted = signing._openssl(
        [
            "enc",
            "-d",
            "-aes-256-cbc",
            "-nopad",
            "-K",
            game.LOCAL_AES_KEY.hex(),
            "-iv",
            game.LOCAL_AES_IV.encode().hex(),
        ],
        body,
    )
    return inflate(decrypted)


def inflate(decrypted):
    stream = zlib.decompressobj()
    plaintext = stream.decompress(decrypted, 16 * 1024 * 1024 + 1)
    if len(plaintext) > 16 * 1024 * 1024 or not stream.eof:
        raise ValueError("Game entity too large or truncated")
    if not isinstance(json.loads(plaintext), dict):
        raise ValueError("Invalid game entity")
    return plaintext


class Handler(AccountHandler):
    def sdk_peer(self):
        peer = self.client_address[0]
        if self.gateway.trusted_proxy and ipaddress.ip_address(peer).is_loopback:
            forwarded = self.headers.get_all("X-Real-IP", [])
            if len(forwarded) == 1:
                try:
                    return str(ipaddress.ip_address(forwarded[0]))
                except ValueError:
                    pass
        return peer

    def event(self, status, reason="", error=None):
        raw_uid = self.headers.get("X-ARGO-USER", "")
        raw_timestamp = self.headers.get("X-ARGO-CLIENT-TIMESTAMP", "")
        client_timestamp = (
            int(raw_timestamp)
            if raw_timestamp.isdecimal() and len(raw_timestamp) <= 20
            else None
        )
        journal.emit(
            "online-http",
            urlsplit(self.path).path,
            uid=int(raw_uid) if raw_uid.isdecimal() and len(raw_uid) <= 20 else None,
            request_id=self.headers.get("X-ARGO-REQUEST-ID", "")
            + ":"
            + self.headers.get("X-ARGO-REQUEST-SEQUENCE", ""),
            status=status,
            error=error,
            duration_ms=(time.perf_counter() - self.request_started) * 1000
            if hasattr(self, "request_started")
            else None,
        )
        print(
            json.dumps(
                {
                    "method": self.command,
                    "path": urlsplit(self.path).path,
                    "queryKeys": sorted(parse_qs(urlsplit(self.path).query)),
                    "bodyBytes": getattr(self, "request_size", 0),
                    "clientTimestamp": client_timestamp,
                    "clockDeltaSeconds": int(time.time()) - client_timestamp
                    if client_timestamp is not None
                    else None,
                    "status": status,
                    "reason": reason,
                }
            ),
            flush=True,
        )

    def __init__(self, *args, gateway, **kwargs):
        self.gateway = gateway
        super().__init__(
            *args,
            store=gateway.store,
            origin=gateway.origin,
            secure_cookie=False,
            **kwargs,
        )

    def read_body(self):
        if (
            self.headers.get("Transfer-Encoding")
            or len(self.headers.get_all("Content-Length", [])) > 1
        ):
            raise ValueError("Invalid body framing")
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 <= size <= 16 * 1024 * 1024:
            raise ValueError("Invalid body size")
        self.connection.settimeout(15)
        body = self.rfile.read(size)
        self.request_size = len(body)
        if len(body) != size:
            raise ValueError("Truncated body")
        return body

    def do_GET(self):
        self.request_started = time.perf_counter()
        path = urlsplit(self.path).path
        if path == "/health":
            self.respond(
                {
                    "status": "ok",
                    "service": "local-native-gateway",
                    "nativeGameReady": False,
                    "nativeAdapterImplemented": True,
                    "nativeClientVerified": False,
                    "sharedMultiplayer": True,
                    "multiplayerMode": "asynchronous-player-snapshots",
                    "authoritativeCombat": False,
                }
            )
        elif path.startswith("/billing/"):
            self.sdk_request()
        elif (
            online_event_content.content_path(self.path) is not None
            or path.startswith("/content/")
            or path == "/storage/eu/status.json"
            or path.startswith(ARCHIVE_API_PREFIXES)
        ):
            self.content_request()
        else:
            super().do_GET()

    def do_HEAD(self):
        if (
            online_event_content.content_path(self.path) is not None
            or urlsplit(self.path).path.startswith("/content/")
            or urlsplit(self.path).path.startswith(ARCHIVE_API_PREFIXES)
        ):
            self.content_request(head=True)
        else:
            self.send_error(404)

    def do_POST(self):
        self.request_started = time.perf_counter()
        path = urlsplit(self.path).path
        if path.startswith("/billing/"):
            self.sdk_request()
        elif (
            path.startswith(("/api/", "/danmemore-local/"))
            or game.api_action(path) is not None
            or any((path.endswith("/" + route) for route in SHARED_ROUTES))
        ):
            try:
                body = self.read_body()
                if len(self.headers.get_all("X-ARGO-SIGNATURE", [])) != 1:
                    raise ValueError("Duplicate or missing signature")
                if len(self.headers.get_all("X-ARGO-USER", [])) != 1:
                    raise ValueError("Duplicate or missing identity")
                content_type, result, token, uid = self.gateway.game_request(
                    self.path, body, self.headers
                )
                self.send_response(200)
                response_headers = {
                    "Content-Type": content_type,
                    "Content-Length": str(len(result)),
                    "Cache-Control": "no-store",
                    "X-ARGO-ENCRYPTION": "0",
                    "X-ARGO-ONE-TIME-TOKEN": token,
                    "X-ARGO-USER": str(uid),
                    "X-ARGO-REQUEST-ID": self.headers.get("X-ARGO-REQUEST-ID", "0"),
                    "X-ARGO-REQUEST-SEQUENCE": self.headers.get(
                        "X-ARGO-REQUEST-SEQUENCE", "0"
                    ),
                    "X-ARGO-REQUEST-BODY-HASH": self.headers.get(
                        "X-ARGO-REQUEST-BODY-HASH", hashlib.md5(body).hexdigest()
                    ),
                    "X-ARGO-SERVER-RESPONSE-CODE": "0",
                    "X-ARGO-SERVER-TIMESTAMP": str(int(time.time())),
                    "X-ARGO-ACCEPT-TIMESTAMP": str(int(time.time())),
                    "X-ARGO-SERVER-VERSION": "DanMemoRe-local-online/1",
                }
                for key, value in response_headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(result)
                self.event(200)
            except (ValueError, zlib.error, WorkerError) as error:
                self.event(
                    401 if not isinstance(error, WorkerError) else 503,
                    str(error),
                    error=error,
                )
                self.respond(
                    {"code": 1, "message": str(error)},
                    401 if not isinstance(error, WorkerError) else 503,
                )
            except (OSError, TimeoutError) as error:
                self.event(499, "Game response connection interrupted", error=error)
                self.close_connection = True
        elif path.startswith("/telemetry/"):
            self.read_body()
            self.respond({})
        else:
            self.event(404, "Unknown POST route")
            super().do_POST()

    def sdk_request(self):
        try:
            if len(self.headers.get_all("Authorization", [])) != 1:
                raise ValueError("Exactly one OAuth Authorization required")
            body = self.read_body()
            self.store.throttle("sdk:" + self.sdk_peer(), 240, 60)
            result = self.gateway.sdk(
                self.command,
                self.path,
                body,
                self.headers["Authorization"],
                self.headers.get("X-GREE-GAMELIB"),
            )
            self.event(200)
            self.respond(result)
        except (ValueError, TypeError, zlib.error) as error:
            self.event(401, str(error))
            self.respond({"result": "ERROR", "code": 401, "message": str(error)}, 401)

    def content_request(self, head=False):
        local = online_event_content.content_path(self.path)
        if local:
            size = local.stat().st_size
            try:
                start, end, partial = game.byte_range(self.headers.get("Range"), size)
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if not head:
                with local.open("rb") as stream:
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = stream.read(min(128 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            return
        req = urllib.request.Request(
            self.gateway.content + self.path,
            headers={"Range": self.headers["Range"]}
            if self.headers.get("Range")
            else {},
            method="HEAD" if head else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                self.send_response(response.status)
                for key in (
                    "Content-Type",
                    "Content-Length",
                    "Content-Range",
                    "Accept-Ranges",
                ):
                    if response.headers.get(key):
                        self.send_header(key, response.headers[key])
                self.end_headers()
                if not head:
                    while chunk := response.read(128 * 1024):
                        self.wfile.write(chunk)
        except urllib.error.HTTPError as error:
            self.event(error.code, "Asset origin rejected request", error=error)
            self.send_error(error.code)
        except (OSError, TimeoutError):
            self.event(503, "Asset origin unavailable or timed out")
            self.send_error(503)


class GameHTTPServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True
