"""Native game API adapters and per-account progression."""

from __future__ import annotations
from danmemo.storage.atomic import write_json as atomic_write_json
from danmemo import paths as _paths
import base64
import copy
import gzip
import functools
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit
import zipfile
import zlib

ROOT = _paths.ROOT
from danmemo.protocol.codec import (
    msgpack_string as msgpack_string,
    msgpack_value as msgpack_value,
)

CONTENTS = _paths.ASSETS
SOURCE_APK = CONTENTS / "com.eu.danmemo_2023-12-22.apk"
WORK = _paths.STATE
TUTORIAL_DRAW_STATE = WORK / "tutorial-draw.json"
PROGRESS_STATE = WORK / "progress.json"
PROGRESS_LOCK = threading.RLock()
MANIFEST_LOCK = threading.RLock()
LOGIN_REFRESH_TOKENS: dict[str, str] = {}
PROGRESS_KEYS = {
    "UserTutorial": "tutorialId",
    "UserSystemFlag": "systemFlagId",
    "UserAchievement": "achievementId",
    "UserPC": "pcId",
    "UserPCJobSet": "pcId",
    "UserLottery": "lotteryId",
    "UserLimitBreakItem": "limitBreakItemId",
    "UserCurrency": "currencyId",
    "UserExpItem": "expItemId",
    "UserStatusBoardItem": "statusBoardItemId",
    "UserGeneralLimitBreakItem": "limitBreakItemId",
    "UserInfo": "userId",
    "UserParty": "id",
    "UserItemToken": "signature",
    "UserRandomSeed": "signature",
    "UserAdventureState": "userId",
    "UserAdventureStage": "adventureStageId",
}
PROGRESS_KEYS["UserEquipmentRecipe"] = "equipmentRecipeId"
PROGRESS_KEYS["UserEquipment"] = "slotIndex"
PROGRESS_KEYS["UserShopProduct"] = "shopProductId"
PROGRESS_KEYS["UserCharacterCostume"] = "costumeId"
PROGRESS_KEYS["UserPvBRecord"] = "pvbId"
PROGRESS_KEYS["UserPvPRecord"] = "pvpId"
PROGRESS_KEYS["UserSerialPvpRecord"] = "serialPvpId"
PROGRESS_KEYS["UserUnderPvPRecord"] = "underPvpId"
PROGRESS_KEYS["UserUnderPvPGameStatus"] = "underPvpId"
PROGRESS_KEYS["UserUnderPvpRevengeStatus"] = "underPvpId"
PROGRESS_KEYS["UserUnderPvpRevengeTicket"] = "underPvpRevengeTicketId"
PROGRESS_KEYS["UserUnionRaidMedal"] = "medalId"
PROGRESS_KEYS["UserElementEventRecord"] = "elementEventStageDifficultyId"
PROGRESS_KEYS["UserGvERecord"] = "gveId"
PROGRESS_KEYS.update(UserVoteInfo="voteInfoId", UserVoteItem="voteItemId")
PROGRESS_KEYS["UserPCTicket"] = "pcTicketId"
PROGRESS_KEYS.update(
    UserLoginBonus="loginBonusId", UserLoginBonusProgress="loginBonusId"
)
PROGRESS_KEYS["UserOtherParty"] = ("userId", "partyType", "index")
PROGRESS_KEYS.update(
    {
        "User" + name[0].upper() + name[1:]: name + "Id"
        for name in (
            "staminaTicket",
            "gachaTicket",
            "adventureTicket",
            "keyItem",
            "heroSublimateItem",
            "heroSublimateUnlockItem",
            "cpItem",
            "equipmentEvolveItem",
            "equipmentMaterial",
            "familiaDonationItem",
            "equipmentEnhanceItem",
            "eventItem",
        )
    }
)
for _table, _key in json.loads(
    (_paths.METADATA / "offline_user_table_keys.json").read_text()
).items():
    PROGRESS_KEYS.setdefault(_table, _key)
PROGRESS_KEYS.update(
    {
        "UserEquipmentEnhanceItem": "enhanceItemId",
        "UserEquipmentEvolveItem": "evolveItemId",
    }
)
PROGRESS_KEYS.update(
    {
        "UserLockAbilityItem": "lockAbilityId",
        "UserSerialPvpSpTicketItem": "ticketId",
        "UserUnionRaidNormalSpTicketItem": "ticketId",
        "UserUnionRaidBossSpTicketItem": "ticketId",
    }
)
SINGLETON_TABLES = set(
    json.loads((_paths.METADATA / "offline_user_table_keys.json").read_text())
)
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
API_ACTIONS = (
    "currency/hard/consume",
    "election/vote",
    "casino/ranking/get",
    "casino/ranking/update",
    "takarakuji/get",
    "matching_user/game_user_id",
    "user/login",
    "user_data/confirm",
    "user_data/push",
    "user_data/pull",
    "tutorial/draw",
    "lottery/draw",
    "lottery/ticket/draw",
    "gift/receive",
    "event_box/show",
    "event_box/draw",
    "notice/show",
    "shop/buy",
    "character/friendship/complete",
    "dress_point/complete",
    "adventure/stage/complete",
    "pve/stage/complete",
    "pve/score/ranking/get",
    "pve/recent/clear/get",
    "pvb/stage/complete",
    "shooting/ranking/get",
    "shooting/stage/complete",
    "pvb/summary/get",
    "pvb/friend/ranking/get",
    "pvb/total/ranking/get",
    "pvb/group/ranking/get",
    "element_event/summary/get",
    "element_event/stage/complete",
    "element_event/ranking/top/get",
    "element_event/ranking/around/get",
    "element_event/ranking/character/get",
    "serial_pvp/summary/get",
    "under_pvp/summary/get",
    "under_pvp/rival_list/update",
    "under_pvp/player_info/get",
    "under_pvp/character/ranking/get",
    "under_pvp/battle/start",
    "under_pvp/battle/end",
    "under_pvp/battle/restart",
    "pvp/summary/get",
    "pvp/battle/start",
    "pvp/battle/end",
    "pvp/battle/restart",
    "serial_pvp/rival_list/update",
    "serial_pvp/grade/select",
    "serial_pvp/battle/start",
    "serial_pvp/battle/end",
    "serial_pvp/battle/restart",
    "serial_pvp/result/get",
    "achievement/complete",
    "expedition/times",
    "expedition/complete",
    "achievement/familia/add",
    "achievement/familia/get",
    "friend/following/list",
    "friend/get",
    "friend/follower/list",
    "friend/support/list",
    "familia/mystatus",
    "familia/search/random",
    "familia/search/name",
    "familia/search/option",
    "familia/invited/list",
    "familia/join/list/sent",
)
URL_PATCHES = {
    b"https://api-danmemo-eu.wrightflyer.net": f"{ENDPOINT}/danmemore-local".encode(),
    b"https://cdn-danmemo.akamaized.net/eu": f"{ENDPOINT}/content".encode(),
    b"https://tdsdk-danmemo-eu.wrightflyer.net": f"{ENDPOINT}/telemetry".encode(),
    b"https://bn-payment-us.wrightflyer.net": f"{ENDPOINT}/billing".encode(),
    b"https://storage.googleapis.com/argo-ap4zsi86n4q": f"{ENDPOINT}/storage".encode(),
}


def sys_module():
    import sys

    return sys.modules[__name__]


def schedule_module():
    from danmemo.config import schedule as offline_admin

    return offline_admin


def arena_module():
    from danmemo.game import arena as offline_arena

    return offline_arena


def content_path(request_path: str) -> Path | None:
    if "manifest" in request_path:
        schedule_module().ensure_daily_catalog(sys_module())
    if os.environ.get("DANMEMORE_ONLINE_ASSETS") == "1":
        from danmemo.content.catalogs import (
            persisted_content_path as persisted_content_path,
        )

        published = persisted_content_path(request_path)
        if published is not None:
            return published
    path = unquote(urlsplit(request_path).path)
    from danmemo.content import web_assets as recovered_web_assets

    recovered = recovered_web_assets.resolve(path)
    if recovered is not None:
        return recovered
    name = PurePosixPath(path).name
    if path.startswith(
        ("/api/asset/manifest/", "/content/asset/manifest/")
    ) and name.startswith("version.manifest."):
        phase = name.rpartition(".")[2]
        candidate = CONTENTS / phase / "version.manifest"
        return (
            local_version_manifest(phase)
            if phase in tuple("12345678") and candidate.is_file()
            else None
        )
    parts = PurePosixPath(path).parts
    if (
        path.startswith(("/api/manifests/us/pkm/", "/content/manifests/us/pkm/"))
        and len(parts) >= 2
    ):
        name = parts[-2]
        if name.startswith("project.manifest."):
            phase = name.rpartition(".")[2]
            return local_project_manifest(phase) if phase in tuple("12345678") else None
        candidate = CONTENTS / "1" / "manifests" / name
        if name in (
            "single_image.manifest",
            "area_atlas.manifest",
            "voice_episode.manifest",
        ):
            leaf = parts[-1]
            if leaf.endswith(".local") and len(leaf) == 38:
                digest = leaf[:-6]
                if all((c in "0123456789abcdef" for c in digest)):
                    published = download_catalog_root() / leaf
                    if published.is_file():
                        return published
                    current = local_download_manifest(name)
                    if hashlib.md5(current.read_bytes()).hexdigest() != digest:
                        return None
                    return published_download_catalog(current)
            return local_download_manifest(name)
        return candidate if candidate.is_file() else None
    # Some archived masters use resources/gacha or resources/shop paths.
    # Resolve only URLs explicitly registered by a verified local manifest.
    if path.startswith(("/api/", "/content/")):
        registered = asset_paths().get(path)
        if registered is not None and registered.is_file():
            return registered
    if path.startswith(("/api/contents/", "/content/contents/")):
        candidate = asset_paths().get(path)
        if candidate is not None and candidate.is_file():
            return candidate
        return supplemental_asset_path(path) or episode_voice_zip(path)
    prefix = next(
        (value for value in ("/content/", "/storage/") if path.startswith(value)), None
    )
    if not prefix:
        return None
    parts = PurePosixPath(path[len(prefix) :]).parts
    if not parts or any((part in ("", ".", "..") for part in parts)):
        return None
    if len(parts) == 2 and parts[0] in tuple("12345678"):
        if parts[1] == "version.manifest":
            return local_version_manifest(parts[0])
        if parts[1] == "project.manifest":
            return local_project_manifest(parts[0])
    candidate = (CONTENTS / Path(*parts)).resolve()
    if (
        len(parts) == 3
        and parts[:2] == ("1", "manifests")
        and (
            parts[2]
            in (
                "single_image.manifest",
                "area_atlas.manifest",
                "voice_episode.manifest",
            )
        )
    ):
        return local_download_manifest(parts[2])
    root = CONTENTS.resolve()
    return candidate if candidate == root or root in candidate.parents else None


@functools.lru_cache(maxsize=8)
def local_project_manifest(phase: str) -> Path:
    WORK.mkdir(exist_ok=True)
    output = WORK / f"project.manifest.{phase}.local"
    source_root = CONTENTS / phase
    manifest = json.loads(
        (source_root / "project.manifest").read_text(encoding="utf-8")
    )
    overlays = schedule_module().overlays() if phase == "1" else {}
    from danmemo.content import ui_layouts as offline_ui_layouts

    overlays.update(offline_ui_layouts.overlays(CONTENTS, WORK, phase))
    for directory in (
        "files/icon",
        "files/shop/event_banner",
        "files/event_box",
        "files/lottery",
        "files/character/lottery",
        "files/adventure",
    ):
        for path in sorted((source_root / directory).rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source_root).as_posix()
            if relative not in manifest["assets"]:
                manifest["assets"][relative] = {
                    "md5": "",
                    "size": 0,
                    "path": f"contents/{relative}/pending{path.suffix}",
                }
    for relative, metadata in list(manifest["assets"].items()):
        previous_metadata = dict(metadata)
        path = overlays.get(relative, source_root / relative)
        local_catalog = relative in (
            "manifests/single_image.manifest",
            "manifests/area_atlas.manifest",
            "manifests/voice_episode.manifest",
        )
        if local_catalog:
            path = published_download_catalog(
                local_download_manifest(PurePosixPath(relative).name)
            )
        if not path.is_file():
            del manifest["assets"][relative]
            continue
        previous_digest = metadata["md5"]
        stat = path.stat()
        cached = (
            archive_checksums().get(f"{phase}/{relative}")
            if not local_catalog and relative not in overlays
            else None
        )
        if cached and cached[:2] == [stat.st_size, stat.st_mtime_ns]:
            metadata["md5"] = cached[2]
        else:
            with path.open("rb") as file:
                metadata["md5"] = (
                    hashlib.file_digest(file, "md5").hexdigest()
                    if hasattr(hashlib, "file_digest")
                    else hashlib.md5(file.read()).hexdigest()
                )
        metadata["size"] = stat.st_size
        if (
            local_catalog
            or relative in overlays
            or (not previous_digest)
            or (metadata["md5"] != previous_digest)
        ):
            filename = metadata["md5"] + (
                ".enc" if relative in overlays else path.suffix
            )
            metadata["path"] = str(PurePosixPath(metadata["path"]).parent / filename)
        if metadata != previous_metadata:
            binding = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
            manifest["version"] = hashlib.md5(
                (manifest["version"] + relative + binding).encode()
            ).hexdigest()
    return generated_manifest(f"project.manifest.{phase}", manifest)


@functools.cache
def archive_checksums() -> dict:
    path = WORK / "archive-checksums.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def generated_manifest(name: str, manifest: dict) -> Path:
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[
        :16
    ]
    output = WORK / "manifests" / f"{name}.{digest}.local"
    with MANIFEST_LOCK:
        if not output.exists():
            atomic_write_json(output, manifest)
    return output


def download_catalog_root() -> Path:
    return Path(
        os.environ.get("DANMEMORE_DOWNLOAD_CATALOGS", str(WORK / "download-catalogs"))
    )


def published_download_catalog(source: Path) -> Path:
    """Freeze the exact catalog bytes advertised by a checksum URL."""
    body = source.read_bytes()
    digest = hashlib.md5(body).hexdigest()
    output = download_catalog_root() / (digest + ".local")
    with MANIFEST_LOCK:
        if not output.is_file():
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=output.parent, prefix=digest + ".", delete=False
            ) as file:
                temporary = Path(file.name)
                file.write(body)
            temporary.chmod(420)
            try:
                temporary.replace(output)
            finally:
                temporary.unlink(missing_ok=True)
    return output


