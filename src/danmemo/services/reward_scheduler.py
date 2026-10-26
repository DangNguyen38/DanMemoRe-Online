"""Deliver earned mail to initialized accounts, including offline players."""

import json
from pathlib import Path
import threading
import time
from danmemo.services import journal as journal
from danmemo.services.game_workers import WorkerBusy as WorkerBusy


def sweep(gateway):
    with gateway.store.connect() as db:
        identifiers = [
            r["uid"] for r in db.execute("SELECT uid FROM accounts ORDER BY uid")
        ]
    cursor = getattr(gateway, "reward_cursor", -1)
    identifiers = [uid for uid in identifiers if uid > cursor] + [
        uid for uid in identifiers if uid <= cursor
    ]
    resident = getattr(gateway.workers, "resident_uids", lambda: set())()
    started = time.monotonic()
    cold_attempts = 0
    first_attempt = True
    delivered = 0
    for uid in identifiers:
        if getattr(gateway, "reward_stop", None) and gateway.reward_stop.is_set():
            break
        if time.monotonic() - started >= 5:
            break
        cold = uid not in resident
        if cold and cold_attempts >= 1:
            continue
        path = Path(gateway.workers.root) / str(uid) / "progress.json"
        try:
            data = json.loads(path.read_text("utf-8"))
            if not data.get("tables", {}).get("UserInfo"):
                continue
            account_lock = gateway.account_lock(uid)
            if not account_lock.acquire(blocking=False):
                continue
            try:
                if cold:
                    cold_attempts += 1
                if first_attempt:
                    gateway.reward_cursor = uid
                    first_attempt = False
                _, body = gateway.workers.call(
                    uid, "__reward_tick", b"", background=True
                )
            finally:
                account_lock.release()
            result = json.loads(body)
            delivered += bool(result.get("changed"))
        except WorkerBusy:
            continue
        except Exception as error:
            journal.emit("reward-scheduler", "reward/tick", uid=uid, error=error)
    return delivered


def start(gateway, interval=60):
    gateway.reward_stop = threading.Event()

    def loop():
        while not gateway.reward_stop.wait(interval):
            try:
                sweep(gateway)
            except Exception as error:
                journal.emit("reward-scheduler", "reward/sweep", error=error)

    gateway.reward_thread = threading.Thread(
        target=loop, name="game-reward-mail", daemon=True
    )
    gateway.reward_thread.start()


def stop(gateway):
    if hasattr(gateway, "reward_stop"):
        gateway.reward_stop.set()
        gateway.reward_thread.join(timeout=3)
