"""Spawn isolated native-game backends without selecting offline accounts."""

from __future__ import annotations
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import threading
import time
import traceback
from urllib.parse import urlsplit
from danmemo.game.engine import API_ACTIONS as API_ACTIONS
from danmemo.game.multiplayer import ROUTES as SHARED_ROUTES
from danmemo.services import journal as journal

CALL_TIMEOUT = 60.0
MAX_ENTITY = 64 * 1024 * 1024
INTERNAL_ACTIONS = ("__reward_tick", "__billing")


def merge_live_tables(response, snapshot, sent_tokens):
    """Send changed mailbox/mission tables without resending every screen."""
    for table in ("UserGift", "UserAchievement"):
        token = snapshot.get("tokens", {}).get(table, "")
        if table in snapshot.get("tables", {}) and sent_tokens.get(table) != token:
            response.setdefault("data", {})[table] = snapshot["tables"][table]
            response.setdefault("dataTokens", {})[table] = token


def remember_live_tables(response, sent_tokens):
    for table in ("UserGift", "UserAchievement"):
        if table in response.get("data", {}) and table in response.get(
            "dataTokens", {}
        ):
            sent_tokens[table] = response["dataTokens"][table]


def ensure_initial_party(oc) -> None:
    """Pair the compatibility client's initial party ID with an empty party."""
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        if state["tables"].get("UserParty"):
            return
        info = next(iter(state["tables"].get("UserInfo", [])), {})
        if info.get("activePartyId", 0) not in (0, 1):
            return
        party = {
            "userId": oc.LOCAL_USER_ID,
            "id": 1,
            "name": "",
            "attackPattern": 2,
            "isCommandFlowActive": False,
            "slots": [
                {
                    "slotNumber": slot,
                    "type": 0,
                    "pcId": 0,
                    "assistingPcId": 0,
                    "hp": 0,
                    "mp": 0,
                    "isChangedPlaceWithSub": False,
                    "commandFlow": "",
                    "additionalEffects": [],
                }
                for slot in range(1, 7)
            ],
            "memoriaSlots": [
                {"slotNumber": slot, "memoriaId": 0} for slot in range(1, 4)
            ],
        }
        state["tables"]["UserParty"] = [party]
        state["tokens"]["UserParty"] = hashlib.md5(
            oc.msgpack_value([party])
        ).hexdigest()
        oc.write_progress(state)


def ensure_prologue_leader(oc) -> None:
    """Restore the starter leader only in an otherwise empty prologue party."""
    with oc.PROGRESS_LOCK:
        state = oc.load_progress()
        info = next(iter(state["tables"].get("UserInfo", [])), {})
        if info.get("activePartyId", 0) not in (0, 1) or info.get("position", {}).get(
            "areaId", 0
        ) not in (0, 511011004):
            return
        party = next(
            (p for p in state["tables"].get("UserParty", []) if p["id"] == 1), None
        )
        if party is None or any((s.get("pcId") for s in party.get("slots", []))):
            return
        starters = oc.starter_pcs()
        starter_ids = {p["pcId"] for p in starters}
        pcs = state["tables"].get("UserPC", [])
        if any((p["pcId"] not in starter_ids for p in pcs)):
            return
        known = {p["pcId"]: p for p in pcs}
        for pc in starters:
            known.setdefault(pc["pcId"], pc)
        bell = known[1011001001]
        slot = next((s for s in party["slots"] if s["slotNumber"] == 1), None)
        if slot is None:
            raise ValueError("Initial party is missing its first slot")
        slot.update(
            pcId=bell["pcId"],
            assistingPcId=1012027001,
            hp=bell["hp"],
            mp=bell["mp"],
            type=1,
        )
        state["tables"]["UserPC"] = list(known.values())
        for table in ("UserPC", "UserParty"):
            state["tokens"][table] = hashlib.md5(
                oc.msgpack_value(state["tables"][table])
            ).hexdigest()
        oc.write_progress(state)


class WorkerError(RuntimeError):
    """Bounded gateway-facing failure without backend request or account data."""


class WorkerBusy(WorkerError):
    """No safe capacity for a background tick; retry on the next sweep."""


