# DanMemoRe Online

Community online server reimplementation for **DanMachi: Memoria Freese 15.1.0 (EU, ARM64)**. Python 3.12+, SQLite and OpenSSL. The native game API, per-account workers, shared game modes, asset delivery and in-game notices are included.

This is reconstructed server software, not the publisher's server source. Some scoring, matchmaking and scheduling policies are reconstructed. Backend checks do not guarantee every Android screen or game mode works exactly like the retired service.

## Layout

```text
config/                 Server configuration and deployment examples
master/metadata/        Immutable runtime catalogs
migrations/             Identity database schema
src/danmemo/
  api/                  Native HTTP gateway
  client/               Optional compatible APK builder and ARM64 patches
  config/               File-based event and season schedules
  content/              Asset delivery and in-game Gacha Details
  game/                 Gameplay, inventory, battles, rewards and missions
  master/               Original master decoding and indexes
  protocol/             Native signing and userdata encoding
  services/             Account workers, reward delivery and bounded logs
  storage/              SQLite identity and atomic progression persistence
static/                 External game archive and client inputs (not tracked)
var/                    Generated accounts, databases, caches and logs (not tracked)
output/                 Built APKs (not tracked)
```


## Required inputs

The repository contains server source and runtime metadata. Runtime catalogs and preserved in-game notice templates are included. Supply your own compatible archive before starting:

```text
static/bundles/
  1/project.manifest
  1/version.manifest
  1/master/divide_enc/*.enc
  1/files/...
  2/... through 8/...    Each phase's manifests and referenced files
static/client/
  config.arm64_v8a.apk   Original EU 15.1.0 ARM64 split, containing libapp.so
```

Preserve the original archive paths. The manifests, encrypted masters and files must belong to the same client revision. An incomplete asset archive can still cause download or scene errors even when `/health` responds successfully. The original ARM64 library supplies the master keys and native signing parameters at runtime; they are not exported from a running account.

You may set absolute archive paths in `config/server.toml`, so no duplicate asset copy is required. Optional recovered notice artwork can be placed in `<assets>/web/` using the hashed filenames in `master/metadata/recovered_notice_assets.json`. Missing notice art falls back only where a matching master character image exists.

## Install

On Ubuntu/Debian:

```sh
sudo apt-get update
sudo apt-get install -y python3 python3-venv openssl
# Python must be 3.12 or newer.
git clone https://github.com/DangNguyen38/DanMemoRe-Online.git
cd DanMemoRe-Online
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
danmemo init
```

On Windows, use `py -3.12 -m venv .venv` and `.venv\Scripts\Activate.ps1`; install OpenSSL and put it on `PATH`. The remaining commands are the same.

Edit `config/server.toml`:

```toml
[server]
bind = "127.0.0.1"
port = 28768
origin = "http://127.0.0.1:28768"
content_bind = "127.0.0.1"
content_port = 28766
workers = 8
trusted_proxy = false

[paths]
assets = "static/bundles"
client_apk = "static/client/config.arm64_v8a.apk"
state = "var"
output = "output"
schedule = "config/schedule.json"
```

Paths are relative to the project containing the `config/` directory. `origin` must exactly match the public origin embedded in your compatible APK, including scheme and port. Do not reuse another operator's hostname or APK endpoint for a new server.

```sh
danmemo check
danmemo prepare
danmemo serve
```

`check` validates archive layout, master decoding and signing inputs without editing accounts. `prepare` computes checksum catalogs and voice ZIP caches under `var/`; its first run can take time on a large archive. `serve` starts the content service and the native API gateway, prewarms the catalog, and accepts players after warmup. It does not start or manipulate an emulator.

A new native device creates its own UID starting at `2000000001`. Reopening the same device preserves the account. Use the game's Import Code flow to transfer an account; there is no browser registration or password-reset page.

## Enable content

Edit `config/schedule.json`; restart the server after schedule changes. New servers intentionally start with no optional events, Iris banners or competitive seasons enabled. Permanent content and the original onboarding/ticket behavior are retained.

Example (UNIX timestamps, with the end exclusive for competitive battle windows):

```json
{
  "banners": {
    "171002059": {"start": 1792962000, "end": 1793566800}
  },
  "events": {},
  "modeSchedules": {
    "online": {
      "modes": {
        "war_game": {
          "seasonId": 10000000179,
          "start": 1792962000,
          "end": 1793566800
        }
      }
    },
    "offline": {"modes": {}}
  }
}
```

Choose IDs from the matching original master. Event keys are `adventureEpisode` IDs. Mode keys include `war_game`, `record_buster`, `ultimate_war_game`, `telskyura`, `familia_royale`, `familia_rush`, `familia_war_game`, `master_familia_war_game` and `union_raid`. Some mode selectors use a parent adventure ID; consult `src/danmemo/config/modes.py` before selecting an archived season. Scheduling an unsupported season does not implement its missing gameplay. Do not open Under and Abyss concurrently unless the original client's release gate supports that combination.

The `offline` schedule controls the underlying content catalog only; it is not a second playable server. Keep it empty for an online-only deployment. Native achievement windows and online mode overlays are generated from `online`.

## Connect a client

A stock client still points at the retired endpoints. Use a compatible client built for your own `origin`. To build one locally, install a JDK and Android SDK build-tools/platform-tools, set `JAVA_HOME` and `ANDROID_SDK_ROOT`, then provide the original complete APK:

```sh
danmemo client --source /path/to/original-eu-15.1.0.apk --endpoint http://YOUR_HOST:28768
```

The builder creates a new local signing identity under `var/` and writes the compatible APK under `output/online-client/`. No signing key is included in Git. Keep that identity private and retain it to sign future updates. Install updates with the same package/signature using `adb install -r`; do not uninstall if you need to retain local game data. Endpoint length is limited by the original native fields; the builder rejects an endpoint that does not fit.

This project does not publish a prebuilt client, modify your original game installation, or copy a private server's player data.

## Run on a VPS

Run one server process against one `state` directory. Start with the default eight account workers and adjust to available RAM; workers preserve account isolation, and the SQLite world is shared. More workers alone do not guarantee more throughput.

For direct testing, bind `0.0.0.0` and expose only the gateway port. Keep the internal content port on loopback. For an HTTPS reverse proxy, leave both Python listeners on loopback, set `origin` to the public HTTPS origin, and set `trusted_proxy = true` only when the reverse proxy is trusted and local. An example is in `config/nginx.conf`; replace the hostname before using it. Native signing requires the same public origin throughout.

An example service is in `config/danmemo.service`. Adjust its user, project path and virtualenv path, then:

```sh
sudo cp config/danmemo.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now danmemo
sudo journalctl -u danmemo -f
```

Keep `var/` on persistent local storage. It holds identities, per-account progression, shared results and reward receipts. Do not delete those files as log cleanup, share a live SQLite state directory between independent deployments, or expose `var/`, `config/` or signing keys through your HTTP server. Log files are separate from account records. Stop the service before moving its persistent state.