@functools.lru_cache(maxsize=3)
def local_download_manifest(name: str) -> Path:
    if name == "voice_episode.manifest":
        return local_voice_manifest()
    if name not in ("single_image.manifest", "area_atlas.manifest"):
        raise ValueError("Unknown download catalog")
    manifest = json.loads((CONTENTS / "1/manifests" / name).read_text("utf-8"))
    manifest["baseUrl"] = ENDPOINT + "/content/"
    if name == "single_image.manifest":
        receipt = WORK / "asset-recovery" / "legacy-reference-recovery.json"
        if receipt.is_file():
            for recovered in json.loads(receipt.read_text("utf-8")):
                if (
                    recovered.get("status") != "recovered-legacy"
                    or recovered.get("kind") != "image"
                ):
                    continue
                relative = recovered.get("path", "")
                identifier = PurePosixPath(relative).stem
                if (
                    not identifier.isdecimal()
                    or relative != f"files/ui/single_image/{identifier}.jpg"
                ):
                    continue
                candidate = CONTENTS / "1" / relative
                if not candidate.is_file():
                    continue
                with candidate.open("rb") as source:
                    checksum = hashlib.file_digest(source, "md5").hexdigest()
                if checksum != recovered.get("md5"):
                    continue
                manifest["singleImages"].setdefault(
                    identifier,
                    dict(
                        singleImageId=int(identifier),
                        md5=checksum,
                        size=candidate.stat().st_size,
                        path=f"contents/{relative}/{checksum}.jpg",
                    ),
                )
    output = WORK / (name + ".local")
    output.parent.mkdir(parents=True, exist_ok=True)
    return generated_manifest(name, manifest)


@functools.lru_cache(maxsize=1)
def asset_paths() -> dict[str, Path]:
    result = {}
    for phase in "12345678":
        manifest = json.loads(local_project_manifest(phase).read_text(encoding="utf-8"))
        overlays = schedule_module().overlays() if phase == "1" else {}
        from danmemo.content import ui_layouts as offline_ui_layouts

        overlays.update(offline_ui_layouts.overlays(CONTENTS, WORK, phase))
        for relative, metadata in manifest["assets"].items():
            source = overlays.get(relative, CONTENTS / phase / Path(relative))
            if phase == "1" and relative in (
                "manifests/single_image.manifest",
                "manifests/area_atlas.manifest",
                "manifests/voice_episode.manifest",
            ):
                source = published_download_catalog(
                    local_download_manifest(PurePosixPath(relative).name)
                )
            result["/api/" + metadata["path"]] = source
            result["/content/" + metadata["path"]] = source
    return result


@functools.lru_cache(maxsize=256)
def supplemental_asset_path(path: str) -> Path | None:
    parts = PurePosixPath(path).parts
    if (
        len(parts) < 6
        or parts[1] not in ("api", "content")
        or parts[2:4] != ("contents", "files")
        or any((part in (".", "..") for part in parts))
    ):
        return None
    relative = Path(*parts[3:-1])
    for archive in ("single_image_root", "area_atlas_root", "voice_root", *"12345678"):
        archive_root = (CONTENTS / archive).resolve()
        candidate = (archive_root / relative).resolve()
        if archive_root not in candidate.parents or not candidate.is_file():
            continue
        with candidate.open("rb") as source:
            digest = hashlib.file_digest(source, "md5").hexdigest()
        if parts[-1] == digest + candidate.suffix:
            return candidate
    return None


VOICE_ZIP_LOCK = threading.Lock()


@functools.lru_cache(maxsize=1)
def local_voice_manifest() -> Path:
    """Advertise only archived voices, with checksums of the served ZIPs."""
    output = WORK / "voice_episode.manifest.local"
    if output.is_file():
        cached = json.loads(output.read_text("utf-8"))
        changed = False
        archived = json.loads(
            (CONTENTS / "1/manifests/voice_episode.manifest").read_text("utf-8")
        )["episodes"]
        for key, metadata in archived.items():
            original = (
                CONTENTS
                / "1/files/sound/voice_episode_zip"
                / (str(metadata["episodeId"]) + ".zip")
            )
            if not original.is_file():
                continue
            digest = hashlib.md5(original.read_bytes()).hexdigest()
            recovered = dict(
                metadata,
                md5=digest,
                size=original.stat().st_size,
                path=str(PurePosixPath(metadata["path"]).parent / (digest + ".zip")),
            )
            if cached["episodes"].get(key) != recovered:
                cached["episodes"][key] = recovered
                changed = True
        if cached.get("baseUrl") != ENDPOINT + "/content/":
            cached["baseUrl"] = ENDPOINT + "/content/"
            changed = True
        if changed:
            atomic_write_json(output, cached)
        return output
    manifest = json.loads(
        (CONTENTS / "1" / "manifests" / "voice_episode.manifest").read_text("utf-8")
    )
    manifest["baseUrl"] = ENDPOINT + "/content/"
    for key, metadata in list(manifest["episodes"].items()):
        bundle = episode_voice_zip("/content/" + metadata["path"])
        if bundle is None:
            del manifest["episodes"][key]
            continue
        with bundle.open("rb") as source:
            digest = hashlib.file_digest(source, "md5").hexdigest()
        metadata["md5"] = digest
        metadata["size"] = bundle.stat().st_size
        metadata["path"] = str(
            PurePosixPath(metadata["path"]).parent / (digest + ".zip")
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    temporary.replace(output)
    return output


@functools.lru_cache(maxsize=8)
def local_version_manifest(phase: str) -> Path:
    manifest = json.loads((CONTENTS / phase / "version.manifest").read_text("utf-8"))
    project = json.loads(local_project_manifest(phase).read_text("utf-8"))
    old_version = manifest["version"]
    manifest["version"] = project["version"]
    for key in ("remoteManifestUrl", "remoteVersionUrl"):
        manifest[key] = manifest[key].replace(old_version, manifest["version"])
    output = WORK / f"version.manifest.{phase}.local"
    return generated_manifest(f"version.manifest.{phase}", manifest)


@functools.lru_cache(maxsize=256)
def episode_voice_zip(path: str) -> Path | None:
    prefix = next((p for p in ("/api/", "/content/") if path.startswith(p)), None)
    if prefix is None:
        return None
    episodes = json.loads(
        (CONTENTS / "1" / "manifests" / "voice_episode.manifest").read_text("utf-8")
    )["episodes"]
    metadata = next(
        (row for row in episodes.values() if prefix + row["path"] == path), None
    )
    local = WORK / "voice_episode.manifest.local"
    if metadata is None and local.is_file():
        metadata = next(
            (
                row
                for row in json.loads(local.read_text("utf-8"))["episodes"].values()
                if prefix + row["path"] == path
            ),
            None,
        )
    if metadata is None and "/contents/files/sound/voice_episode_zip/" in path:
        parts = PurePosixPath(path).parts
        if len(parts) >= 2 and parts[-2].endswith(".zip"):
            requested_episode = parts[-2][:-4]
            metadata = next(
                (
                    row
                    for row in episodes.values()
                    if str(row["episodeId"]) == requested_episode
                ),
                None,
            )
    if metadata is None:
        return None
    episode = str(metadata["episodeId"])
    wanted = PurePosixPath(path).name
    original = CONTENTS / "1/files/sound/voice_episode_zip" / (episode + ".zip")
    rebuilt = WORK / "voice-zips" / (episode + ".zip")
    for candidate in (original, rebuilt):
        if candidate.is_file():
            with candidate.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "md5").hexdigest()
            if wanted == checksum + ".zip":
                return candidate
    source_root = CONTENTS / "voice_root" / "files" / "sound" / "voice_episode"
    source = source_root / episode
    files = sorted(source.rglob("*.ogg")) if source.is_dir() else []
    if not files:
        return None
    output = WORK / "voice-zips" / (episode + ".zip")
    with VOICE_ZIP_LOCK:
        if not output.is_file():
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(".tmp")
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_STORED
            ) as bundle:
                for file in files:
                    bundle.write(file, file.relative_to(source_root).as_posix())
            temporary.replace(output)
    with output.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "md5").hexdigest()
    return output if wanted == checksum + ".zip" else None


def byte_range(value: str | None, size: int) -> tuple[int, int, bool]:
    if not value:
        return (0, size - 1, False)
    unit, separator, spec = value.partition("=")
    start_text, dash, end_text = spec.partition("-")
    if unit != "bytes" or separator != "=" or dash != "-" or (not start_text):
        raise ValueError("Unsupported Range header")
    start = int(start_text)
    end = min(int(end_text), size - 1) if end_text else size - 1
    if start < 0 or start > end or start >= size:
        raise ValueError("Range outside file")
    return (start, end, True)


def api_action(request_path: str) -> str | None:
    path = urlsplit(request_path).path.rstrip("/")
    return max(
        (action for action in API_ACTIONS if path.endswith("/" + action)),
        key=len,
        default=None,
    )


def decode_request(body: bytes) -> dict:
    openssl = shutil.which("openssl") or "openssl"
    process = subprocess.run(
        [
            openssl,
            "enc",
            "-d",
            "-aes-256-cbc",
            "-nopad",
            "-K",
            LOCAL_AES_KEY.hex(),
            "-iv",
            LOCAL_AES_IV.encode().hex(),
        ],
        input=body,
        capture_output=True,
    )
    if process.returncode:
        raise ValueError("Could not decrypt the local client request")
    value = json.loads(zlib.decompress(process.stdout))
    if not isinstance(value, dict):
        raise ValueError("Expected a request object")
    return value


def load_progress() -> dict:
    if not PROGRESS_STATE.is_file():
        return {"tables": {}, "tokens": {}, "completed": {}}
    from danmemo.storage.progress_io import STORE as STORE

    state = STORE.load(PROGRESS_STATE)
    from danmemo.game import resources as resources

    for table in resources.normalize_blacksmith(state.get("tables", {})):
        state["tokens"][table] = hashlib.md5(
            msgpack_value(state["tables"][table])
        ).hexdigest()
    migrated = False
    for row in state.get("tables", {}).get("UserGeneralLimitBreakItem", []):
        if "generalLimitBreakItemId" in row:
            row.setdefault("limitBreakItemId", row.pop("generalLimitBreakItemId"))
            migrated = True
    if migrated:
        state["tokens"]["UserGeneralLimitBreakItem"] = hashlib.md5(
            msgpack_value(state["tables"]["UserGeneralLimitBreakItem"])
        ).hexdigest()
    from danmemo.game.under_pvp import (
        repair_userdata_contract as repair_userdata_contract,
    )

    repair_userdata_contract(sys_module(), state)
    from danmemo.game.achievement_progress import repair as repair

    repair(sys_module(), state)
    return state


def write_progress(state: dict) -> None:
    from danmemo.game.achievement_progress import repair as repair

    repair(sys_module(), state)
    for attempt, receipt in list(state.get("completed", {}).items()):
        if isinstance(receipt, dict) and "UserAdventureStage" in receipt.get(
            "data", {}
        ):
            compact = dict(receipt, data=dict(receipt["data"]))
            compact["data"].pop("UserAdventureStage")
            state["completed"][attempt] = compact
    from danmemo.storage.progress_io import STORE as STORE

    STORE.save(PROGRESS_STATE, state)


def repair_ticket_inventory() -> int:
    """Migrate stack-shaped mission ticket rewards to native dated tickets."""
    with PROGRESS_LOCK:
        state = load_progress()
        changed = 0
        for row in state["tables"].get("UserGachaTicket", []):
            if "amount" not in row:
                continue
            if "dateList" not in row:
                row["dateList"] = [
                    {"aquiredAt": int(time.time())}
                    for _ in range(max(0, row["amount"]))
                ]
            row.pop("amount", None)
            row.pop("reservedAmount", None)
            changed += 1
        if changed:
            state["tokens"]["UserGachaTicket"] = hashlib.md5(
                msgpack_value(state["tables"]["UserGachaTicket"])
            ).hexdigest()
            write_progress(state)
        return changed


def repair_special_arts_unlock() -> bool:
    """Enable the native arts model after the opening selection tutorial.

    The offline selection flow can finish with the native arts model still
    locked. Make the post-intro state consistent without granting charge or
    skipping battle tutorials. Native charge handlers ignore a locked model.
    """
    with PROGRESS_LOCK:
        state = load_progress()
        tutorials = state["tables"].get("UserTutorial", [])
        if not any(
            (
                r.get("tutorialId") == 780030005 and r.get("isEnd") is True
                for r in tutorials
            )
        ):
            return False
        changed = False
        for info in state["tables"].get("UserInfo", []):
            force = info.get("anotherForce")
            if isinstance(force, dict) and force.get("isUnlocked") is False:
                force["isUnlocked"] = True
                changed = True
        if changed:
            state["tokens"]["UserInfo"] = hashlib.md5(
                msgpack_value(state["tables"]["UserInfo"])
            ).hexdigest()
            write_progress(state)
        return changed


def repair_battle_auto_default() -> bool:
    """Ensure battleAuto defaults to False for accounts to prevent automatic battle actions."""
    with PROGRESS_LOCK:
        state = load_progress()
        infos = state.get("tables", {}).get("UserInfo", [])
        if not infos:
            return False
        changed = False
        for info in infos:
            if info.get("battleAuto") is not False:
                info["battleAuto"] = False
                changed = True
        if changed:
            state["tokens"]["UserInfo"] = hashlib.md5(
                msgpack_value(state["tables"]["UserInfo"])
            ).hexdigest()
            write_progress(state)
        return changed


def repair_character_jobs() -> int:
    """Restore missing job links without changing levels, boards or equipment."""
    with PROGRESS_LOCK:
        state = load_progress()
        changed = 0
        for row in state["tables"].get("UserPC", []):
            if row.get("jobs") and row.get("jobSlots"):
                continue
            master = raw_lottery_catalog()["pcs"].get(str(row["pcId"]))
            job = master[0] if master else tutorial_pc_jobs().get(row["pcId"])
            if job is None:
                continue
            template = user_pc(row["pcId"], job, row.get("hp", 100), row.get("mp", 10))
            for field in ("jobs", "jobSlots"):
                if not row.get(field):
                    row[field] = template[field]
                    changed += 1
        if changed:
            state["tokens"]["UserPC"] = hashlib.md5(
                msgpack_value(state["tables"]["UserPC"])
            ).hexdigest()
            write_progress(state)
        return changed