def _backend(connection, root: str, endpoint: str, uid: int) -> None:
    with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
        try:
            from danmemo.game import engine as oc
            from danmemo.game.multiplayer import (
                SharedWorld as SharedWorld,
                ROUTES as ROUTES,
            )

            base = Path(root).resolve()
            folder = (base / str(uid)).resolve()
            if base not in folder.parents:
                raise ValueError("Invalid account folder")
            folder.mkdir(parents=True, exist_ok=True)
            oc.LOCAL_USER_ID = uid
            oc.PROGRESS_STATE = folder / "progress.json"
            oc.TUTORIAL_DRAW_STATE = folder / "tutorial-draw.json"
            oc.ENDPOINT = endpoint
            oc.ONLINE_SHARED = SharedWorld(base / "multiplayer.sqlite3", uid)
            if not oc.PROGRESS_STATE.exists():
                oc.write_progress({"tables": {}, "tokens": {}, "completed": {}})
            admin = oc.schedule_module()
            if admin.CONFIG.is_file():
                shared = json.loads(admin.CONFIG.read_text("utf-8"))
                oc.ENABLED_LOTTERIES = tuple(
                    (int(value) for value in shared.get("banners", {}))
                )
            else:
                defaults = {
                    "banners": {
                        str(value): {"start": 1, "end": 2114356592}
                        for value in admin.DEFAULT_BANNERS
                    },
                    "events": {
                        str(value): {"start": 1, "end": 2114356592}
                        for value in admin.DEFAULT_EVENTS
                    },
                }
                admin.config = lambda: json.loads(json.dumps(defaults))
            connection.send(("ready",))
            sent_tokens = {}
            while True:
                request = connection.recv()
                if request is None:
                    break
                action, body, request_id = request
                started = time.perf_counter()
                decoded = {}
                if (
                    action not in (*oc.API_ACTIONS, *ROUTES, *INTERNAL_ACTIONS)
                    or not isinstance(body, bytes)
                    or len(body) > MAX_ENTITY
                ):
                    connection.send(("error", "Invalid game request"))
                    continue
                try:
                    from danmemo.game.familia_economy import recovery as recovery

                    recovery(oc.ONLINE_SHARED, oc)
                    from danmemo.game.familia_raid import recovery as raid_recovery

                    raid_recovery(oc.ONLINE_SHARED, oc)
                    from danmemo.game.union_raid import recovery as union_recovery

                    union_recovery(oc.ONLINE_SHARED, oc)
                    from danmemo.game.campaign_features import (
                        recovery as election_recovery,
                    )

                    election_recovery(oc.ONLINE_SHARED, oc)
                    if action in INTERNAL_ACTIONS:
                        try:
                            if action == "__billing":
                                from danmemo.game import (
                                    private_purchase as private_purchase,
                                )

                                value = private_purchase.dispatch(
                                    oc, **json.loads(body)
                                )
                            elif action == "__reward_tick":
                                from danmemo.game.reward_delivery import (
                                    run as deliver_rewards,
                                )

                                value = dict(code=0, changed=deliver_rewards(oc))
                        except (ValueError, KeyError, FileNotFoundError) as error:
                            value = {"error": str(error)}
                        connection.send(
                            ("ok", "application/json", json.dumps(value).encode())
                        )
                        journal.emit(
                            "online-admin",
                            action,
                            uid=uid,
                            request_id=request_id,
                            duration_ms=(time.perf_counter() - started) * 1000,
                            response={"code": 1 if "error" in value else 0},
                        )
                        continue
                    if action == "user/login":
                        for name, prepare in (
                            ("initial-party", lambda: ensure_initial_party(oc)),
                            ("prologue-leader", lambda: ensure_prologue_leader(oc)),
                            ("battle-auto", oc.repair_battle_auto_default),
                            ("stage-migration", oc.migrate_stage_progress),
                            ("stage-unlocks", oc.repair_completed_stage_unlocks),
                            ("gacha-guarantees", oc.repair_lottery_guarantees),
                        ):
                            step_start = time.perf_counter()
                            journal.emit("online-prepare", name + "/begin", uid=uid)
                            prepare()
                            journal.emit(
                                "online-prepare",
                                name + "/end",
                                uid=uid,
                                duration_ms=(time.perf_counter() - step_start) * 1000,
                            )
                    decoded = (
                        oc.decode_request(body)
                        if body
                        and action
                        not in (
                            "matching_user/game_user_id",
                            "user/login",
                            "user_data/confirm",
                        )
                        else {}
                    )
                    if action == "user/login":
                        sent_tokens.clear()
                    from danmemo.game.reward_delivery import run as deliver_rewards

                    _, snapshot = deliver_rewards(oc, return_state=True)
                    oc.ONLINE_SHARED.publish(snapshot, decoded)
                    shared_response = oc.ONLINE_SHARED.dispatch(
                        action, decoded, oc, request_id
                    )
                    if shared_response is None:
                        confirming = action == "user_data/confirm"
                        content_type, response = oc.api_response(
                            action,
                            body,
                            request_id,
                            reward_preflight=False,
                            progress_snapshot=snapshot if confirming else None,
                        )
                        if not confirming:
                            snapshot = oc.load_progress()
                    else:
                        from danmemo.game.reward_delivery import run as deliver_rewards

                        _, snapshot = deliver_rewards(oc, return_state=True)
                        merge_live_tables(shared_response, snapshot, sent_tokens)
                        content_type, response = (
                            "application/json",
                            json.dumps(shared_response, separators=(",", ":")).encode(),
                        )
                    oc.ONLINE_SHARED.publish(snapshot, decoded)
                    if (
                        not isinstance(content_type, str)
                        or len(content_type) > 128
                        or (not isinstance(response, bytes))
                        or (len(response) > MAX_ENTITY)
                    ):
                        raise ValueError("Invalid backend response")
                    connection.send(("ok", content_type, response))
                    if content_type == "application/json":
                        remember_live_tables(json.loads(response), sent_tokens)
                    journal.emit(
                        "online-backend",
                        action,
                        uid=uid,
                        request_id=request_id,
                        duration_ms=(time.perf_counter() - started) * 1000,
                        request=decoded,
                        response=journal.response_summary(response),
                        native=dict(
                            wireBytes=len(response), tables=decoded.get("tables", [])
                        )
                        if action == "user_data/pull"
                        else None,
                    )
                except Exception as error:
                    journal.emit(
                        "online-backend",
                        action,
                        uid=uid,
                        request_id=request_id,
                        duration_ms=(time.perf_counter() - started) * 1000,
                        request=decoded,
                        error=error,
                    )
                    frames = traceback.extract_tb(error.__traceback__)
                    diagnostic = dict(
                        time=int(time.time()),
                        action=action,
                        errorType=type(error).__name__,
                        frames=[
                            dict(
                                file=Path(frame.filename).name,
                                line=frame.lineno,
                                function=frame.name,
                            )
                            for frame in frames[-8:]
                        ],
                    )
                    try:
                        with (folder / "backend-errors.jsonl").open(
                            "a", encoding="utf-8"
                        ) as output:
                            output.write(json.dumps(diagnostic) + "\n")
                    except OSError:
                        pass
                    connection.send(("error", "Game backend rejected the request"))
        except (EOFError, BrokenPipeError, OSError):
            pass
        except Exception:
            try:
                connection.send(("error", "Game backend initialization failed"))
            except (EOFError, BrokenPipeError, OSError):
                pass
        finally:
            connection.close()


