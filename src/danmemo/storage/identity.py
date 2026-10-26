"""Reconstructed identity storage; SDK authentication and native Import Code transfers."""

from __future__ import annotations
from danmemo import paths as _paths
import base64
import codecs
from contextlib import contextmanager
import hashlib
from pathlib import Path
import secrets
import sqlite3
import time
import uuid


def sdk_password(password: str) -> str:
    return codecs.encode(base64.b64encode(password.encode()).decode()[::-1], "rot_13")


def decode_sdk_password(value: str) -> str:
    try:
        return base64.b64decode(
            codecs.decode(value, "rot_13")[::-1], validate=True
        ).decode()
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid migration password encoding") from error


def password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32
    ).hex()


def valid_password(password: str) -> None:
    if not isinstance(password, str) or not 8 <= len(password) <= 64:
        raise ValueError("Password must contain 8-64 characters")


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class IdentityStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                (_paths.MIGRATIONS / "001_identity.sql").read_text("utf-8")
            )
            db.execute(
                "INSERT INTO sqlite_sequence(name,seq) SELECT 'accounts',2000000000\n                WHERE NOT EXISTS(SELECT 1 FROM sqlite_sequence WHERE name='accounts')"
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def throttle(self, bucket: str, limit: int = 10, window: int = 600) -> None:
        now = int(time.time())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM attempts WHERE bucket=?", (bucket,)
            ).fetchone()
            if row and now - row["start"] < window:
                if row["count"] >= limit:
                    raise ValueError("Too many attempts; try again later")
                db.execute(
                    "UPDATE attempts SET count=count+1 WHERE bucket=?", (bucket,)
                )
            else:
                db.execute(
                    "INSERT OR REPLACE INTO attempts VALUES(?,?,1)", (bucket, now)
                )
            db.execute(
                "DELETE FROM attempts WHERE start<?", (now - max(window, 86400),)
            )

    @staticmethod
    def public(row) -> dict:
        return {"userId": row["uid"], "name": row["name"], "uuid": row["uuid"]}

    @staticmethod
    def _session(db, row) -> str:
        token = secrets.token_urlsafe(32)
        db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?)",
            (digest(token), row["uid"], int(time.time()) + 86400, row["generation"]),
        )
        db.execute("DELETE FROM sessions WHERE expires<=?", (int(time.time()),))
        return token

    @staticmethod
    def _authenticated(db, token):
        row = db.execute(
            "SELECT a.* FROM accounts a JOIN sessions s ON a.uid=s.uid\n                WHERE s.hash=? AND s.expires>? AND s.generation=a.generation",
            (digest(token), int(time.time())),
        ).fetchone()
        if not row:
            raise ValueError("Session expired or invalid")
        return row

    def logout(self, token: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE hash=?", (digest(token),))

    def set_import(self, token: str, password: str, renew: bool = False) -> str:
        valid_password(password)
        salt = secrets.token_hex(16)
        hashed = password_hash(password, salt)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._authenticated(db, token)
            code = row["migration_code"]
            if renew or not code:
                code = "".join(
                    (
                        secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ")
                        for _ in range(16)
                    )
                )
            db.execute(
                "UPDATE accounts SET migration_code=?,migration_salt=?,migration_password=? WHERE uid=?",
                (code, salt, hashed, row["uid"]),
            )
            db.execute("DELETE FROM transfers WHERE uid=?", (row["uid"],))
            return code

    def import_code(self, token: str, renew: bool = False) -> str:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._authenticated(db, token)
            code = row["migration_code"]
            if renew or not code:
                code = "".join(
                    (
                        secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ")
                        for _ in range(16)
                    )
                )
                db.execute(
                    "UPDATE accounts SET migration_code=? WHERE uid=?",
                    (code, row["uid"]),
                )
                db.execute("DELETE FROM transfers WHERE uid=?", (row["uid"],))
            return code

    def verify_import(self, code: str, password: str) -> dict:
        valid_password(password)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM accounts WHERE migration_code=?", (code,)
            ).fetchone()
            hashed = password_hash(
                password,
                row["migration_salt"] if row and row["migration_salt"] else "00" * 16,
            )
            if (
                not row
                or not row["migration_password"]
                or (not secrets.compare_digest(hashed, row["migration_password"]))
            ):
                raise ValueError("Invalid import code or password")
            token = secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO transfers VALUES(?,?,?,?)",
                (digest(token), row["uid"], int(time.time()) + 300, row["generation"]),
            )
            db.execute("DELETE FROM transfers WHERE expires<=?", (int(time.time()),))
            return {
                "migration_token": token,
                "src_uuid": row["uuid"],
                "src_x_uid": str(row["uid"]),
            }

    def consume_nonce(self, scope: str, nonce: str, expires: int) -> None:
        if not isinstance(nonce, str) or not 1 <= len(nonce) <= 512:
            raise ValueError("Invalid signed nonce")
        try:
            with self.connect() as db:
                db.execute(
                    "DELETE FROM signed_nonces WHERE expires<=?", (int(time.time()),)
                )
                db.execute(
                    "INSERT INTO signed_nonces VALUES(?,?,?)", (scope, nonce, expires)
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("Signed request already used") from error

    def device(self, sdk_uuid: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT a.*,d.device_id,d.public_key FROM accounts a\n                JOIN devices d ON a.uuid=d.uuid WHERE a.uuid=? AND d.generation=a.generation",
                (sdk_uuid,),
            ).fetchone()
            if not row:
                raise ValueError("Unknown or revoked device")
            return {
                **self.public(row),
                "public_key": row["public_key"],
                "device_id": row["device_id"],
                "generation": row["generation"],
            }

    def initialize_device(self, device_id: str, public_key: str) -> dict:
        if not isinstance(device_id, str) or not 1 <= len(device_id) <= 256:
            raise ValueError("Invalid device ID")
        salt = secrets.token_hex(16)
        hashed = password_hash(secrets.token_urlsafe(32), salt)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT a.*,d.public_key FROM accounts a JOIN devices d\n                ON a.uuid=d.uuid WHERE d.device_id=? AND d.public_key=?\n                AND d.generation=a.generation",
                (device_id, public_key),
            ).fetchone()
            if existing:
                return self.public(existing)
            name = "device_" + secrets.token_hex(8)
            sdk_uuid = str(uuid.uuid4())
            now = int(time.time())
            db.execute(
                "INSERT INTO accounts(name,uuid,salt,password,created) VALUES(?,?,?,?,?)",
                (name, sdk_uuid, salt, hashed, now),
            )
            db.execute(
                "INSERT INTO devices VALUES(?,?,?,?,?)",
                (sdk_uuid, device_id, public_key, 0, now),
            )
            return self.public(
                db.execute(
                    "SELECT * FROM accounts WHERE uuid=?", (sdk_uuid,)
                ).fetchone()
            )

    def device_session(self, sdk_uuid: str) -> str:
        with self.connect() as db:
            row = db.execute(
                "SELECT a.* FROM accounts a JOIN devices d ON a.uuid=d.uuid\n                WHERE a.uuid=? AND d.generation=a.generation",
                (sdk_uuid,),
            ).fetchone()
            if not row:
                raise ValueError("Unknown or revoked device")
            return self._session(db, row)

    def transfer_device(
        self,
        token: str,
        source_uuid: str,
        destination_uuid: str,
        device_id: str,
        public_key: str,
    ) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            dst = db.execute(
                "SELECT d.* FROM devices d JOIN accounts a ON a.uuid=d.uuid\n                WHERE d.uuid=? AND d.generation=a.generation",
                (destination_uuid,),
            ).fetchone()
            if (
                not dst
                or dst["device_id"] != device_id
                or dst["public_key"] != public_key
            ):
                raise ValueError(
                    "Transfer destination does not match its signed device"
                )
            row = db.execute(
                "SELECT a.* FROM accounts a JOIN transfers t ON a.uid=t.uid\n                WHERE t.hash=? AND t.expires>? AND t.generation=a.generation AND a.uuid=?",
                (digest(token), int(time.time()), source_uuid),
            ).fetchone()
            if not row:
                raise ValueError("Import verification expired or already consumed")
            db.execute(
                "UPDATE accounts SET generation=generation+1 WHERE uid=?", (row["uid"],)
            )
            db.execute("DELETE FROM sessions WHERE uid=?", (row["uid"],))
            db.execute("DELETE FROM transfers WHERE uid=?", (row["uid"],))
            db.execute(
                "DELETE FROM devices WHERE uuid IN (?,?)",
                (source_uuid, destination_uuid),
            )
            db.execute(
                "INSERT INTO devices VALUES(?,?,?,?,?)",
                (
                    source_uuid,
                    device_id,
                    public_key,
                    row["generation"] + 1,
                    int(time.time()),
                ),
            )
            return self.public(row)