def persist_progress(value: dict) -> None:
    flow = flow_repair_module()
    with PROGRESS_LOCK:
        state = load_progress()
        from danmemo.game.inventory_sync import (
            observed_spending as observed_spending,
            refill_evidence as refill_evidence,
        )

        stamina_refill = refill_evidence(state, value)
        for delta in value["deltas"]:
            incoming = delta.get("putItems", {}).get("UserAdventureState", [])
            previous = state["tables"].get("UserAdventureState", [{}])[0]
            for adventure in incoming:
                sid = adventure.get("stageId", 0)
                old_events = previous.get("events", [])
                new_events = adventure.get("events", [])
                fresh = sid != previous.get("stageId") or (
                    old_events
                    and all((e.get("isFinished") for e in old_events))
                    and any((not e.get("isFinished") for e in new_events))
                )
                if sid and delta["trigger"] == "AreaChanged" and fresh:
                    stage = next(
                        (
                            r
                            for r in state["tables"].get("UserAdventureStage", [])
                            if r["adventureStageId"] == sid
                        ),
                        None,
                    )
                    if stage is not None and stage.get("state") in (1, 2, 4):
                        now = max(int(time.time()), stage.get("lastTryDateTime", 0) + 1)
                        stage["firstTryDateTime"] = stage.get("firstTryDateTime") or now
                        stage["lastTryDateTime"] = now
                        state["tokens"]["UserAdventureStage"] = hashlib.md5(
                            msgpack_value(state["tables"]["UserAdventureStage"])
                        ).hexdigest()
                if __package__ and hasattr(sys_module(), "ONLINE_SHARED"):
                    from danmemo.game.familia_raid import (
                        capture_adventure as capture_adventure,
                    )

                    capture_adventure(state, adventure, fresh)
                    from danmemo.game import extra_modes as online_extra_modes
                    from danmemo.game import union_raid as online_union_raid

                    online_extra_modes.capture_adventure(state, adventure, fresh)
                    online_union_raid.capture_adventure(state, adventure, fresh)
                from danmemo.game import element_event as offline_element_event

                offline_element_event.capture_adventure(state, adventure)
            tables = set(delta.get("putItems", {})) | set(delta.get("deleteItems", {}))
            for table in tables:
                puts = delta.get("putItems", {}).get(table, [])
                deletes = delta.get("deleteItems", {}).get(table, [])
                if not puts and (not deletes):
                    continue
                defaults = login_pcs() if table == "UserPC" else []
                key = PROGRESS_KEYS.get(table)
                sample = (puts or deletes)[0]
                if key is None:
                    natural = table.removeprefix("User")
                    natural = natural[:1].lower() + natural[1:] + "Id"
                    key = next(
                        (
                            candidate
                            for candidate in (natural, "id")
                            if candidate in sample
                        ),
                        None,
                    )
                    if key is None:
                        ids = [
                            field
                            for field in sample
                            if field.endswith("Id") and field != "userId"
                        ]
                        key = (
                            "signature"
                            if "signature" in sample
                            else tuple(sorted(ids))
                            if ids
                            else "userId"
                        )
                gift_pending = state.get("giftInventoryReconciliation", {}).get(
                    table, []
                )
                familia_pending = state.get("familiaInventoryReconciliation", {}).get(
                    table, []
                )
                keys = (key,) if isinstance(key, str) else key
                owners = (
                    (0, LOCAL_USER_ID) if table == "UserMemoria" else (LOCAL_USER_ID,)
                )
                if any(
                    (
                        any((field not in row for field in keys))
                        or row.get("userId", LOCAL_USER_ID) not in owners
                        for row in puts + deletes
                    )
                ):
                    raise ValueError(f"Invalid account or identity in {table}")

                def identity(row):
                    return tuple((row[field] for field in keys))

                if table == "UserGift" and state.get("adminGiftItems"):
                    continue
                rows = {
                    identity(row): row for row in state["tables"].get(table, defaults)
                }
                for row in deletes:
                    if table in ("UserLoginBonus", "UserLoginBonusProgress"):
                        continue
                    if table in ("UserShopProduct", "UserCharacterCostume"):
                        owned = rows.get(identity(row), {})
                        from danmemo.game.shop_limits import protected as protected

                        if (
                            table == "UserShopProduct"
                            and protected(owned)
                            or (
                                table == "UserCharacterCostume"
                                and owned.get("costumeState", 0) >= 2
                            )
                        ):
                            continue
                    if table in ("UserVoteInfo", "UserVoteItem"):
                        continue
                    if (
                        table == "UserUnionRaidMedal"
                        and row.get("medalId") == 410000001
                        and hasattr(sys_module(), "ONLINE_SHARED")
                    ):
                        from danmemo.game.union_raid import (
                            medal_balance as medal_balance,
                        )

                        if medal_balance(sys_module().ONLINE_SHARED) is not None:
                            continue
                    if table == "UserInfo" and state.get("arenaStatusReconciliation"):
                        continue
                    if isinstance(key, str) and row.get(key) in familia_pending:
                        continue
                    if isinstance(key, str) and row.get(key) in gift_pending:
                        continue
                    if (
                        table == "UserAchievement"
                        and row.get("achievementId") in flow.rookie_ids()
                    ):
                        continue
                    if table == "UserAchievement":
                        from danmemo.game.mission_history import (
                            rules as seasonal_mission_rules,
                        )

                        if row.get("achievementId") in seasonal_mission_rules():
                            continue
                        from danmemo.game.reward_delivery import (
                            manual_claim_ids as manual_claim_ids,
                        )

                        saved = rows.get(identity(row), {})
                        if row.get("achievementId") in manual_claim_ids() and saved.get(
                            "state"
                        ) in (3, 4):
                            continue
                    if table == "UserAchievement" and str(
                        row.get("achievementId")
                    ) in state.get("gameRewardAchievementCycles", {}):
                        continue
                    if table == "UserSkipTicket" and str(
                        row.get("skipTicketId")
                    ) in state.get("skipTicketReconciliation", {}):
                        continue
                    rows.pop(identity(row), None)
                for row in puts:
                    if table in ("UserLoginBonus", "UserLoginBonusProgress"):
                        from danmemo.game.login_bonuses import reconcile as reconcile

                        row = reconcile(table, row, rows.get(identity(row), {}))
                        if row is None:
                            continue
                    if table in ("UserShopProduct", "UserCharacterCostume"):
                        previous_row = rows.get(identity(row), {})
                        if (
                            table == "UserCharacterCostume"
                            and previous_row.get("costumeState", 0) >= 2
                        ):
                            row = copy.deepcopy(row)
                            row["costumeState"] = max(
                                row.get("costumeState", 0), previous_row["costumeState"]
                            )
                        elif table == "UserShopProduct":
                            from danmemo.game.shop_limits import reconcile as reconcile

                            row = reconcile(row, previous_row)
                    if table == "UserAchievement":
                        from danmemo.game.mission_history import (
                            preserve_push as preserve_push,
                        )

                        row = preserve_push(row, rows.get(identity(row), {}))
                    if table in ("UserVoteInfo", "UserVoteItem", "UserInfo"):
                        from danmemo.game.campaign_features import (
                            reconcile as reconcile,
                        )

                        row = reconcile(state, table, row, rows.get(identity(row), {}))
                    if table == "UserCasinoState":
                        from danmemo.game.casino_lotto import (
                            reconcile_push as reconcile_push,
                        )

                        row = reconcile_push(state, row)
                    if table == "UserUnionRaidMedal" and hasattr(
                        sys_module(), "ONLINE_SHARED"
                    ):
                        from danmemo.game.union_raid import (
                            reconcile_medal_push as reconcile_medal_push,
                        )

                        row = reconcile_medal_push(sys_module().ONLINE_SHARED, row)
                    if isinstance(key, str) and row.get(key) in familia_pending:
                        saved_item = rows.get(identity(row))
                        if saved_item is not None:
                            if row.get("amount", 0) == saved_item.get("amount", 0) or (
                                table == "UserStaminaTicket"
                                and observed_spending(row, saved_item)
                            ):
                                familia_pending.remove(row[key])
                            else:
                                row = copy.deepcopy(saved_item)
                    if isinstance(key, str) and row.get(key) in gift_pending:
                        saved_item = rows.get(identity(row))
                        if saved_item is not None:

                            def stock(item):
                                return (
                                    len(item.get("dateList", []))
                                    if table
                                    in ("UserGachaTicket", "UserSubscriptionTicket")
                                    else item.get("amount", 0)
                                )

                            if stock(row) == stock(saved_item) or (
                                table == "UserStaminaTicket"
                                and observed_spending(row, saved_item)
                            ):
                                gift_pending.remove(row[key])
                            else:
                                row = copy.deepcopy(saved_item)
                    if table == "UserSkipTicket":
                        row = flow.reconcile_ticket(state, row)
                    if (
                        table == "UserAchievement"
                        and row.get("achievementId") in flow.rookie_ids()
                    ):
                        row = copy.deepcopy(row)
                        saved = rows.get(identity(row), {})
                        for field in ("state", "progressParam", "completedAt"):
                            row[field] = max(row.get(field, 0), saved.get(field, 0))
                    if table == "UserAchievement" and str(
                        row.get("achievementId")
                    ) in state.get("gameRewardAchievementCycles", {}):
                        saved = rows.get(identity(row), {})
                        if saved.get("state") == 4:
                            row = copy.deepcopy(saved)
                    if table == "UserAchievement":
                        from danmemo.game.reward_delivery import (
                            manual_claim_ids as manual_claim_ids,
                        )

                        saved = rows.get(identity(row), {})
                        if row.get("achievementId") in manual_claim_ids():
                            claim = f"{row.get('achievementId')}:{saved.get('completedAt', 0)}"
                            if saved.get("state") == 3 or (
                                saved.get("state") == 4
                                and claim in state.get("achievementClaims", {})
                            ):
                                row = copy.deepcopy(row)
                                for field in ("state", "completedAt", "progressParam"):
                                    row[field] = saved.get(field, 0)
                                if "lastResetAt" in saved:
                                    row["lastResetAt"] = saved["lastResetAt"]
                    if table == "UserInfo":
                        row = copy.deepcopy(row)
                        previous_info = rows.get(identity(row), {})
                        saved_status = previous_info.get("playerStatus", {})
                        incoming_status = row.get("playerStatus")
                        has_incoming_status = isinstance(incoming_status, dict)
                        if not isinstance(incoming_status, dict) and state.get(
                            "arenaStatusReconciliation"
                        ):
                            row["playerStatus"] = copy.deepcopy(saved_status)
                            incoming_status = row["playerStatus"]
                        if isinstance(incoming_status, dict):
                            from danmemo.game.pass_recovery import observe as observe

                            observe(state, incoming_status, int(time.time()))
                            for field in list(
                                state.get("arenaStatusReconciliation", {})
                            ):
                                from danmemo.game.pass_recovery import STAMPS as STAMPS

                                stamp = STAMPS.get(field, field + "UpdatedAt")
                                if (
                                    field == "baseSP"
                                    and has_incoming_status
                                    and (stamina_refill > 0)
                                    and (type(incoming_status.get(field)) is int)
                                    and (
                                        saved_status.get(field, 0)
                                        < incoming_status[field]
                                        <= saved_status.get(field, 0) + stamina_refill
                                    )
                                    and (
                                        incoming_status.get(stamp, 0)
                                        >= saved_status.get(stamp, 0)
                                    )
                                ):
                                    state["arenaStatusReconciliation"].pop(field)
                                    stamina_refill = 0
                                elif (
                                    field == "baseSP"
                                    and has_incoming_status
                                    and (type(incoming_status.get(field)) is int)
                                    and (incoming_status[field] < saved_status[field])
                                    and (
                                        incoming_status.get(stamp, 0)
                                        >= saved_status.get(stamp, 0)
                                    )
                                ):
                                    state["arenaStatusReconciliation"].pop(field)
                                elif (
                                    has_incoming_status
                                    and incoming_status.get(field)
                                    == saved_status.get(field)
                                    and (
                                        incoming_status.get(stamp)
                                        == saved_status.get(stamp)
                                    )
                                ):
                                    state["arenaStatusReconciliation"].pop(field)
                                else:
                                    incoming_status[field] = saved_status[field]
                                    incoming_status[stamp] = saved_status[stamp]
                            for field in ("totalExp", "level"):
                                incoming_status[field] = max(
                                    incoming_status.get(field, 0),
                                    saved_status.get(field, 0),
                                )
                    if state.get("arena", {}).get("under"):
                        from danmemo.game.under_pvp import (
                            reconcile_snapshot_row as reconcile_snapshot_row,
                        )

                        row = reconcile_snapshot_row(
                            state, table, row, rows.get(identity(row), {})
                        )
                    rows[identity(row)] = row
                state["tables"][table] = list(rows.values())
                state["tokens"][table] = (
                    hashlib.md5(msgpack_value(state["tables"][table])).hexdigest()
                    if table
                    in (
                        "UserInfo",
                        "UserSkipTicket",
                        "UserAchievement",
                        "UserShopProduct",
                        "UserCharacterCostume",
                        "UserUnionRaidMedal",
                    )
                    or table.startswith(("UserUnderPvP", "UserUnderPvp"))
                    or table in state.get("giftInventoryReconciliation", {})
                    or (table in state.get("familiaInventoryReconciliation", {}))
                    else value["checksums"]["after"][table]
                )
                if table in state.get("familiaInventoryReconciliation", {}) and (
                    not familia_pending
                ):
                    state["familiaInventoryReconciliation"].pop(table)
                if table in state.get("giftInventoryReconciliation", {}) and (
                    not gift_pending
                ):
                    state["giftInventoryReconciliation"].pop(table)
        repair_feature_gates(state)
        flow.reconcile_rookie_stages(sys_module(), state)
        from danmemo.game.familia_quest_repair import (
            reconcile as reconcile_familia_quests,
        )

        reconcile_familia_quests(sys_module(), state)
        from danmemo.game.mission_history import snapshot as snapshot_missions

        snapshot_missions(state)
        write_progress(state)


def flow_repair_module():
    from danmemo.game import flow_repair as offline_flow_repair

    return offline_flow_repair


@functools.cache
def feature_gates() -> list[dict]:
    return json.loads(
        (_paths.METADATA / "offline_feature_gates.json").read_text("utf-8")
    )


def repair_feature_gates(state: dict) -> bool:
    cleared = {
        row["adventureStageId"]
        for row in state["tables"].get("UserAdventureStage", [])
        if row.get("state") == 4
    }
    flags = {
        row["systemFlagId"]: row for row in state["tables"].get("UserSystemFlag", [])
    }
    changed = False
    for gate in feature_gates():
        if gate["stageId"] not in cleared:
            continue
        flag = flags.setdefault(
            gate["flagId"],
            {
                "userId": LOCAL_USER_ID,
                "systemFlagId": gate["flagId"],
                "flagValue": 0,
                "lastUpdatedAt": 0,
            },
        )
        if flag["flagValue"] == 0:
            flag.update(flagValue=1, lastUpdatedAt=int(time.time()))
            changed = True
    if changed:
        state["tables"]["UserSystemFlag"] = list(flags.values())
        state["tokens"]["UserSystemFlag"] = hashlib.md5(
            msgpack_value(list(flags.values()))
        ).hexdigest()
    return changed


