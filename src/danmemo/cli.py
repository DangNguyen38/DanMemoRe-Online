"""Online server lifecycle, archive preparation and optional client build."""

import argparse
import os
from pathlib import Path
import shutil
import signal
import threading
import tomllib

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = (
    ROOT / "config"
    if (ROOT / "config/server.example.toml").is_file()
    else Path(__file__).resolve().parent / "_data/config"
)


def settings(filename):
    config = Path(filename).resolve()
    if not config.is_file():
        raise ValueError(f"Missing {config}; run danmemo init")
    value = tomllib.loads(config.read_text("utf-8"))
    server = value.get("server", {})
    base = config.parent.parent
    os.environ["DANMEMO_ROOT"] = str(base)
    for key, env in [
        ("assets", "DANMEMO_ASSETS"),
        ("client_apk", "DANMEMO_CLIENT_APK"),
        ("state", "DANMEMO_STATE"),
        ("output", "DANMEMO_OUTPUT"),
        ("schedule", "DANMEMO_SCHEDULE"),
    ]:
        p = Path(value.get("paths", {})[key]).expanduser()
        os.environ[env] = str(
            (base / p).resolve() if not p.is_absolute() else p.resolve()
        )
    os.environ["DANMEMO_GAME_WORKERS"] = str(server.get("workers", 8))
    os.environ["DANMEMO_LOG_ROOT"] = str(Path(os.environ["DANMEMO_STATE"]) / "logs")
    os.environ["DANMEMORE_CONTENT_ORIGIN"] = server.get(
        "origin", "http://127.0.0.1:28768"
    ).rstrip("/")
    return server


def check_archive():
    from danmemo import paths

    missing = [
        str(paths.ASSETS / str(p) / "project.manifest")
        for p in range(1, 9)
        if not (paths.ASSETS / str(p) / "project.manifest").is_file()
    ]
    if not paths.CLIENT_APK.is_file():
        missing.append(str(paths.CLIENT_APK))
    if missing:
        raise ValueError("Missing archive inputs:\n" + "\n".join(missing))
    if not shutil.which("openssl"):
        raise ValueError("OpenSSL must be installed and available on PATH")
    from danmemo.master.reader import keys, rows, master_bytes
    from danmemo.protocol.signing import load_app_credentials, load_game_salt

    keys()
    load_app_credentials()
    load_game_salt()
    count = len(rows(master_bytes("pc")))
    print(
        f"Archive/key layout verified; {count} original PC rows. No account data changed."
    )


def prepare_archive():
    check_archive()
    from danmemo.content.checksums import main as cache
    from danmemo.game import engine

    cache()
    engine.archive_checksums.cache_clear()
    engine.local_voice_manifest()
    print("Preparing native manifest overlays...", flush=True)
    engine.asset_paths()
    engine.local_version_manifest("1")
    print("Prepared immutable checksums, voice and native catalogs.")


def serve(server):
    from functools import partial
    from danmemo import paths
    from danmemo.game import engine
    from danmemo.api.gateway import Gateway, Handler, GameHTTPServer
    from danmemo.content.server import ContentServer, ContentHandler
    from danmemo.content import catalogs

    if not paths.SCHEDULE.is_file():
        raise ValueError("Missing schedule file; run danmemo init")
    check_archive()
    paths.STATE.mkdir(parents=True, exist_ok=True)
    engine.ENDPOINT = server["origin"].rstrip("/")
    print("Warming native content catalogs before accepting players...", flush=True)
    engine.local_voice_manifest()
    engine.asset_paths()
    engine.local_version_manifest("1")
    content = ContentServer(
        (server.get("content_bind", "127.0.0.1"), server.get("content_port", 28766)),
        ContentHandler,
    )
    threading.Thread(target=content.serve_forever, name="content", daemon=True).start()
    gateway = None
    http = None
    try:
        content_origin = f"http://127.0.0.1:{content.server_address[1]}"
        gateway = Gateway(
            paths.STATE / "online",
            engine.ENDPOINT,
            content_origin,
            trusted_proxy=server.get("trusted_proxy", False),
        )
        http = GameHTTPServer(
            (server.get("bind", "127.0.0.1"), server.get("port", 28768)),
            partial(Handler, gateway=gateway),
        )
        # Do not make the first player pay for archive hashing/catalog warmup.
        # Content and gateway share this process; read prepared bytes directly.
        catalogs.CONTENT_ORIGIN = None
        catalogs.catalogs()
        print(
            f"Online gateway ready: {engine.ENDPOINT}; API={http.server_address}; content={content.server_address}",
            flush=True,
        )
        print("Admin and account website routes are absent.", flush=True)
        if hasattr(signal, "SIGTERM"):
            signal.signal(
                signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())
            )
        http.serve_forever()
    finally:
        if http is not None:
            http.server_close()
        if gateway is not None:
            gateway.close()
        content.shutdown()
        content.server_close()


def main():
    parser = argparse.ArgumentParser(description="DanMemoRe online server")
    parser.add_argument("--config", default="config/server.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("check")
    sub.add_parser("prepare")
    sub.add_parser("serve")
    client = sub.add_parser("client")
    client.add_argument("--source", type=Path, required=True)
    client.add_argument("--endpoint", required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            target = Path(args.config).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(TEMPLATES / "server.example.toml", target)
            schedule = target.parent / "schedule.json"
            if not schedule.exists():
                shutil.copyfile(TEMPLATES / "schedule.example.json", schedule)
            print(
                f"Created configuration at {target}; edit paths/origin before running."
            )
            return
        server = settings(args.config)
        if args.command == "check":
            check_archive()
        elif args.command == "prepare":
            prepare_archive()
        elif args.command == "serve":
            serve(server)
        elif args.command == "client":
            from danmemo.client import online

            online.configure(endpoint=args.endpoint, app_label="DanMemoRe Online")
            online.client.SOURCE_APK = args.source.resolve()
            online.client.ARM64_XAPK = Path("/nonexistent/optional-client-splits.xapk")
            online.client.build()
    except (ValueError, FileNotFoundError) as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        print("Server stopped.")