@dataclass
class _Worker:
    process: object
    connection: object
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop_lock: threading.Lock = field(default_factory=threading.Lock)
    active: int = 0
    last_used: float = field(default_factory=time.monotonic)
    ready: bool = False


class GameWorkers:
    def __init__(self, root: Path, endpoint: str, max_workers: int | None = None):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.username
            or parsed.query
            or parsed.fragment
            or (parsed.path not in ("", "/"))
        ):
            raise ValueError("Expected the gateway origin URL")
        if max_workers is None:
            try:
                max_workers = int(os.environ.get("DANMEMO_GAME_WORKERS", "8"))
            except ValueError:
                raise ValueError("Invalid backend worker limit") from None
        if type(max_workers) is not int or not 1 <= max_workers <= 64:
            raise ValueError("Invalid backend worker limit")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.endpoint = endpoint.rstrip("/")
        self.max_workers = max_workers
        self._context = multiprocessing.get_context("spawn")
        self._workers: dict[int, _Worker] = {}
        self._lock = threading.RLock()
        self._capacity = threading.Condition(self._lock)
        self._closed = False

    @staticmethod
    def _stop(worker: _Worker, graceful: bool = False) -> None:
        with worker.stop_lock:
            process = worker.process
            if graceful and process.is_alive():
                try:
                    worker.connection.send(None)
                    process.join(1)
                except (EOFError, BrokenPipeError, OSError):
                    pass
            if process.is_alive():
                process.terminate()
                process.join(2)
            if process.is_alive():
                process.kill()
                process.join(2)
            worker.connection.close()
            process.join(0)

    def resident_uids(self):
        """Snapshot used to budget background work without starting backends."""
        with self._lock:
            return {
                uid
                for uid, worker in self._workers.items()
                if worker.process.is_alive()
            }

    def _reserve(self, uid: int, deadline=None, background=False) -> _Worker:
        with self._lock:
            if self._closed:
                raise WorkerError("Game workers are closed")
            worker = self._workers.get(uid)
            if background and worker is not None and worker.active:
                raise WorkerBusy("Game backend capacity is busy")
            if worker is None:
                while len(self._workers) >= self.max_workers:
                    idle = [
                        (value.last_used, key, value)
                        for key, value in self._workers.items()
                        if value.active == 0
                        and (
                            not background or time.monotonic() - value.last_used >= 120
                        )
                    ]
                    if idle:
                        _, key, evicted = min(idle)
                        del self._workers[key]
                        self._stop(evicted, graceful=True)
                        break
                    if background:
                        raise WorkerBusy("Game backend capacity is busy")
                    remaining = 0 if deadline is None else deadline - time.monotonic()
                    if remaining <= 0:
                        raise WorkerError("Game backend capacity is busy")
                    self._capacity.wait(remaining)
                    if self._closed:
                        raise WorkerError("Game workers are closed")
                    worker = self._workers.get(uid)
                    if worker is not None:
                        break
                if worker is None:
                    parent, child = self._context.Pipe(duplex=True)
                    process = self._context.Process(
                        target=_backend,
                        args=(child, str(self.root), self.endpoint, uid),
                        daemon=True,
                        name=f"DanMemo-backend-{uid}",
                    )
                    try:
                        process.start()
                    except Exception:
                        parent.close()
                        child.close()
                        raise WorkerError("Game backend could not start") from None
                    child.close()
                    worker = _Worker(process, parent)
                    self._workers[uid] = worker
            worker.active += 1
            return worker

    @contextmanager
    def _serial(self, worker: _Worker, deadline: float):
        if not worker.lock.acquire(timeout=max(0, deadline - time.monotonic())):
            self._stop(worker)
            raise WorkerError("Game backend request timed out")
        try:
            yield
        finally:
            worker.lock.release()

    def call(
        self,
        uid: int,
        action: str,
        body: bytes,
        request_id: str | None = None,
        *,
        background=False,
    ) -> tuple[str, bytes]:
        if type(uid) is not int or not 2000000000 <= uid <= 2147483647:
            raise ValueError("Invalid online game UID")
        if not isinstance(action, str) or action not in (
            *API_ACTIONS,
            *SHARED_ROUTES,
            *INTERNAL_ACTIONS,
        ):
            raise ValueError("Unsupported game action")
        if not isinstance(body, bytes) or len(body) > MAX_ENTITY:
            raise ValueError("Invalid game request entity")
        if request_id is not None and (
            not isinstance(request_id, str) or len(request_id) > 512
        ):
            raise ValueError("Invalid game request ID")
        deadline = time.monotonic() + CALL_TIMEOUT
        worker = self._reserve(uid, deadline=deadline, background=background)
        try:
            with self._serial(worker, deadline):
                if not worker.process.is_alive():
                    raise WorkerError("Game backend is unavailable")
                done = threading.Event()
                results = []

                def exchange():
                    try:
                        if not worker.ready:
                            initial = worker.connection.recv()
                            if initial != ("ready",):
                                raise WorkerError("Game backend initialization failed")
                            worker.ready = True
                        worker.connection.send((action, body, request_id))
                        result = worker.connection.recv()
                        results.append(result)
                    except Exception:
                        results.append(("error", "Game backend communication failed"))
                    finally:
                        done.set()

                thread = threading.Thread(target=exchange, daemon=True)
                thread.start()
                if not done.wait(max(0, deadline - time.monotonic())):
                    self._stop(worker)
                    thread.join(1)
                    raise WorkerError("Game backend request timed out")
                result = results[0]
                if not isinstance(result, tuple) or not result or result[0] != "ok":
                    raise WorkerError("Game backend rejected the request")
                return (result[1], result[2])
        finally:
            with self._lock:
                worker.active -= 1
                worker.last_used = time.monotonic()
                self._capacity.notify_all()
                if not worker.process.is_alive() and self._workers.get(uid) is worker:
                    del self._workers[uid]
                    self._stop(worker)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            workers = list(self._workers.values())
            self._workers.clear()
            self._capacity.notify_all()
        for worker in workers:
            self._stop(worker, graceful=worker.active == 0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