@functools.cache
def achievement_rewards() -> dict:
    rewards = json.loads(
        gzip.decompress(
            (_paths.METADATA / "offline_achievement_rewards.json.gz").read_bytes()
        )
    )
    from danmemo.master import reader as original

    pcs = {r.text(8): r.number(0) for r in original.rows(original.master_bytes("pc"))}
    memorias = {
        r.text(2): r.number(0) for r in original.rows(original.master_bytes("memoria"))
    }
    from danmemo.game import resources as resource_identities

    labels = {
        r.text(2 if name == "memoriaExpItem" else 1): (
            resource_identities.table_name(name),
            r.number(0),
        )
        for name in resource_identities.NAMES
        for r in original.rows(original.master_bytes(name))
    }
    lotto_items = {
        r.text(1): r.number(0)
        for r in original.rows(original.master_bytes("takarakujiItem"))
    }
    for row in original.rows(original.master_bytes("achievement")):
        label = row.text(16)
        if label in labels and label != "currency.stamina":
            table, iid = labels[label]
            rewards[str(row.number(0, 8))] = [
                table,
                resource_identities.identity_key(table),
                iid,
                row.number(17),
            ]
        if row.number(15) == 18 and label in lotto_items:
            rewards[str(row.number(0, 8))] = [
                "LottoTicket",
                "takarakujiItemId",
                lotto_items[label],
                row.number(17),
            ]
        if row.number(15) == 29 and row.text(16) in pcs:
            rewards[str(row.number(0, 8))] = [
                "UserPC",
                "pcId",
                pcs[row.text(16)],
                row.number(17),
            ]
        if row.number(15) == 30 and label in memorias:
            rewards[str(row.number(0, 8))] = [
                "UserMemoria",
                "memoriaId",
                memorias[label],
                row.number(17),
            ]
    from danmemo.game.event_missions import reconstructed as reconstructed

    for rule in reconstructed():
        template = rewards.get(str(rule["_originalTemplateId"]))
        if template:
            rewards[str(rule["0"])] = list(template)
    return rewards


def achievement_complete_response(request: dict) -> dict:
    claims = request.get("achievementIds")
    if (
        not isinstance(claims, list)
        or not claims
        or any(
            (
                not isinstance(row, dict)
                or type(row.get("achievementId")) is not int
                or type(row.get("completedAt")) is not int
                or (row["completedAt"] < 0)
                for row in claims
            )
        )
        or (len({row["achievementId"] for row in claims}) != len(claims))
    ):
        raise ValueError("Invalid achievement claims")
    with PROGRESS_LOCK:
        state = load_progress()
        achievements = {
            row["achievementId"]: row
            for row in state["tables"].get("UserAchievement", [])
        }
        rewards = achievement_rewards()
        ledger = state.setdefault("achievementClaims", {})
        pending = []
        display_rewards = []
        display_acknowledgement = False
        for claim in claims:
            aid = claim["achievementId"]
            row = achievements.get(aid)
            if (
                row is not None
                and row.get("state") in (3, 4)
                and (not row.get("completedAt"))
            ):
                row["completedAt"] = row.get("lastResetAt") or int(time.time())
            effective = (
                row.get("completedAt", 0)
                if row and claim["completedAt"] == 0
                else claim["completedAt"]
            )
            key = f"{aid}:{effective}"
            if key in ledger:
                if (
                    row is not None
                    and row.get("state") in (3, 4)
                    and (row.get("completedAt") == effective)
                ):
                    row["state"] = 4
                    if str(aid) in rewards:
                        display_rewards.append(rewards[str(aid)])
                        display_acknowledgement = True
                continue
            if (
                row is None
                or row.get("state") != 3
                or row.get("completedAt") != effective
            ):
                raise ValueError("Achievement is not ready to claim")
            if str(aid) not in rewards:
                raise ValueError("Unsupported achievement reward")
            if rewards[str(aid)][0] == "stamina" and (
                not state["tables"].get("UserInfo")
            ):
                raise ValueError("Player status is required for a stamina reward")
            pending.append((row, rewards[str(aid)], key))
            display_rewards.append(rewards[str(aid)])
        changed = {"UserAchievement"}
        received = []
        received_pcs = []
        received_memorias = []
        issued_stamina = 0
        for row, (table, id_key, item_id, amount), key in pending:
            if table == "UserMemoria":
                if amount != 1:
                    raise ValueError("Invalid original Memoria reward quantity")
                memorias = state["tables"].setdefault("UserMemoria", [])
                if not any((card["memoriaId"] == item_id for card in memorias)):
                    memorias.append(
                        dict(
                            userId=LOCAL_USER_ID,
                            memoriaId=item_id,
                            totalExp=0,
                            signature=0,
                        )
                    )
                    changed.add("UserMemoria")
                received_memorias.append(dict(memoriaId=item_id, level=1))
                row["state"] = 4
                ledger[key] = True
                continue
            if table == "UserPC":
                pcs = state["tables"].setdefault("UserPC", [])
                if not any((pc["pcId"] == item_id for pc in pcs)):
                    job, hp, mp, _ = offline_lotteries()["pcs"][str(item_id)]
                    pcs.append(user_pc(item_id, job, hp, mp))
                    changed.add("UserPC")
                    received_pcs.append(item_id)
                row["state"] = 4
                ledger[key] = True
                continue
            if table == "stamina":
                status = state["tables"]["UserInfo"][0]["playerStatus"]
                status["baseSP"] += amount
                issued_stamina += amount
                changed.add("UserInfo")
                row["state"] = 4
                ledger[key] = True
                continue
            if table == "LottoTicket":
                awards = state.setdefault("lottoTicketAwards", {})
                awards[str(item_id)] = awards.get(str(item_id), 0) + amount
                from danmemo.game.casino_lotto import ensure_tables as ensure_tables

                changed.update(ensure_tables(sys_module(), state))
                received.append({"itemId": item_id, "amount": amount})
                row["state"] = 4
                ledger[key] = True
                continue
            inventory = state["tables"].setdefault(table, [])
            item = next((item for item in inventory if item[id_key] == item_id), None)
            if item is None:
                item = {
                    "userId": LOCAL_USER_ID,
                    id_key: item_id,
                    "amount": 0,
                    "cumulativeAmount": 0,
                    "reservedAmount": 0,
                    "signature": 0,
                    "bookEntryState": 1,
                }
                inventory.append(item)
            if table == "UserGachaTicket":
                item.setdefault("dateList", []).extend(
                    ({"aquiredAt": int(time.time())} for _ in range(amount))
                )
                item.pop("amount", None)
                item.pop("reservedAmount", None)
            else:
                item["amount"] += amount
            item["cumulativeAmount"] = item.get("cumulativeAmount", 0) + amount
            row["state"] = 4
            ledger[key] = True
            changed.add(table)
            received.append({"itemId": item_id, "amount": amount})
        flow_repair_module().activate_rookie(sys_module(), state)
        flow_repair_module().reconcile_rookie_stages(sys_module(), state)
        tokens = {
            table: hashlib.md5(msgpack_value(state["tables"][table])).hexdigest()
            for table in changed
        }
        state["tokens"].update(tokens)
        from danmemo.game.mission_history import snapshot as snapshot_missions

        snapshot_missions(state)
        write_progress(state)
        data = {
            table: state["tables"][table]
            for table in changed
            if table == "UserAchievement"
            or (table == "UserInfo" and (not issued_stamina))
        }
        result = {
            "code": 0,
            "UserAchievement": [achievements[c["achievementId"]] for c in claims],
            "issueAmount": 0,
            "issueStamina": issued_stamina,
            "issueCasinoMedal": 0,
            "receivedItems": received,
            "receivedCostumes": [],
            "receivedPCs": received_pcs,
            "receivedMemorias": received_memorias,
            "data": data,
            "dataTokens": {table: tokens[table] for table in data},
            "operations": [],
        }
        if display_acknowledgement:
            display = dict(
                issueAmount=0,
                issueStamina=0,
                issueCasinoMedal=0,
                receivedItems=[],
                receivedCostumes=[],
                receivedPCs=[],
                receivedMemorias=[],
            )
            for table, id_key, item_id, amount in display_rewards:
                if table == "stamina":
                    display["issueStamina"] += amount
                elif table == "UserPC":
                    display["receivedPCs"].append(item_id)
                elif table == "UserMemoria":
                    display["receivedMemorias"].append(dict(memoriaId=item_id, level=1))
                else:
                    display["receivedItems"].append(dict(itemId=item_id, amount=amount))
            result["rewardDisplay"] = display
        return result


_STORY_STAGE_SUCCESSORS_GZ_B64 = "H4sIAAAAAAAC/23awY4cyRGD4XfZsw+dwcyqol/N8Lt7ZiXN9DdeYYFFglCogkVm/4f6z1/rtV4ffz7+9/HfX/9+P86/UEc1qlHd7+o4eZw8Th4nj5PHyXFynBwnx8lxct4nT5n8eRzVUY1qVH9M3qpH9aheqpfqrXqrPt/qfvF+P4+jOqpRjepW3apH9aheqpfqrXqrfm80JnZM7JjYMbFjYsfE/jqqHtWjeqleqve7Gp85PnN85vjM8ZnjM8dnjs8cnzk+c3zm91zNogt/H0d1VKMa1a26VY/qUb1UL9Ufz3yrPqqPar/UmKuYq5irmKuYq5irmKuYq5irmKuYq5irX0fV512NG8WN4kZxo7hR3ChuFDeKG8WN4kZxo7jR+xuMmYyZjJmMmYyZjJmMmYyZjJmMmYyZjJmMmYyZjJmMmdxmcpvJbSa3mdxmcpvJbSa3mdxmcpvJbSa3mdxmcpvJbSa3mdxmcpvJbSa3mdxmcpvJbSa3mdxmcpvJbSa3mdyv40bHjY4bHTc6bnTc6LjRcaPjRseNjhsdNzpudP5vo0e1qkVdr091/sapr8R+H0d1VKMa1f1bPU4+Tj5OPk4+Tj5MXi8mrxeT14vJ68Xk9WLyejF5bib/OY7qqEY1qu+Tx8nj5HHyOHmcPE6Ok+PkODlOjpPze3LMRsxGzEbMRsxGzEbMRsxGzEbMRsxGzEbMRsxGzEbMRsxGzEbMRsxGzEbMRsxGzMbW563PW5+3Pm993vr8dlQ9qkf1Ur1Ub9Vb9VF9VKta1F830ttRdanq1dKrpVdLr5ZeLb1aerX0aunV0qulV0uvll4tvVp6tfRq6dXo1ejV6NXo1fzx6pi6Y+qOqTum7pi6Y+qOqTum7pi6Y+qOqTum7pi6Q+ro/rb72+5vu7/t/rb7b0fVo3pUL9VL9Va9VX9s9Kj+zsZ4b4z3xnhvjPfGeG+M98Z4b4z3xnhvjPfGeG+M98Z4b4z3xnhvzPrHfYv6594Y743x3hjvjfHeGO+N8d4Y743x3uB3Yfu7sP1d2P4ubH8X9s/fha+j6lE9qpfqpXqr3qqP6qNa1aJ+vYXbt3D7Fm7fwu1buH0Lt2/h9i3c//QWll4tvVp6tfRq6dXSq6VXS6+WXi29Wno1ejV6NXo1ejV6NXo1ejV6NXo1ejV6NXo1ejV6NXo1ejV6NXo1ejV6Fb2KXkWvolfRq+hV/vaqH1fqer76+30c1VH98XejulW36lE9qpfqpXqr3qqP6qNa1aL+6u/bUXWp6tXSq6VXS6+WXi29Wnq19Grp1dKrpVdLr5ZeLb1aerX0aunV6NXo1ejV6NXo1ejV6NXo1ejV6NXo1ejV6NXo1eDV2IWxC2MXxi6MXRi7MHZh7MLYhbELYxfm9eOZb9VH9VGtKu937MLYhbELYxfGLoxdGLswdmHswtiFsQtjF8YujF0YuzB2YezC2IWxC2MXxi6MXRi7MHZh7MLYhbELYxfGLoxdGLswdmHswtiFsQuxC7ELsQuxC7ELsQuxC7ELsQuxC7ELef145lv1UX1Uq8r7jV2IXYhdiF2IXYhdiF2IXYhdiF2IXYhdiF2IXYhdiF2IXYhdiF2IXYhdiF2IXYhdiF2IXYhdiF2IXYhdiF2IXYhdiF3IWxcqI1VGqoxUGakyUmWkykiVkSojVUaqjFQZqTJSZaTKSJWRKiNVRqqMVBmpMlJlpMpIlZEqI1VGqoxUGakyUmWkykiVkSojVUaqjFQZqTJSZaTKSJWRKiNVRqqMVBmpMlJlpMpIlZEqI1VGqoxUGakyUmWkykiVkSojVUaqjFQZqTJSZaTKSJWRKiNVRqqMVBmpMlJlpMpIlZEqI1VGqoxUGakyUmWkykiVkSojVUaqjFQZqTJSZaTKSJWRKiNVRqqMVBmpMlJlpMpIlZEqI1VGqoxUGakyUmWkykiVkSojVUaqjFQZqTJSZaTKSJWRKiNVRqqMVBmpMlJlpMpIlZEqI1VGqoxUGakyUmWkykiVkSojVUaqjFQZqTJSZaTKSJWRKiNVRqqMVBmpMlJlpMpIlZEqI1VGqoxUGakyUmWkykiVkSojVUaqjNQ3Rvq89PPNSH+OozqqUY3qVt2qn8+8fn1u199fDL4dR3VUoxrVrbpVf/y7R/VSvVRv1Vv1M1cf2338+aLNt+OojmpUo/q50fj17Pj17Pj17Pj17Pj17Pj17PtR9age1Uv1Ur1Vb9VH9VH97OBs993uu913u+/1cTr319/9Ps5//wf2+fFmiSwAAA=="


@functools.cache
def story_stage_successors() -> dict[int, int]:
    return {int(k): v for k, v in stage_catalog()["successors"].items()}


@functools.cache
def stage_catalog() -> dict:
    return json.loads(
        gzip.decompress(
            (_paths.METADATA / "offline_stage_catalog.json.gz").read_bytes()
        )
    )


@functools.cache
def stage_groups() -> dict[tuple[int, int], list[int]]:
    groups = {}
    for sid, info in stage_catalog()["stages"].items():
        groups.setdefault(tuple(info[:2]), []).append(int(sid))
    for stages in groups.values():
        stages.sort(key=lambda sid: stage_catalog()["stages"][str(sid)][2])
    return groups


def unlock_stage_successor(rows: list[dict], stage_id: int) -> list[dict]:
    saved = {row["adventureStageId"]: row for row in rows}
    _unlock_stage_successor(saved, stage_id)
    return list(saved.values())


def _unlock_stage_successor(saved: dict[int, dict], stage_id: int) -> None:
    successor = story_stage_successors().get(stage_id)
    from danmemo.game import event_unlocks as offline_event_unlocks

    openings = [successor] if successor is not None else []
    openings.extend(offline_event_unlocks.openings(saved, stage_id))
    current = stage_catalog()["stages"].get(str(stage_id))
    if current is not None and current[1] in (1, 2):
        group = stage_groups().get(tuple(current[:2]), [])
        harder = stage_groups().get((current[0], current[1] + 1), [])
        if (
            group
            and harder
            and all((saved.get(sid, {}).get("state") == 4 for sid in group))
        ):
            openings.append(harder[0])
    for opening in openings:
        if not offline_event_unlocks.eligible(saved, opening):
            continue
        metadata = stage_catalog()["stages"].get(str(opening))
        if metadata is None:
            continue
        for sid in stage_groups()[tuple(metadata[:2])]:
            if sid in saved:
                continue
            info = stage_catalog()["stages"][str(sid)]
            saved[sid] = {
                "userId": LOCAL_USER_ID,
                "adventureStageId": sid,
                "state": 0,
                "tasks": [
                    {"adventureStageTaskId": task, "state": 0} for task in info[4]
                ],
                "ticketOpenedAt": 0,
                "ticketClosedAt": 0,
                "usedTicketId": 0,
                "firstTryDateTime": 0,
                "firstCompleteDateTime": 0,
                "lastTryDateTime": 0,
                "lastCompleteDateTime": 0,
                "token": "",
                "cumulativeClearCount": 0,
                "currentScore": 0,
                "highScore": 0,
                "dailyClearCount": 0,
            }
        if saved[opening]["state"] == 0:
            saved[opening]["state"] = 1


def migrate_stage_progress() -> None:
    with PROGRESS_LOCK:
        state = load_progress()
        if state.get("version", 0) >= 2:
            return
        completed_ids = {
            row["adventureStageId"]
            for key, response in state["completed"].items()
            if ":" not in key
            for row in response.get("data", {}).get("UserAdventureStage", [])
            if row.get("state") == 3 and row.get("cumulativeClearCount", 0) > 0
        }
        rows = state["tables"].get("UserAdventureStage", [])
        successors = {story_stage_successors().get(stage) for stage in completed_ids}
        for row in rows:
            if row["adventureStageId"] in completed_ids:
                row["state"] = 4
                row["tasks"] = [
                    dict(task, state=2) if task["state"] == 1 else task
                    for task in row.get("tasks", [])
                ]
            elif row["adventureStageId"] in successors and row["state"] == 0:
                row["state"] = 1
        if completed_ids:
            state["tokens"]["UserAdventureStage"] = hashlib.md5(
                msgpack_value(rows)
            ).hexdigest()
        state["completed"] = {
            key: value for key, value in state["completed"].items() if ":" in key
        }
        state["version"] = 2
        write_progress(state)


def repair_completed_stage_unlocks() -> None:
    with PROGRESS_LOCK:
        state = load_progress()
        rows = state["tables"].get("UserAdventureStage", [])
        before = [(row["adventureStageId"], row.get("state")) for row in rows]
        saved = {row["adventureStageId"]: row for row in rows}
        for row in list(rows):
            if row.get("state") == 4 and row.get("cumulativeClearCount", 0) > 0:
                _unlock_stage_successor(saved, row["adventureStageId"])
        rows = list(saved.values())
        gates_changed = repair_feature_gates(state)
        if [
            (row["adventureStageId"], row.get("state")) for row in rows
        ] != before or gates_changed:
            state["tables"]["UserAdventureStage"] = rows
            state["tokens"]["UserAdventureStage"] = hashlib.md5(
                msgpack_value(rows)
            ).hexdigest()
            write_progress(state)


def stage_complete_response(request: dict, request_id: str | None = None) -> dict:
    stage_id, token = (request.get("adventureStageId"), request.get("token"))
    skip_count = request.get("skipCount", 0)
    if type(skip_count) is not int or not 0 <= skip_count <= 999:
        raise ValueError("Invalid quest skip count")
    clear_count = max(1, skip_count)
    tasks = request.get("adventureStageTaskIds", request.get("tasks", []))
    cleared = request.get("isEnemyAnnihilated", True)
    if type(cleared) is not bool:
        raise ValueError("Invalid battle completion flag")
    record_attempt = (
        isinstance(request.get("userPvBScore"), dict)
        or isinstance(request.get("userGuildRaidScore"), dict)
        or isinstance(request.get("userGvEScore"), dict)
        or (
            type(request.get("unionRaidStageId")) is int
            and request["unionRaidStageId"] > 0
        )
    )
    completed_attempt = True
    if (
        type(stage_id) is not int
        or not isinstance(token, str)
        or (not isinstance(tasks, list))
        or any((type(task) is not int for task in tasks))
    ):
        raise ValueError("Unsupported local stage completion")
    with PROGRESS_LOCK:
        state = load_progress()
        from danmemo.game.stage_completion import repair_legacy as repair_legacy

        if repair_legacy(sys_module(), state):
            write_progress(state)
        rows = state["tables"].get("UserAdventureStage", [])
        stage = next(
            (row.copy() for row in rows if row["adventureStageId"] == stage_id), None
        )
        active = state["tables"].get("UserAdventureState", [{}])[0]
        field_attempt = (
            token == ""
            and stage is not None
            and (stage.get("state") in (1, 2, 4))
            and (stage.get("lastTryDateTime", 0) > 0)
            and (active.get("stageId") == stage_id)
            and active.get("events")
            and all((event.get("isFinished") for event in active["events"]))
        )
        raid_attempt = state.get("familiaRushNativeAttempts", {}).get(str(stage_id), {})
        extra_attempt = state.get("extraModeNativeAttempts", {}).get(str(stage_id), {})
        field_attempt = field_attempt or (
            token == ""
            and stage is not None
            and (extra_attempt.get("stamp", 0) > 0)
            and (extra_attempt["stamp"] == stage.get("lastTryDateTime"))
            and (extra_attempt.get("finished") is True)
            and (
                record_attempt or isinstance(request.get("userElementEventScore"), dict)
            )
        )
        field_attempt = field_attempt or (
            token == ""
            and stage is not None
            and (stage.get("lastTryDateTime", 0) > 0)
            and (raid_attempt.get("stamp") == stage.get("lastTryDateTime"))
            and (raid_attempt.get("finished") is True)
            and isinstance(request.get("userGuildRaidScore"), dict)
            and (
                raid_attempt.get("score") == request["userGuildRaidScore"]
                or (
                    type(request.get("guildRaidStageId")) is int
                    and request["guildRaidStageId"]
                    == request["userGuildRaidScore"].get("guildRaidStageId")
                    and (
                        request.get("bosses")
                        == request["userGuildRaidScore"].get("enemyDamage")
                    )
                )
            )
        )
        skip_attempt = (
            skip_count > 0
            and stage is not None
            and (stage.get("state") == 4)
            and (stage.get("cumulativeClearCount", 0) > 0)
            and (token == "" or token == stage.get("token"))
            and bool(request_id and request_id.strip(":"))
        )
        story_attempt = (
            stage is not None
            and (not stage.get("tasks"))
            and ("adventureStageTaskIds" not in request)
            and (stage.get("state") in (1, 4))
            and (stage_catalog()["stages"].get(str(stage_id), [0, 0, 0, 0])[3] == 51)
            and isinstance(request.get("adventureRecord"), dict)
            and (request["adventureRecord"].get("turnCount") == 0)
        )
        if (
            stage is None
            or (not token and (not field_attempt) and (not skip_attempt))
            or (
                stage.get("token") != token
                and (not story_attempt)
                and (not field_attempt)
                and (not skip_attempt)
            )
        ):
            raise ValueError("Stage token does not match the active attempt")
        if story_attempt:
            stage["token"] = token
        if not set(tasks).issubset(
            (row["adventureStageTaskId"] for row in stage.get("tasks", []))
        ):
            raise ValueError("Task does not belong to the active stage")
        attempt = f"{stage_id}:{token}:{stage.get('lastTryDateTime', 0)}"
        if skip_attempt:
            attempt = f"skip:{stage_id}:{request_id}"
        info = stage_catalog()["stages"].get(str(stage_id))
        shared_stories = []
        if info is not None and info[3] == 51 and (not tasks):
            for row in rows:
                other = stage_catalog()["stages"].get(str(row["adventureStageId"]))
                if (
                    row["adventureStageId"] != stage_id
                    and row.get("state") == 1
                    and (other is not None)
                    and (other[0] == info[0])
                    and (other[2:4] == info[2:4])
                    and (not row.get("tasks"))
                ):
                    shared_stories.append(row.copy())
        base_completed = attempt in state["completed"]
        if shared_stories:
            attempt += ":shared:" + ",".join(
                (str(r["adventureStageId"]) for r in shared_stories)
            )
        if attempt in state["completed"]:
            response = copy.deepcopy(state["completed"][attempt])
            if response.get("skipCount", 0) != skip_count:
                raise ValueError("Quest skip count does not match completed attempt")
            result_state = None
            if "userPvBScore" in request and "pvbResult" not in response:
                result_state = copy.deepcopy(state)
                arena_module().record_buster_result(
                    sys_module(),
                    result_state,
                    request,
                    stage.copy(),
                    response,
                    legacy=True,
                )
            if "userPVEScore" in request and "finalScore" not in response:
                result_state = copy.deepcopy(state)
                arena_module().monster_result(
                    sys_module(), result_state, request, stage.copy(), response
                )
            if "pvbResult" in response and "userPvbRecord" in response:
                response["pvbResult"].setdefault(
                    "pvbId", response["userPvbRecord"]["pvbId"]
                )
            response["UserAdventureStage"] = [stage]
            response.setdefault("adventureStageTaskIds", tasks)
            response["data"]["UserAdventureStage"] = rows
            response["dataTokens"]["UserAdventureStage"] = state["tokens"][
                "UserAdventureStage"
            ]
            for table in response["data"]:
                response["data"][table] = state["tables"][table]
                response["dataTokens"][table] = state["tokens"][table]
            if response.get("skipSPDebit") is not None:
                response["data"]["UserInfo"] = state["tables"]["UserInfo"]
                response["dataTokens"]["UserInfo"] = state["tokens"]["UserInfo"]
            else:
                response["data"].pop("UserInfo", None)
                response["dataTokens"].pop("UserInfo", None)
            response.pop("exp", None)
            if "userPVEScore" in request:
                from danmemo.game.monster_rewards import (
                    retry_inventory as retry_inventory,
                )

                retry_inventory(state, response)
            return response
        from danmemo.game import transactions as offline_transactions

        skip_sp_debit = None
        if skip_count and (not base_completed):
            if not skip_attempt:
                raise ValueError(
                    "Quest skip requires a cleared stage and request identity"
                )
            flow_repair_module().debit_skip(sys_module(), state, skip_count)
            from danmemo.game.stamina import debit_skip as debit_skip

            skip_sp_debit = debit_skip(
                sys_module(), state, stage_id, skip_count, int(time.time())
            )
        changed_drops = (
            offline_transactions.complete_drops(sys_module(), state, request)
            if not base_completed and completed_attempt
            else set()
        )
        from danmemo.game import progression as offline_progression

        before_exp = (
            next(iter(state["tables"].get("UserInfo", [])), {})
            .get("playerStatus", {})
            .get("totalExp", 0)
        )
        gained_exp, changed_exp = (
            offline_progression.award_exp(sys_module(), state, stage_id, clear_count)
            if not base_completed and completed_attempt
            else (0, set())
        )
        changed_drops.update(changed_exp)
        if not base_completed and completed_attempt:
            changed_drops.update(
                offline_progression.award_clear_rewards(
                    sys_module(), state, stage, tasks
                )
            )
        now = int(time.time())
        from danmemo.game.daily_windows import mission_day as mission_day

        for completed_stage in (
            ([stage] if not base_completed else []) + shared_stories
            if completed_attempt
            else []
        ):
            completed_stage.update(
                state=4,
                firstCompleteDateTime=completed_stage.get("firstCompleteDateTime")
                or now,
                lastCompleteDateTime=now,
                cumulativeClearCount=completed_stage.get("cumulativeClearCount", 0)
                + clear_count,
                dailyClearCount=(
                    completed_stage.get("dailyClearCount", 0)
                    if mission_day(completed_stage.get("lastCompleteDateTime", 0))
                    == mission_day(now)
                    else 0
                )
                + clear_count,
            )
        stage["tasks"] = [
            dict(row, state=2)
            if completed_attempt and row["adventureStageTaskId"] in tasks
            else row
            for row in stage.get("tasks", [])
        ]
        arena_result = {}
        monster_reward_tables = set()
        if "userPvBScore" in request:
            arena_module().record_buster_result(
                sys_module(), state, request, stage, arena_result
            )
            changed_drops.add("UserPvBRecord")
        if "userPVEScore" in request:
            monster_reward_tables = arena_module().monster_result(
                sys_module(), state, request, stage, arena_result
            )
        if completed_attempt and (not base_completed):
            from danmemo.game.mission_sync import record_clear as record_clear

            record_clear(
                state,
                stage_id,
                clear_count,
                now,
                arena_result.get("pvbResult", {}).get("finalScore", 0),
                stamina_spent=skip_sp_debit,
            )
        rows = [stage if row["adventureStageId"] == stage_id else row for row in rows]
        variants = {r["adventureStageId"]: r for r in shared_stories}
        rows = [variants.get(row["adventureStageId"], row) for row in rows]
        if completed_attempt:
            rows = unlock_stage_successor(rows, stage_id)
        for sid in variants:
            rows = unlock_stage_successor(rows, sid)
        checksum = hashlib.md5(msgpack_value(rows)).hexdigest()
        response = {
            "code": 0,
            "UserAdventureStage": [stage],
            "adventureStageTaskIds": tasks,
            "gainedItems": [],
            "gainedPCs": [],
            "gainedCostumes": [],
            "friendshipStates": [],
            "data": {"UserAdventureStage": rows},
            "dataTokens": {"UserAdventureStage": checksum},
            "operations": [],
        }
        if gained_exp:
            response["playerExpResult"] = dict(
                before=before_exp, after=before_exp + gained_exp
            )
        if skip_count:
            response["skipCount"] = skip_count
        if skip_sp_debit is not None:
            response["skipSPDebit"] = skip_sp_debit
            response["data"]["UserInfo"] = state["tables"]["UserInfo"]
            response["dataTokens"]["UserInfo"] = state["tokens"]["UserInfo"]
            response["data"]["UserSkipTicket"] = state["tables"]["UserSkipTicket"]
            response["dataTokens"]["UserSkipTicket"] = state["tokens"]["UserSkipTicket"]
        response.update(arena_result)
        for table in changed_drops:
            if table == "UserInfo" or table in monster_reward_tables:
                continue
            response["data"][table] = state["tables"][table]
            response["dataTokens"][table] = state["tokens"][table]
        state["tables"]["UserAdventureStage"] = rows
        state["tokens"]["UserAdventureStage"] = checksum
        if flow_repair_module().reconcile_rookie_stages(sys_module(), state):
            response["data"]["UserAchievement"] = state["tables"]["UserAchievement"]
            response["dataTokens"]["UserAchievement"] = state["tokens"][
                "UserAchievement"
            ]
        if repair_feature_gates(state):
            response["data"]["UserSystemFlag"] = state["tables"]["UserSystemFlag"]
            response["dataTokens"]["UserSystemFlag"] = state["tokens"]["UserSystemFlag"]
        state["completed"][attempt] = response
        write_progress(state)
        if monster_reward_tables:
            from danmemo.services import journal as test_journal

            test_journal.emit(
                "monster-reward",
                "pve/reward/complete",
                uid=LOCAL_USER_ID,
                request={"adventureStageId": stage_id},
                response={"code": 0, "receivedItems": response["gainedItems"]},
            )
        return response


def bond_push_requires_correction(state: dict, request: dict, table: str) -> bool:
    """Do not replay an accepted Bond snapshot over newer local spending.

    Original Bond userdata imports absolute stock. Unrelated outstanding gift
    acknowledgements must not force the entire inventory into every push ACK.
    Keep authoritative correction when a touched row was actually rejected.
    """
    expected = {}
    for delta in request.get("deltas", []):
        for row in delta.get("deleteItems", {}).get(table, []):
            expected[row["limitBreakItemId"]] = None
        for row in delta.get("putItems", {}).get(table, []):
            expected[row["limitBreakItemId"]] = row
    saved = {r["limitBreakItemId"]: r for r in state["tables"].get(table, [])}
    return any((saved.get(iid) != row for iid, row in expected.items()))


def push_response(value: dict) -> dict:
    deltas = value.get("deltas")
    checksums = value.get("checksums")
    if not isinstance(deltas, list) or not isinstance(checksums, dict):
        raise ValueError("Unsupported user-data push")
    after = checksums.get("after")
    if not isinstance(after, dict):
        raise ValueError("Unsupported user-data checksums")
    puts: dict[str, int] = {}
    deletes: dict[str, int] = {}
    triggers = []
    for delta in deltas:
        if not isinstance(delta, dict) or not isinstance(delta.get("trigger"), str):
            raise ValueError("Unsupported user-data delta")
        triggers.append(delta["trigger"])
        for result, name in ((puts, "putItems"), (deletes, "deleteItems")):
            items = delta.get(name)
            if not isinstance(items, dict):
                raise ValueError("Unsupported user-data items")
            for table, rows in items.items():
                if not isinstance(table, str) or not isinstance(rows, list):
                    raise ValueError("Unsupported user-data table")
                result[table] = result.get(table, 0) + len(rows)
    return {
        "code": 0,
        "triggers": triggers,
        "putItems": puts,
        "deleteItems": deletes or [],
        "data": {},
        "operations": [],
        "dones": [],
        "dataTokens": after,
    }


def pull_response(value: dict) -> bytes:
    tables = value.get("tables")
    if (
        not isinstance(tables, list)
        or not tables
        or (not all((isinstance(table, str) for table in tables)))
    ):
        raise ValueError("Unsupported user-data pull")
    if any((table in tables for table in ("UserItemToken", "UserRandomSeed"))):
        repair_field_resources()
    progress = load_progress()
    defaults = {
        **field_resources(),
        "UserArea": [
            {
                "userId": LOCAL_USER_ID,
                "areaId": 511011004,
                "lineBlockDwords": [0],
                "encountCount": 0,
            }
        ],
        "UserCommunicationCharacter": [
            {
                "userId": LOCAL_USER_ID,
                "characterId": 100000001,
                "signature": 0,
                "costumeId": 1022027000,
                "characterState": 2,
                "joinDateTime": 0,
                "touchCountBust": 0,
                "touchCountOther": 0,
            }
        ],
    }
    selected = list(dict.fromkeys(tables))
    data = {
        table: progress["tables"].get(table, defaults.get(table, []))
        for table in selected
    }
    if "UserArea" in data:
        info = next(iter(progress["tables"].get("UserInfo", [])), {})
        area_id = info.get("position", {}).get("areaId", 511011004)
        if (
            type(area_id) is int
            and area_id > 0
            and (not any((row.get("areaId") == area_id for row in data["UserArea"])))
        ):
            data["UserArea"] = [
                *data["UserArea"],
                {
                    "userId": LOCAL_USER_ID,
                    "areaId": area_id,
                    "lineBlockDwords": [0],
                    "encountCount": 0,
                },
            ]
    tokens = {
        table: progress["tokens"].get(
            table,
            HOME_CHARACTER_TOKEN
            if table == "UserCommunicationCharacter"
            else hashlib.md5(msgpack_value(rows)).hexdigest()
            if rows
            else "",
        )
        for table, rows in data.items()
    }
    if "UserArea" in data:
        tokens["UserArea"] = hashlib.md5(msgpack_value(data["UserArea"])).hexdigest()
    return msgpack_value(
        {"code": 0, "data": wire_tables(data), "dataTokens": tokens, "recovery": False}
    )


def wire_tables(data: dict) -> dict:
    return {
        table: rows[0]
        if table in SINGLETON_TABLES and isinstance(rows, list) and rows
        else rows
        for table, rows in data.items()
    }


def field_resources() -> dict[str, list[dict]]:
    return {
        "UserItemToken": [
            {
                "userId": LOCAL_USER_ID,
                "signature": i,
                "consumer": 0,
                "value": 0,
                "context": "",
                "verifier": "",
            }
            for i in range(1, 33)
        ],
        "UserRandomSeed": [
            {
                "userId": LOCAL_USER_ID,
                "signature": i,
                "seed": i * 48271 % 2147483647,
                "consumer": 0,
                "result": [],
                "verifier": "",
            }
            for i in range(1, 65)
        ],
    }


def repair_field_resources() -> None:
    with PROGRESS_LOCK:
        state = load_progress()
        bond_count = sum(
            (
                row.get("amount", 0) > 0
                for row in state["tables"].get("UserLimitBreakItem", [])
            )
        )
        for table, minimum in (
            ("UserItemToken", bond_count + 32),
            ("UserRandomSeed", 25),
        ):
            rows = state["tables"].setdefault(table, [])
            available = sum((row.get("consumer") == 0 for row in rows))
            if available >= minimum:
                continue
            start = max((row["signature"] for row in rows), default=0)
            templates = field_resources()[table]
            for i in range(1, max(len(templates), minimum - available) + 1):
                template = templates[(i - 1) % len(templates)]
                row = dict(template, signature=start + i)
                if table == "UserRandomSeed":
                    row["seed"] = secrets.randbelow(2147483646) + 1
                rows.append(row)
            state["tokens"][table] = hashlib.md5(msgpack_value(rows)).hexdigest()
        write_progress(state)


def repair_current_area() -> bool:
    with PROGRESS_LOCK:
        state = load_progress()
        info = next(iter(state["tables"].get("UserInfo", [])), {})
        area = info.get("position", {}).get("areaId", 511011004)
        if type(area) is not int or area < 0:
            raise ValueError("Invalid saved world area")
        if area == 0:
            return False
        rows = state["tables"].setdefault("UserArea", [])
        if any((row.get("areaId") == area for row in rows)):
            return False
        rows.append(
            dict(userId=LOCAL_USER_ID, areaId=area, lineBlockDwords=[0], encountCount=0)
        )
        state["tokens"]["UserArea"] = hashlib.md5(msgpack_value(rows)).hexdigest()
        write_progress(state)
        return True


def user_pc(pc_id: int, job_id: int, hp: int, mp: int) -> dict:
    return {
        "userId": LOCAL_USER_ID,
        "pcId": pc_id,
        "totalExp": 0,
        "hp": hp,
        "mp": mp,
        "destinyPoint": 0,
        "variableChantLevel": 1,
        "currentJobIndex": 1,
        "jobs": [
            {
                "pcJobId": job_id,
                "abilityPanels": [],
                "signature": 0,
                "abilityPanelState": 0,
            }
        ],
        "jobSlots": [
            {
                "jobSlotNumber": 1,
                "slotJobType": 1,
                "pcJobId": job_id,
                "state": 1,
                "signature": 0,
            }
        ],
        "skillSlots": [],
        "abilityPoint": 0,
        "bookEntryState": 1,
        "signature": 0,
        "entryState": 1,
        "joiningState": 1,
        "limitbreakCount": 0,
        "ownerType": 1,
        "equippedWeaponIndex": 0,
        "equippedArmorIndex": 0,
        "attackPattern": 2,
        "heroSublimateLevel": 0,
        "currentDP": 0,
        "isUnlockedHeroSublimate": False,
        "battlePlayCount": 0,
    }


def starter_pcs() -> list[dict]:
    return [
        user_pc(pc_id, job_id, 100, 10)
        for pc_id, job_id in ((1011001001, 106000101), (1012027001, 106000051))
    ]


_TUTORIAL_PC_JOBS_GZ_B64 = b"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"


@functools.cache
def tutorial_pc_jobs() -> dict[int, int]:
    cache_file = WORK / "tutorial_pc_jobs.json"
    if cache_file.is_file():
        try:
            return {
                int(k): int(v)
                for k, v in json.loads(cache_file.read_text("utf-8")).items()
            }
        except Exception:
            pass
    raw = gzip.decompress(base64.b64decode(_TUTORIAL_PC_JOBS_GZ_B64))
    return {int(k): int(v) for k, v in json.loads(raw).items()}


_TUTORIAL_PC_STATS_GZ_B64 = "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"


@functools.cache
def tutorial_pc_stats() -> dict[int, list[int]]:
    raw = gzip.decompress(base64.b64decode(_TUTORIAL_PC_STATS_GZ_B64))
    return {int(k): v for k, v in json.loads(raw).items()}


_OFFLINE_LOTTERY_GZ_B64 = "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"


@functools.cache
def raw_lottery_catalog() -> dict:
    catalog = _paths.METADATA / "offline_lottery_catalog.json.gz"
    if catalog.is_file():
        data = json.loads(gzip.decompress(catalog.read_bytes()))
    else:
        data = json.loads(gzip.decompress(base64.b64decode(_OFFLINE_LOTTERY_GZ_B64)))
    ticket_catalog = _paths.METADATA / "offline_ticket_catalog.json.gz"
    if ticket_catalog.exists():
        recovered = json.loads(gzip.decompress(ticket_catalog.read_bytes()))
        data["lotteries"].update(recovered["lotteries"])
        data["pcs"].update(recovered["pcs"])
    return data


@functools.cache
def offline_lotteries() -> dict:
    data = raw_lottery_catalog()
    from danmemo.game import lottery_rates as offline_lottery_rates
    from danmemo.content import notice as offline_notice
    from danmemo.game import lottery_guarantees as offline_lottery_guarantees

    return offline_lottery_rates.apply(
        offline_lottery_guarantees.apply(data), offline_notice.catalog()
    )


def tutorial_draw_response(value: dict) -> dict:
    if (
        type(value.get("lotteryId")) is not int
        or type(value.get("pcId")) is not int
        or value["lotteryId"] != 171001765
    ):
        raise ValueError("Unsupported local tutorial selection")
    pc_id = value["pcId"]
    jobs = tutorial_pc_jobs()
    if pc_id not in jobs:
        raise ValueError(f"Unsupported local tutorial selection: {pc_id}")
    job_id = jobs[pc_id]
    hp, mp = tutorial_pc_stats()[pc_id]
    new_pc = user_pc(pc_id, job_id, hp, mp)
    tutorials = {
        row["tutorialId"]: row
        for row in load_progress()["tables"].get("UserTutorial", [])
    }
    for tutorial_id in (
        *range(780010001, 780010007),
        780010099,
        *range(780030001, 780030006),
        780030099,
    ):
        tutorials[tutorial_id] = {
            "userId": LOCAL_USER_ID,
            "tutorialId": tutorial_id,
            "isEnd": True,
        }
    tutorial_rows = list(tutorials.values())
    return {
        "code": 0,
        "lotteryPCs": [{"pcId": pc_id, "isFirst": True}],
        "UserPC": [new_pc],
        "UserCommunicationCharacter": [],
        "UserHeroSublimatePCs": [],
        "data": {"UserTutorial": tutorial_rows},
        "dataTokens": {
            "UserTutorial": hashlib.md5(msgpack_value(tutorial_rows)).hexdigest()
        },
        "operations": [],
    }


def repair_lottery_guarantees() -> None:
    with PROGRESS_LOCK:
        state = load_progress()
        if not state.get("lotteryDraws"):
            return
        ledger = state.get("lotteryGuaranteeRepairs", {})
        pending = []
        for key, draw in state["lotteryDraws"].items():
            lid, button, count = map(int, key.split(":")[:3])
            if button == 3 and key not in ledger:
                pending.append((key, draw, lid, count))
        if not pending:
            return
        master = offline_lotteries()
        ledger = state.setdefault("lotteryGuaranteeRepairs", {})
        pcs = bonds = None
        repaired = False
        changed = False
        for key, draw, lid, count in pending:
            banner = master["lotteries"].get(str(lid), {})
            pool = banner.get("guarantees", {}).get(str(count + 1))
            if not pool:
                continue
            allowed = {pc for _, group in pool for pc in group}
            if draw.get("lotteryPCs") and draw["lotteryPCs"][-1]["pcId"] in allowed:
                ledger[key] = {"compensated": False}
                changed = True
                continue
            if pcs is None:
                rows = (
                    state["tables"]["UserPC"]
                    if "UserPC" in state["tables"]
                    else login_pcs()
                )
                pcs = {r["pcId"]: r for r in rows}
                bonds = {
                    r["limitBreakItemId"]: r
                    for r in state["tables"].get("UserLimitBreakItem", [])
                }
            group = secrets.SystemRandom().choices(
                pool, weights=[g[0] for g in pool], k=1
            )[0]
            pc_id = secrets.choice(group[1])
            job, hp, mp, bond_id = master["pcs"][str(pc_id)]
            if pc_id not in pcs:
                pcs[pc_id] = user_pc(pc_id, job, hp, mp)
            else:
                bond = bonds.setdefault(
                    bond_id,
                    {
                        "userId": LOCAL_USER_ID,
                        "limitBreakItemId": bond_id,
                        "amount": 0,
                        "cumulativeAmount": 0,
                        "reservedAmount": 0,
                        "signature": 0,
                        "bookEntryState": 1,
                    },
                )
                bond["amount"] += 1
                bond["cumulativeAmount"] += 1
            ledger[key] = {"compensated": True, "pcId": pc_id}
            repaired = True
            changed = True
        if repaired:
            state["tables"]["UserPC"] = list(pcs.values())
            state["tables"]["UserLimitBreakItem"] = list(bonds.values())
            for table in ("UserPC", "UserLimitBreakItem"):
                state["tokens"][table] = hashlib.md5(
                    msgpack_value(state["tables"][table])
                ).hexdigest()
        if changed:
            write_progress(state)


@functools.cache
def lottery_ascension_pickups() -> dict[int, set[int]]:
    from danmemo.game import lottery_ascension as original

    result = {}
    for lid, banner in original.catalog().items():
        pools = list(banner["pools"].values()) + list(banner["guarantees"].values())
        pools += [p for step in banner["steps"].values() for p in step.values()]
        if any(pools):
            result[lid] = set().union(*pools)
    return result


def lottery_draw_response(request: dict) -> dict:
    master = offline_lotteries()
    lottery_id, button = (request.get("lotteryId"), request.get("buttonIndex"))
    step_index = request.get("currentStepIndex")
    if (
        type(lottery_id) is not int
        or type(button) is not int
        or type(step_index) is not int
    ):
        raise ValueError("Invalid lottery identifiers")
    from danmemo.game import private_purchase as private_purchase

    button_master = (
        private_purchase.lottery_buttons().get(lottery_id, {}).get(button, {})
    )
    if lottery_id == 171002049:
        expected = {
            "lotteryId": lottery_id,
            "buttonIndex": 2,
            "pickCount": 11,
            "consumeAmount": 0,
            "currentStepIndex": 0,
        }
        banner = master["lotteries"][str(lottery_id)]
        normal, fixed_pool, fixed = (
            banner["pools"]["7"],
            banner["pools"]["25"],
            banner["fixedCount"],
        )
    elif (
        str(lottery_id) in master["lotteries"]
        and button == 1
        and button_master.get("picks")
    ):
        banner = master["lotteries"][str(lottery_id)]
        if banner.get("ticketId") or step_index not in (0, 1):
            raise ValueError("Invalid daily Iris draw")
        expected = dict(
            lotteryId=lottery_id,
            buttonIndex=1,
            pickCount=button_master["picks"],
            consumeAmount=button_master["cost"],
            currentStepIndex=step_index,
        )
        normal, fixed_pool, fixed = (banner["pools"]["7"], [], 0)
    elif str(lottery_id) in master["lotteries"] and button in (2, 3):
        banner = master["lotteries"][str(lottery_id)]
        if banner.get("ticketId"):
            expected = {
                "lotteryId": lottery_id,
                "buttonIndex": 2,
                "pickCount": banner["singlePickCount"],
                "consumeAmount": banner["singleCost"],
                "currentStepIndex": 0,
            }
            normal, fixed_pool, fixed = (
                banner["pools"]["7"],
                banner["pools"].get("25", []),
                banner["fixedCount"],
            )
        elif button == 3 and banner["steps"]:
            step = banner["steps"].get(str(step_index))
            if step is None:
                raise ValueError("Invalid lottery step")
            expected = {
                "lotteryId": lottery_id,
                "buttonIndex": button,
                "pickCount": step["pickCount"],
                "consumeAmount": step["cost"],
                "currentStepIndex": step_index,
            }
            normal, fixed_pool, fixed = (
                step["normal"],
                step["fixed"],
                step["fixedCount"],
            )
        else:
            if step_index not in (0, 1):
                raise ValueError("Invalid non-step lottery index")
            expected = {
                "lotteryId": lottery_id,
                "buttonIndex": button,
                "pickCount": banner["singlePickCount"]
                if button == 2
                else banner.get("multiPickCount", 11),
                "consumeAmount": banner["singleCost"]
                if button == 2
                else banner["multiCost"],
                "currentStepIndex": step_index,
            }
            normal = banner["pools"]["7"]
            fixed_pool = banner["pools"].get("35" if button == 3 else "25", [])
            fixed = banner["multiFixedCount"] if button == 3 else banner["fixedCount"]
            if expected["pickCount"] <= 0:
                raise ValueError("Gacha button is unavailable")
    else:
        raise ValueError("Unsupported offline lottery draw")
    if (
        any(
            (
                type(request.get(k)) is not int or request[k] != v
                for k, v in expected.items()
            )
        )
        or request.get("isScratch") is not False
    ):
        raise ValueError("Unsupported offline lottery draw")
    repeats = request.get("drawCount", 1)
    if (
        type(repeats) is not int
        or not 1 <= repeats <= 100
        or (not banner.get("ticketId") and repeats != 1)
    ):
        raise ValueError("Invalid ticket draw count")
    selected_receipt = {}
    if banner.get("selection"):
        from danmemo.game import ticket_lineup as offline_ticket_lineup

        normal, fixed_pool, selected_receipt = offline_ticket_lineup.selected_pools(
            banner, request
        )
    count = request.get("progressCount")
    if type(count) is not int or count < 0:
        raise ValueError("Invalid lottery progress")
    with PROGRESS_LOCK:
        state = load_progress()
        key = f"{lottery_id}:{button}:{count}"
        if button == 1 and button_master.get("daily"):
            key += ":" + str((int(time.time()) + 9 * 3600) // 86400)
        cached = state.setdefault("lotteryDraws", {}).get(key)
        if cached is not None:
            receipt = state.get("ticketDrawRequests", {}).get(key)
            if receipt is not None and receipt != {
                **expected,
                "drawCount": repeats,
                **selected_receipt,
            }:
                raise ValueError("Ticket retry parameters do not match the saved draw")
            return cached
        lotteries = {r["lotteryId"]: r for r in state["tables"].get("UserLottery", [])}
        lottery = lotteries.setdefault(
            lottery_id,
            {"userId": LOCAL_USER_ID, "lotteryId": lottery_id, "buttons": []},
        )
        previous = next(
            (r for r in lottery["buttons"] if r["buttonType"] == button), None
        )
        saved_count = previous["count"] if previous else 0
        if button == 1 and button_master.get("daily") and previous:
            if (previous.get("lastPickedAt", 0) + 9 * 3600) // 86400 != (
                int(time.time()) + 9 * 3600
            ) // 86400:
                saved_count = 0
        if (
            button == 1
            and button_master.get("limit")
            and (saved_count >= button_master["limit"])
        ):
            raise ValueError("Daily Iris draw limit reached")
        if (
            button != 1
            and (not banner.get("ticketId"))
            and button_master.get("limit")
            and (saved_count + repeats > button_master["limit"])
        ):
            raise ValueError("Iris gacha draw limit reached")
        if count != saved_count or (lottery_id == 171002049 and saved_count != 0):
            raise ValueError("Invalid lottery progress or consumed free draw")
        is_step = button == 3 and bool(banner["steps"])
        if is_step and step_index != min(
            saved_count + 1, max(map(int, banner["steps"]))
        ):
            raise ValueError("Lottery step does not match saved progress")
        guarantee = banner.get("guarantees", {}).get(str(saved_count + 1))
        if button == 3 and guarantee:
            fixed_pool, fixed = (guarantee, max(1, fixed))
        cost = expected["consumeAmount"] * repeats
        currencies = {
            r["currencyId"]: r for r in state["tables"].get("UserCurrency", [])
        }
        currency = currencies.get(205000001)
        ticket_id = banner.get("ticketId", 0)
        cost_table = "UserGachaTicket" if ticket_id else "UserCurrency"
        if ticket_id:
            currency = next(
                (
                    r
                    for r in state["tables"].get(cost_table, [])
                    if r["gachaTicketId"] == ticket_id
                ),
                None,
            )
        available_amount = 0
        if currency is not None:
            available_amount = (
                len(currency["dateList"])
                if ticket_id and "dateList" in currency
                else currency.get("amount", 0) - currency.get("reservedAmount", 0)
            )
        paid = state.get("privateIrisWallet", {}).get("paid", 0)
        paid_only = not ticket_id and button_master.get("consumeType") == 3
        available = (
            available_amount
            if ticket_id
            else paid
            if paid_only
            else available_amount + paid
        )
        if cost and available < cost:
            raise ValueError("Insufficient offline Iris")
        if cost:
            if ticket_id and "dateList" in currency:
                del currency["dateList"][:cost]
            elif not ticket_id:
                free_spent = 0 if paid_only else min(available_amount, cost)
                if free_spent:
                    currency["amount"] -= free_spent
                if cost > free_spent:
                    state.setdefault("privateIrisWallet", dict(paid=0))["paid"] -= (
                        cost - free_spent
                    )
                state.setdefault("lotteryIrisSpends", {})[key] = dict(
                    paid=cost - free_spent, free=free_spent, at=int(time.time())
                )
            else:
                currency["amount"] -= cost
        rng = secrets.SystemRandom()
        from danmemo.game import lottery_ascension as ascension

        normal_ascension, fixed_ascension = ascension.selected(
            lottery_id, button, step_index, saved_count
        )

        def pick(pool, eligible):
            group = rng.choices(pool, weights=[part[0] for part in pool], k=1)[0]
            pid = rng.choice(group[1])
            return (pid, pid in eligible)

        drawn = []
        for _ in range(repeats):
            drawn += [
                pick(normal, normal_ascension)
                for _ in range(expected["pickCount"] - fixed)
            ]
            drawn += [pick(fixed_pool, fixed_ascension) for _ in range(fixed)]
        pcs = {r["pcId"]: r for r in state["tables"].get("UserPC", login_pcs())}
        bonds = {
            r["limitBreakItemId"]: r
            for r in state["tables"].get("UserLimitBreakItem", [])
        }
        new_pcs, lottery_pcs, changed_bonds = ([], [], set())
        ascended = []
        for pc_id, unlock_ascension in drawn:
            job, hp, mp, bond = master["pcs"][str(pc_id)]
            first = pc_id not in pcs
            lottery_pcs.append({"pcId": pc_id, "isFirst": first})
            if first:
                row = user_pc(pc_id, job, hp, mp)
                pcs[pc_id] = row
                new_pcs.append(row)
            else:
                row = bonds.setdefault(
                    bond,
                    {
                        "userId": LOCAL_USER_ID,
                        "limitBreakItemId": bond,
                        "amount": 0,
                        "signature": 0,
                    },
                )
                row["amount"] += 1
                changed_bonds.add(bond)
            if unlock_ascension and (
                not pcs[pc_id].get("isUnlockedHeroSublimate", False)
            ):
                pcs[pc_id]["isUnlockedHeroSublimate"] = True
                ascended.append(pc_id)
        if previous is None:
            previous = {"buttonType": button}
            lottery["buttons"].append(previous)
        previous.update(
            {
                "count": saved_count + repeats,
                "lastPickedAt": int(time.time()),
                "loopCount": 0,
                "currentStepIndex": min(step_index + 1, max(map(int, banner["steps"])))
                if is_step
                else step_index,
            }
        )
        state["tables"]["UserPC"] = list(pcs.values())
        state["tables"]["UserLimitBreakItem"] = list(bonds.values())
        state["tables"]["UserLottery"] = list(lotteries.values())
        if cost and (not ticket_id):
            state["tables"]["UserCurrency"] = list(currencies.values())
        updated = ["UserPC", "UserLimitBreakItem", "UserLottery"] + (
            [cost_table] if cost else []
        )
        for table in updated:
            state["tokens"][table] = hashlib.md5(
                msgpack_value(state["tables"][table])
            ).hexdigest()
        result = {
            "code": 0,
            "lotteryPCs": lottery_pcs,
            "UserPC": new_pcs,
            "UserLottery": [lottery],
            "UserLimitBreakItem": [bonds[k] for k in sorted(changed_bonds)],
            "UserCommunicationCharacter": [],
            "UserHeroSublimatePCs": ascended,
            "data": {cost_table: state["tables"][cost_table]} if cost else {},
            "dataTokens": {cost_table: state["tokens"][cost_table]} if cost else {},
            "operations": [],
        }
        state["lotteryDraws"][key] = result
        if ticket_id:
            state.setdefault("ticketDrawRequests", {})[key] = {
                **expected,
                "drawCount": repeats,
                **selected_receipt,
            }
        write_progress(state)
        return result


def login_pcs() -> list[dict]:
    if TUTORIAL_DRAW_STATE.is_file():
        selection = json.loads(TUTORIAL_DRAW_STATE.read_text(encoding="utf-8"))
        return starter_pcs() + tutorial_draw_response(selection)["UserPC"]
    return starter_pcs()


def save_tutorial_draw(selection: dict) -> None:
    TUTORIAL_DRAW_STATE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=TUTORIAL_DRAW_STATE.parent,
        suffix=".tmp",
    ) as output:
        json.dump(
            {"lotteryId": selection["lotteryId"], "pcId": selection["pcId"]}, output
        )
        temp_path = Path(output.name)
    try:
        temp_path.replace(TUTORIAL_DRAW_STATE)
    finally:
        temp_path.unlink(missing_ok=True)


def lottery_notice(request: dict) -> dict:
    if "eventBoxId" in request:
        from danmemo.game import event_box as offline_event_box

        return offline_event_box.notice(sys_module(), request)
    lottery_id = request.get("lotteryId")
    if (
        type(lottery_id) is not int
        or str(lottery_id) not in offline_lotteries()["lotteries"]
    ):
        raise ValueError("Unknown local lottery notice")
    banner = offline_lotteries()["lotteries"][str(lottery_id)]
    from danmemo.content import notice as offline_notice
    from danmemo.content import gacha_details as offline_gacha_details
    from danmemo.game import lottery_ascension as offline_lottery_ascension

    sections = offline_gacha_details.render(
        lottery_id,
        banner,
        offline_notice.catalog(),
        offline_lottery_ascension.catalog(),
    )
    period = (
        schedule_module()
        .config()["banners"]
        .get(str(lottery_id), offline_notice.catalog()["banners"][str(lottery_id)])
    )
    page = offline_notice.render(lottery_id, ENDPOINT, period, sections)
    return {"code": 0, "html": page}


def iris_history(offset: int, limit: int) -> dict:
    transactions = []
    master = offline_lotteries()["lotteries"]
    snapshot = load_progress()
    for key, receipt in snapshot.get("lotteryDraws", {}).items():
        lid, button, count = map(int, key.split(":")[:3])
        banner = master.get(str(lid))
        if not banner or banner.get("ticketId") or lid == 171002049:
            continue
        lottery = next(
            (row for row in receipt.get("UserLottery", []) if row["lotteryId"] == lid),
            None,
        )
        saved = next(
            (
                row
                for row in (lottery or {}).get("buttons", [])
                if row["buttonType"] == button
            ),
            None,
        )
        if not saved:
            continue
        step = None
        if button == 3 and banner["steps"]:
            step = banner["steps"][str(min(count + 1, max(map(int, banner["steps"]))))]
        cost = (
            step["cost"]
            if step
            else banner["singleCost" if button == 2 else "multiCost"]
        )
        if button == 1:
            from danmemo.game.private_purchase import lottery_buttons as lottery_buttons

            cost = lottery_buttons().get(lid, {}).get(1, {}).get("cost", 0)
        if not cost:
            continue
        spend = snapshot.get("lotteryIrisSpends", {}).get(key, dict(paid=0, free=cost))
        transactions.append(
            {
                "transaction_type": 1,
                "date": time.strftime(
                    "%Y-%m-%dT%H:%M:%S",
                    time.gmtime(spend.get("at", saved["lastPickedAt"])),
                ),
                "reason": f"Gacha ({len(receipt['lotteryPCs'])} draws)",
                "balance_change_charge": -spend["paid"],
                "balance_change_free": -spend["free"],
                "price": 0,
                "formatted_price": "",
                "expire_charge": "",
                "expire_free": "",
            }
        )
    transactions.sort(key=lambda row: row["date"], reverse=True)
    return {
        "result": "OK",
        "entry": {
            "offset": offset,
            "limit": limit,
            "has_next": offset + limit < len(transactions),
            "transactions": transactions[offset : offset + limit],
        },
    }


def api_response(
    action: str,
    request_body: bytes = b"",
    request_id: str | None = None,
    *,
    reward_preflight: bool = True,
    progress_snapshot: dict | None = None,
) -> tuple[str, bytes]:
    if reward_preflight and action in (
        "user_data/confirm",
        "user_data/pull",
        "user_data/push",
        "achievement/complete",
    ):
        from danmemo.game import reward_delivery as offline_reward_delivery

        offline_reward_delivery.run(sys_module())
    if action in ("casino/ranking/get", "casino/ranking/update", "takarakuji/get"):
        from danmemo.game import casino_lotto as casino_lotto
        from danmemo.game.multiplayer import SharedWorld as SharedWorld

        world = getattr(sys_module(), "ONLINE_SHARED", None)
        if world is None:
            world = SharedWorld(PROGRESS_STATE.parent / "casino.sqlite3", LOCAL_USER_ID)
        if action != "takarakuji/get":
            world.publish(load_progress())
        value = casino_lotto.dispatch(
            world, action, decode_request(request_body), sys_module()
        )
    elif action == "election/vote":
        from danmemo.game import campaign_features as campaign_features
        from danmemo.game.multiplayer import SharedWorld as SharedWorld

        world = getattr(sys_module(), "ONLINE_SHARED", None) or SharedWorld(
            PROGRESS_STATE.parent / "casino.sqlite3", LOCAL_USER_ID
        )
        value = campaign_features.dispatch(
            world, decode_request(request_body), sys_module(), request_id
        )
    elif action == "currency/hard/consume":
        from danmemo.game.pass_recovery import recover as recover

        value = recover(sys_module(), decode_request(request_body), request_id)
    elif action == "shop/buy":
        from danmemo.game import transactions as offline_transactions

        value = offline_transactions.buy(
            sys_module(), decode_request(request_body), request_id
        )
    elif action == "familia/mystatus":
        value = {
            "code": 0,
            "result": 0,
            "status": {"familiaId": 0, "recievedRequest": False},
            "data": {},
            "dataTokens": {},
            "operations": [],
        }
    elif action in (
        "familia/search/random",
        "familia/search/name",
        "familia/search/option",
        "familia/invited/list",
        "familia/join/list/sent",
    ):
        value = {"code": 0, "familias": [], "requests": []}
    elif action == "achievement/familia/get":
        value = {
            "code": 0,
            "records": {"3001": 0, "3101": 0, "3102": 0},
            "daily_achievements": [],
        }
    elif action == "achievement/familia/add":
        request = decode_request(request_body)
        if type(request.get("recordType")) is not int or request["recordType"] != 401:
            raise ValueError("Unsupported offline Familia achievement record")
        value = {"code": 0, "data": {}, "dataTokens": {}, "operations": []}
    elif action == "friend/support/list":
        value = {"code": 0, "is_fullset": True, "supporters": []}
    elif action == "friend/get":
        request = decode_request(request_body)
        if type(request.get("userId")) is not int or request["userId"] <= 0:
            raise ValueError("Invalid friend ID")
        value = {"code": 0, "result": 10000003}
    elif action in ("friend/following/list", "friend/follower/list"):
        request = decode_request(request_body)
        page = request.get("page", 1)
        if type(page) is not int or page < 1:
            raise ValueError("Invalid friend list page")
        value = {
            "code": 0,
            "followingCount": 0,
            "followersCount": 0,
            "current": page,
            "pageCount": 0,
            "following": [],
            "followers": [],
            "followerListLastViewedAt": int(time.time()),
        }
    elif action == "matching_user/game_user_id":
        value = {"code": 0, "game_user_id": LOCAL_USER_ID, "aesIv": LOCAL_AES_IV}
    elif action == "user/login":
        from danmemo.game.equipment_state import repair_links as repair_links

        repair_links(sys_module())
        from danmemo.game import login_bonuses as login_bonuses

        login_bonuses.prepare(sys_module())
        from danmemo.game import party_state as offline_party_state

        offline_party_state.recover_missing(sys_module())
        from danmemo.game import campaign_features as campaign_features

        campaign_features.prepare(sys_module())
        from danmemo.game import casino_lotto as casino_lotto

        casino_lotto.prepare(sys_module())
        from danmemo.game import event_box as offline_event_box
        from danmemo.game import transactions as offline_transactions
        from danmemo.game import gifts as offline_gifts

        offline_gifts.repair_legacy_enums(sys_module())
        offline_transactions.repair_currencies(sys_module())
        offline_transactions.repair_recipes(sys_module())
        repair_character_jobs()
        repair_field_resources()
        repair_current_area()
        repair_special_arts_unlock()
        repair_ticket_inventory()
        repair_battle_auto_default()
        from danmemo.game import progression as offline_progression

        offline_progression.record_login(sys_module())
        from danmemo.game import reward_delivery as offline_reward_delivery

        offline_reward_delivery.run(sys_module())
        offline_event_box.repair_resources(sys_module())
        pcs = login_pcs()
        progress = load_progress()
        from danmemo.game.stage_completion import repair_legacy as repair_legacy

        repair_legacy(sys_module(), progress)
        flow_repair_module().activate_rookie(sys_module(), progress)
        flow_repair_module().reconcile_rookie_stages(sys_module(), progress)
        progress["eventBoxSession"] = secrets.token_hex(16)
        write_progress(progress)
        LOGIN_REFRESH_TOKENS.clear()
        nonce = os.urandom(16).hex()
        LOGIN_REFRESH_TOKENS.update(
            {
                table: hashlib.md5((nonce + table).encode()).hexdigest()
                for table in progress["tables"]
            }
        )
        data = {"UserPC": pcs, **field_resources(), **progress["tables"]}
        value = {
            "code": 0,
            "apiUrl": f"{ENDPOINT}/api",
            "cdnUrl": f"{ENDPOINT}/content",
            "game_user_id": LOCAL_USER_ID,
            "aesIv": LOCAL_AES_IV,
            "userDataPullMultiRequestEnabled": True,
            "warningId": 0,
            "warningTitle": "",
            "warningMessage": "",
            "agreePolicyVersionUpdate": False,
            "data": data,
            "dataTokens": {
                table: progress["tokens"].get(table)
                or hashlib.md5(msgpack_value(rows)).hexdigest()
                for table, rows in data.items()
            },
        }
    elif action == "user_data/confirm":
        with PROGRESS_LOCK:
            progress = (
                progress_snapshot
                if progress_snapshot is not None and (not reward_preflight)
                else load_progress()
            )
            progress["tokens"].update(LOGIN_REFRESH_TOKENS)
            write_progress(progress)
        value = {
            "code": 0,
            "dataTokens": {
                "UserCommunicationCharacter": HOME_CHARACTER_TOKEN,
                **progress["tokens"],
                **LOGIN_REFRESH_TOKENS,
            },
            "migrators": [],
            "operations": [],
        }
        LOGIN_REFRESH_TOKENS.clear()
    elif action == "user_data/push":
        request = decode_request(request_body)
        value = push_response(request)
        persist_progress(request)
        repair_special_arts_unlock()
        state = load_progress()
        for table in set(state.get("giftInventoryReconciliation", {})) | set(
            state.get("familiaInventoryReconciliation", {})
        ):
            if table in ("UserLimitBreakItem", "UserGeneralLimitBreakItem") and (
                not bond_push_requires_correction(state, request, table)
            ):
                continue
            value["data"][table] = state["tables"].get(table, [])
            value["dataTokens"][table] = state["tokens"].get(table, "")
        if state.get("adminGiftItems"):
            value["data"]["UserGift"] = state["tables"].get("UserGift", [])
            value["dataTokens"]["UserGift"] = state["tokens"].get("UserGift", "")
        if "UserInfo" in request["checksums"]["after"]:
            value["data"]["UserInfo"] = state["tables"].get("UserInfo", [])
            value["dataTokens"]["UserInfo"] = state["tokens"].get("UserInfo", "")
        if (
            state.get("skipTicketReconciliation")
            or "UserSkipTicket" in request["checksums"]["after"]
        ):
            value["data"]["UserSkipTicket"] = state["tables"].get("UserSkipTicket", [])
            value["dataTokens"]["UserSkipTicket"] = state["tokens"].get(
                "UserSkipTicket", ""
            )
        if (
            "UserAchievement" in request["checksums"]["after"]
            or "UserAdventureStage" in request["checksums"]["after"]
        ):
            value["data"]["UserAchievement"] = state["tables"].get(
                "UserAchievement", []
            )
            value["dataTokens"]["UserAchievement"] = state["tokens"].get(
                "UserAchievement", ""
            )
        for table in ("UserShopProduct", "UserCharacterCostume", "UserUnionRaidMedal"):
            if table in request["checksums"]["after"]:
                if table == "UserShopProduct":
                    from danmemo.game.shop_limits import (
                        push_requires_correction as push_requires_correction,
                    )

                    if not push_requires_correction(state, request):
                        continue
                value["data"][table] = state["tables"].get(table, [])
                value["dataTokens"][table] = state["tokens"].get(table, "")
        for table in ("UserLoginBonus", "UserLoginBonusProgress"):
            if table in request["checksums"]["after"]:
                value["data"][table] = state["tables"].get(table, [])
                value["dataTokens"][table] = state["tokens"].get(table, "")
    elif action == "user_data/pull":
        return ("application/x-msgpack", pull_response(decode_request(request_body)))
    elif action == "gift/receive":
        from danmemo.game import gifts as offline_gifts

        value = offline_gifts.receive(sys_module(), decode_request(request_body))
    elif action == "tutorial/draw":
        selection = decode_request(request_body)
        value = tutorial_draw_response(selection)
        save_tutorial_draw(selection)
        with PROGRESS_LOCK:
            state = load_progress()
            state["tables"].update(value["data"])
            state["tokens"].update(value["dataTokens"])
            write_progress(state)
    elif action in ("event_box/show", "event_box/draw"):
        from danmemo.game import event_box as offline_event_box
        import sys

        value = offline_event_box.response(
            sys.modules[__name__],
            action.rsplit("/", 1)[1],
            decode_request(request_body),
            request_id,
        )
    elif action == "lottery/ticket/draw":
        request = decode_request(request_body)
        banner = offline_lotteries()["lotteries"].get(str(request.get("lotteryId")), {})
        if not banner.get("ticketId"):
            raise ValueError("This banner does not accept Gacha Tickets")
        value = lottery_draw_response(request)
    elif action == "lottery/draw":
        request = decode_request(request_body)
        if not schedule_module().available("banners", request.get("lotteryId")):
            raise ValueError("This banner is outside its scheduled availability")
        value = lottery_draw_response(request)
    elif action == "notice/show":
        value = lottery_notice(decode_request(request_body))
    elif action == "pvb/summary/get":
        value = arena_module().record_summary(
            sys_module(), decode_request(request_body)
        )
    elif action.startswith("element_event/"):
        from danmemo.game import element_event as offline_element_event

        value = offline_element_event.dispatch(
            sys_module(), action, decode_request(request_body)
        )
    elif action in (
        "pvb/friend/ranking/get",
        "pvb/total/ranking/get",
        "pvb/group/ranking/get",
    ):
        request = decode_request(request_body)
        value = arena_module().record_ranking(sys_module(), action, request)
    elif action.startswith("under_pvp/"):
        from danmemo.game import under_pvp as offline_under_pvp

        value = offline_under_pvp.dispatch(
            sys_module(), action, decode_request(request_body), request_id
        )
    elif action in ("pvp/summary/get", "serial_pvp/summary/get"):
        value = arena_module().summaries(
            sys_module(), action, decode_request(request_body)
        )
    elif action in ("pvp/battle/start", "pvp/battle/end", "pvp/battle/restart"):
        value = arena_module().war_battle(
            sys_module(), action, decode_request(request_body), request_id
        )
    elif action in ("serial_pvp/rival_list/update", "serial_pvp/grade/select"):
        value = arena_module().serial_menu(
            sys_module(), action, decode_request(request_body), request_id
        )
    elif action in (
        "serial_pvp/battle/start",
        "serial_pvp/battle/end",
        "serial_pvp/battle/restart",
        "serial_pvp/result/get",
    ):
        value = arena_module().serial_battle(
            sys_module(), action, decode_request(request_body), request_id
        )
    elif action in ("pve/score/ranking/get", "pve/recent/clear/get"):
        value = arena_module().monster_ranking(
            sys_module(), action, decode_request(request_body)
        )
    elif action == "dress_point/complete":
        from danmemo.game.dress_point import complete as complete

        value = complete(sys_module(), decode_request(request_body), request_id)
    elif action == "character/friendship/complete":
        from danmemo.game import transactions as offline_transactions

        value = offline_transactions.friendship(
            sys_module(), decode_request(request_body), request_id
        )
    elif action in ("shooting/ranking/get", "shooting/stage/complete"):
        from danmemo.game.multiplayer import SharedWorld as SharedWorld

        world = SharedWorld(PROGRESS_STATE.parent / "shooting.sqlite3", LOCAL_USER_ID)
        world.publish(load_progress())
        value = world.dispatch(
            action, decode_request(request_body), sys_module(), request_id
        )
    elif action in (
        "adventure/stage/complete",
        "pve/stage/complete",
        "pvb/stage/complete",
    ):
        request = decode_request(request_body)
        if action == "pve/stage/complete":
            info = stage_catalog()["stages"].get(str(request.get("adventureStageId")))
            if info is None or info[3] != 12:
                raise ValueError("Not a Monsterphilia stage")
            if "userPVEScore" not in request:
                raise ValueError("Missing Monsterphilia score")
        if action == "pvb/stage/complete" and "userPvBScore" not in request:
            raise ValueError("Missing Record Buster score")
        value = stage_complete_response(request, request_id)
    elif action == "achievement/complete":
        value = achievement_complete_response(decode_request(request_body))
    elif action in ("expedition/times", "expedition/complete"):
        from danmemo.game import transactions as offline_transactions

        handler = (
            offline_transactions.dispatch
            if action.endswith("/times")
            else offline_transactions.dispatch_complete
        )
        value = handler(sys_module(), decode_request(request_body))
    else:
        raise ValueError(f"Unsupported API action: {action}")
    if action in (
        "user_data/push",
        "achievement/complete",
        "adventure/stage/complete",
        "pve/stage/complete",
        "pvb/stage/complete",
        "element_event/stage/complete",
    ):
        from danmemo.game import reward_delivery as offline_reward_delivery

        if offline_reward_delivery.run(sys_module()):
            snapshot = load_progress()
            for table in ("UserGift", "UserAchievement"):
                value.setdefault("data", {})[table] = snapshot["tables"].get(table, [])
                value.setdefault("dataTokens", {})[table] = snapshot["tokens"].get(
                    table, ""
                )
    if action == "user/login":
        from danmemo.game.fwg_scout_schedule import login_fields as login_fields

        value.update(login_fields(sys_module()))
    if "data" in value:
        value["data"] = wire_tables(value["data"])
    return ("application/json", json.dumps(value, separators=(",", ":")).encode())
