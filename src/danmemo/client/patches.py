"""Version-guarded ARM64 compatibility patches."""

# familia_membership
"""Block nonmembers before transitions to Familia events start.

Original entries: Arena FWG/Master 0x1bcdd90, HomeQuest Rush 0x1a5a194,
HomeQuest GuildEvent/Royale/Grand Battle 0x1a5a438, Arena Union 0x1bce734.
Uses pure DomainGuild membership; reuses the Rush message and close callback.
Compatibility reconstruction; does not fabricate Familia membership or API data.
"""
familia_membership_CAVE = 28958208
familia_membership_ENTRIES = (
    (29154704, bytes.fromhex("ff4303d1"), bytes.fromhex("6040ff17")),
    (27632020, bytes.fromhex("ffc301d1"), bytes.fromhex("670f0514")),
    (27632696, bytes.fromhex("ffc301d1"), bytes.fromhex("c60e0514")),
    (29157172, bytes.fromhex("ffc301d1"), bytes.fromhex("0f3eff17")),
)
familia_membership_ENTRY = familia_membership_ENTRIES[0][0]
familia_membership_HOOK = bytes.fromhex(
    "fd7baaa9e00701a9e20f02a9e41703a9e61f04a9e82b00f9e00703ade20f04ade41705ade61f06ad37ed2594b6ed2594a0010036e00743ade20f44ade41745ade61f46ade82b40f9e00741a9e20f42a9e41743a9e61f44a9fd7bd6a830008052c0035fd6e00b40f921008052a58d199422681994e07b00f940bb009000140391e80304910df7df97c0c700f000003991e10304912ee7e697e10300aa082a019008c13891e90b40f9e82712a9e9830491e9a300f9e07b40f9e2830491aa691994e0830491080040f9081140f900013fd6e803443968000036e08b40f939f0c697e00743ade20f44ade41745ade61f46ade82b40f9e00741a9e20f42a9e41743a9e61f44a9fd7bd6a810008052c0035fd6fd7bbfa9bbffff9770000035fd7bc1a8c0035fd6fd7bc1a8ff4303d19abf0014fd7bbfa9b3ffff9770000035fd7bc1a8c0035fd6fd7bc1a8ffc301d193f0fa17fd7bbfa9abffff9770000035fd7bc1a8c0035fd6fd7bc1a8ffc301d134f1fa17fd7bbfa9a3ffff9770000035fd7bc1a8c0035fd6fd7bc1a8ffc301d1ebc10014"
)


def familia_membership_patch(data):
    if (
        all(
            (data[e : e + 4] == branch for e, old, branch in familia_membership_ENTRIES)
        )
        and data[
            familia_membership_CAVE : familia_membership_CAVE
            + len(familia_membership_HOOK)
        ]
        == familia_membership_HOOK
    ):
        return data
    if any((data[e : e + 4] != old for e, old, branch in familia_membership_ENTRIES)):
        raise ValueError("Unexpected original Familia event transition")
    if data[
        familia_membership_CAVE : familia_membership_CAVE + len(familia_membership_HOOK)
    ] != bytes(len(familia_membership_HOOK)):
        raise ValueError("Familia membership code cave is occupied")
    value = bytearray(data)
    for entry, old, branch in familia_membership_ENTRIES:
        value[entry : entry + 4] = branch
    value[
        familia_membership_CAVE : familia_membership_CAVE + len(familia_membership_HOOK)
    ] = familia_membership_HOOK
    return bytes(value)


# familia_refresh
"""Refresh membership before FWG gating; restore original refresh in other entries.

The previous early gate mistook an unloaded DomainGuild for nonmembership.
Original DomainGuild::refresh clears/reloads it from familia/mystatus; only
its successful callback may test membership and continue the FWG transition.
"""
familia_refresh_CAVE = 28955408
familia_refresh_CODE = bytes.fromhex(
    "f37bbca9e10300aa13000094e01b00f9f9ef2594e1430091e2031f2af8f02594e01b40f923f3c697f37bc4a8c0035fd6010440f908000014f37bbfa9f30301aa010440f904000094601200f9f37bc1a8c0035fd6f37bbfa9f30301aa000c805244f1c69708400091084c00a9a8020010080800f988020010080c00f9a8fdff10081000f9a8fdff10081400f9a8010010081800f988010010081c00f9a8010010082000f928010010082400f988f6ffb008d10d91082800f9f37bc1a8c0035fd6c0035fd6fbf2c617e0031faac0035fd6f55bbda9f35301a9fe1300f9280040b9140440f9554c40a95f7c00a988010035e00315aaa1030010d552e097e1031faa3055e097c0000035e00314aa7902009470000034e00314aa12000094b30100b46822009109fd5fc82a0500d10afd0bc8abffff35e90000b5680240f9e00313aa080940f900013fd6e00313aab52bc797fe1340f9f35341a9f55bc3a8c0035fd6ff4303d148c20014726573756c7400"
)
familia_refresh_BRANCH = bytes.fromhex("603dff17")
familia_refresh_INVOKE = 28955616
familia_refresh_ORIGINAL = 28955760
familia_refresh_VTABLE = 0
familia_refresh_POST_CAVE = 47068160
familia_refresh_POST_CODE = bytes.fromhex(
    "fd7bbfa9e00314aa7eeaba97fd7bc1a850000035f62ab61793dbb5972a2bb617fd7bbfa9e00316aa76eaba97fd7bc1a850000035052db617b3d7e0971b2db617fd7bbfa9e00316aa6eeaba97fd7bc1a850000035ecbabb17abd7e09720bbbb17"
)
familia_refresh_POST_SITES = (
    (27713728, b"i\xb0\xff\x97", b"\xd0\xd4I\x14"),
    (27715748, b"\x98\xaa*\x94", b"\xdf\xd2I\x14"),
    (29171928, b"\x8b\x1c%\x94", b"\xdaDD\x14"),
)
familia_refresh_LEGACY_CODE = bytes.fromhex(
    "f37bbca9f30300aa48080010e84f01a9e8430091e81b00f9f7ef2594e1430091e2031f2af6f02594f37bc4a8c0035fd6f37bbfa9f30300aa000280524df1c69788060010690640f9082400a9f37bc1a8c0035fd6e8050010090440f9282400a9c0035fd6c0035fd612f3c617e0031faac0035fd6f55bbda9f35301a9fe1300f9280040b9140440f9554c40a95f7c00a988010035e00315aa01060010ec52e097e1031faa4755e097c0000035e00314aa9002009470000034e00314aa12000094b30100b46822009109fd5fc82a0500d10afd0bc8abffff35e90000b5680240f9e00313aa080940f900013fd6e00313aacc2bc797fe1340f9f35341a9f55bc3a8c0035fd6ff4303d15fc200140000000074d3b9010000000078d3b9010000000040d3b9010000000064d3b9010000000074d3b9010000000078d3b9010000000084d3b901000000007cd3b9010000000074e3a60100000000726573756c7400"
)


def familia_refresh_patch(data):
    entry = familia_membership_ENTRIES[0][0]
    if (
        data[entry : entry + 4] == familia_refresh_BRANCH
        and data[
            familia_refresh_CAVE : familia_refresh_CAVE
            + len(familia_refresh_LEGACY_CODE)
        ]
        == familia_refresh_LEGACY_CODE
    ):
        if (
            not all(
                (
                    data[e : e + 4] == old
                    for e, old, branch in familia_membership_ENTRIES[1:]
                )
            )
            or data[
                familia_refresh_POST_CAVE : familia_refresh_POST_CAVE
                + len(familia_refresh_POST_CODE)
            ]
            != familia_refresh_POST_CODE
            or (
                not all(
                    (
                        data[e : e + 4] == branch
                        for e, old, branch in familia_refresh_POST_SITES
                    )
                )
            )
        ):
            raise ValueError("Unexpected legacy refresh patch")
        upgrade = bytearray(data)
        upgrade[
            familia_refresh_CAVE : familia_refresh_CAVE
            + len(familia_refresh_LEGACY_CODE)
        ] = bytes(len(familia_refresh_LEGACY_CODE))
        upgrade[
            familia_refresh_POST_CAVE : familia_refresh_POST_CAVE
            + len(familia_refresh_POST_CODE)
        ] = bytes(len(familia_refresh_POST_CODE))
        for e, old, branch in familia_membership_ENTRIES:
            upgrade[e : e + 4] = branch
        for e, old, branch in familia_refresh_POST_SITES:
            upgrade[e : e + 4] = old
        data = bytes(upgrade)
    if (
        data[entry : entry + 4] == familia_refresh_BRANCH
        and data[
            familia_refresh_CAVE : familia_refresh_CAVE + len(familia_refresh_CODE)
        ]
        == familia_refresh_CODE
    ):
        if (
            all(
                (
                    data[e : e + 4] == old
                    for e, old, branch in familia_membership_ENTRIES[1:]
                )
            )
            and data[
                familia_refresh_POST_CAVE : familia_refresh_POST_CAVE
                + len(familia_refresh_POST_CODE)
            ]
            == familia_refresh_POST_CODE
            and all(
                (
                    data[e : e + 4] == branch
                    for e, old, branch in familia_refresh_POST_SITES
                )
            )
        ):
            return data
    if any(
        (data[e : e + 4] != branch for e, old, branch in familia_membership_ENTRIES)
    ):
        raise ValueError("Unexpected previous Familia membership gates")
    if (
        data[
            familia_membership_CAVE : familia_membership_CAVE
            + len(familia_membership_HOOK)
        ]
        != familia_membership_HOOK
    ):
        raise ValueError("Unexpected existing member modal helper")
    if any(
        data[familia_refresh_CAVE : familia_refresh_CAVE + len(familia_refresh_CODE)]
    ):
        raise ValueError("Occupied membership refresh helper padding")
    if any(
        data[
            familia_refresh_POST_CAVE : familia_refresh_POST_CAVE
            + len(familia_refresh_POST_CODE)
        ]
    ) or any((data[e : e + 4] != old for e, old, branch in familia_refresh_POST_SITES)):
        raise ValueError("Unexpected post-refresh membership callbacks")
    result = bytearray(data)
    for e, old, branch in familia_membership_ENTRIES[1:]:
        result[e : e + 4] = old
    result[entry : entry + 4] = familia_refresh_BRANCH
    result[familia_refresh_CAVE : familia_refresh_CAVE + len(familia_refresh_CODE)] = (
        familia_refresh_CODE
    )
    result[
        familia_refresh_POST_CAVE : familia_refresh_POST_CAVE
        + len(familia_refresh_POST_CODE)
    ] = familia_refresh_POST_CODE
    for e, old, branch in familia_refresh_POST_SITES:
        result[e : e + 4] = branch
    return bytes(result)


# manifest_cache
"""Preserve validated cached catalogs when versions are unordered MD5 IDs.

Original comparator at loadLocalManifest can delete the entire asset directory
when a bundled hash sorts above a downloaded hash. Keep cached manifest for
hash identities; original remote manifest checks and per-asset diffs still run.
Non-hash release versions retain the original strcmp comparator.
"""
manifest_cache_ENTRY = 44561328
manifest_cache_CAVE = 28956160
manifest_cache_ORIGINAL = bytes.fromhex("90568b97")
manifest_cache_BRANCH = bytes.fromhex("9478c497")
manifest_cache_HOOK = bytes.fromhex(
    "fd7bbea9f30b00f9f30300aae80300aae90301aa0a0080520b696a382c696a386dc10051bf250071890000546d850151bf150071880200548dc10051bf250071890000548d850151bf150071c80100544a0500115f81007103feff540b8140392c8140396b0000347f810071c10000546c0000349f810071610000540000805203000014e00313aadaddc697f30b40f9fd7bc2a8c0035fd6"
)


def manifest_cache_patch(data):
    result = bytearray(data)
    if (
        result[manifest_cache_ENTRY : manifest_cache_ENTRY + 4]
        != manifest_cache_ORIGINAL
    ):
        raise ValueError("Unexpected original cached-manifest comparator")
    if result[
        manifest_cache_CAVE : manifest_cache_CAVE + len(manifest_cache_HOOK)
    ] != bytes(len(manifest_cache_HOOK)):
        raise ValueError("Occupied cached-manifest helper padding")
    result[manifest_cache_ENTRY : manifest_cache_ENTRY + 4] = manifest_cache_BRANCH
    result[manifest_cache_CAVE : manifest_cache_CAVE + len(manifest_cache_HOOK)] = (
        manifest_cache_HOOK
    )
    return bytes(result)


# manifest_storage_root
"""Use each validated manifest's phase storage directory for file lookup.

Original parse derives root from the source manifest parent (bundled manifest/
for APK fallback, or contents/1/manifests/ for phased bootstrap). Interrupted
phases have no completed cached manifest to supply the phase directory. Restore
AssetsManager.storagePath before the original prependSearchPaths method so its
existing file hash checks can find completed files; do not bypass any checksum.
"""
manifest_storage_root_ENTRY = 44561472
manifest_storage_root_CAVE = 28956672
manifest_storage_root_ORIGINAL = bytes.fromhex("3a310094")
manifest_storage_root_BRANCH = bytes.fromhex("f078c497")
manifest_storage_root_HOOK = bytes.fromhex(
    "f47bbfa9f40300aa00400091610204916379e097e00314aa44b83b94f47bc1a8c0035fd6"
)


def manifest_storage_root_patch(data):
    result = bytearray(data)
    if (
        result[manifest_storage_root_ENTRY : manifest_storage_root_ENTRY + 4]
        != manifest_storage_root_ORIGINAL
    ):
        raise ValueError("Unexpected original manifest search path call")
    if result[
        manifest_storage_root_CAVE : manifest_storage_root_CAVE
        + len(manifest_storage_root_HOOK)
    ] != bytes(len(manifest_storage_root_HOOK)):
        raise ValueError("Occupied manifest storage root helper padding")
    result[manifest_storage_root_ENTRY : manifest_storage_root_ENTRY + 4] = (
        manifest_storage_root_BRANCH
    )
    result[
        manifest_storage_root_CAVE : manifest_storage_root_CAVE
        + len(manifest_storage_root_HOOK)
    ] = manifest_storage_root_HOOK
    return bytes(result)


# outfit_shop
"""Remove the superseded outfit availability experiment.

Original hot shop transport reads singular operation at 0x209bd48/0x2421690.
The missing envelope is fixed on the server. Keep the original native list and
availability behavior rather than masking a delayed callback with a UI filter.
"""
outfit_shop_ENTRY = 37878264
outfit_shop_CAVE = 28957328
outfit_shop_OLD = bytes.fromhex("ff4301d1")
outfit_shop_LEGACY_BRANCH = bytes.fromhex("26f8dd17")
outfit_shop_LEGACY_CODE = bytes.fromhex(
    "f353bea9fd7b01a9fd030091f30300aa5c0722941f100071c1010054e00313aa2d0622941f0000714d010054f403002ae00313aa300622941f00146bab00005440008052fd7b41a9f353c2a8c0035fd6e00313aafd7b41a9f353c2a8ff4301d1c3072214"
)


def outfit_shop_patch(data):
    if data[outfit_shop_ENTRY : outfit_shop_ENTRY + 4] == outfit_shop_OLD:
        return data
    if (
        data[outfit_shop_ENTRY : outfit_shop_ENTRY + 4] != outfit_shop_LEGACY_BRANCH
        or data[outfit_shop_CAVE : outfit_shop_CAVE + len(outfit_shop_LEGACY_CODE)]
        != outfit_shop_LEGACY_CODE
    ):
        raise ValueError("Unknown native outfit availability experiment")
    result = bytearray(data)
    result[outfit_shop_ENTRY : outfit_shop_ENTRY + 4] = outfit_shop_OLD
    result[outfit_shop_CAVE : outfit_shop_CAVE + len(outfit_shop_LEGACY_CODE)] = bytes(
        len(outfit_shop_LEGACY_CODE)
    )
    return bytes(result)


# phase_bootstrap
"""Seed missing phase manifests from recovered APK bootstrap resources.

Only original absent /contents/1/manifests/phase.manifest.[2-8] filenames
can fall back to manifest/phase.manifest.N. Existing/corrupt files retain the
original parser and validation; no loaded flag or download validation bypass.
"""
phase_bootstrap_ENTRY = 44604820
phase_bootstrap_CAVE = 28955136
phase_bootstrap_ORIGINAL = bytes.fromhex("ffc301d1")
phase_bootstrap_BRANCH = bytes.fromhex("1b4dc417")
phase_bootstrap_HOOK = bytes.fromhex(
    "fd7bb3a9f35301a9f55b02a9f76303a9f30300aaf40301aa28004039290440f90a7d01531f0100724901899a3f9900f1a30500542a0840f92b0400911f0100726a018a9a4a01098b56f15f38c8ca00511f19007188040054409900d121160010a20480d2083c01f0088142f900013fd6a0030035600640f9080040f9087940f9e10314aa00013fd6e0020035f8030291e00318aae1150010020380d2283c01f0089142f900013fd6166300391f670039280480d2e82b00f9280380d2e82f00f9f83300f9f5430191600640f9080040f9087940f9e10315aa00013fd640000034f40315aae00313aae10314aa85000094f76343a9f55b42a9f35341a9fd7bcda8c0035fd600000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000ffc301d125b23b140000000000000000000000000000000000000000000000002f636f6e74656e74732f312f6d616e6966657374732f70686173652e6d616e69666573742e00000000000000000000006d616e69666573742f70686173652e6d616e69666573742e000000000000000000000000000000000000000000000000"
)


def phase_bootstrap_patch(data):
    result = bytearray(data)
    if (
        result[phase_bootstrap_ENTRY : phase_bootstrap_ENTRY + 4]
        != phase_bootstrap_ORIGINAL
    ):
        raise ValueError("Unexpected original manifest parser")
    if result[
        phase_bootstrap_CAVE : phase_bootstrap_CAVE + len(phase_bootstrap_HOOK)
    ] != bytes(len(phase_bootstrap_HOOK)):
        raise ValueError("Occupied phase bootstrap helper padding")
    result[phase_bootstrap_ENTRY : phase_bootstrap_ENTRY + 4] = phase_bootstrap_BRANCH
    result[phase_bootstrap_CAVE : phase_bootstrap_CAVE + len(phase_bootstrap_HOOK)] = (
        phase_bootstrap_HOOK
    )
    return bytes(result)


def phase_bootstrap_bootstrap_resources(root):
    """Return recovered empty phase catalogs for the primary APK only."""
    import json
    from pathlib import Path

    for phase in range(2, 9):
        source = Path(root) / "contents" / "1" / "manifests" / f"phase.manifest.{phase}"
        payload = source.read_bytes()
        manifest = json.loads(payload)
        if (
            manifest.get("assets") != {}
            or not manifest.get("version", "").endswith(" phased")
            or f"project.manifest.{phase}/" not in manifest.get("remoteManifestUrl", "")
        ):
            raise ValueError(f"Unexpected recovered phase bootstrap: {source}")
        yield (f"assets/us/manifest/phase.manifest.{phase}", payload)


# player_exp
"""Apply a receipt-guarded EXP transition before original quest result UI.

Compatibility for the snapshot adapter which omits completion operation 5000.
Only calls original changeTotalExp; never replays its other completion writes.
"""
player_exp_ENTRY = 38240328
player_exp_CAVE = 28956736
player_exp_OLD = bytes.fromhex("2e1dc317")
player_exp_BRANCH = bytes.fromhex("fe95dc17")
player_exp_OMITTED_RESULT_CHAIN_CODE = bytes.fromhex(
    "fd7ba8a9e00701a9e20f02a9e41703a9e61f04a9e82705a9ea2f06a9ec3707a9ee3f08a9f04709a9e00705ade20f06ade41707ade61f08adf35312a9f55b13a9e00315aac1060010b551e09708004039080d00121f05007141030054f30300aa61060010ae51e097e1031faa291be197f40300aae00313aac1050070a851e097e1031faa231be197f50300aa9f0200f18b010054bf0214eb4d010054d8872694f60300aa000040f9378ae2971f0014eb81000054e00316aaa10214cb968c2694e89b40f9e8a300f9e0030591e1031f2afc87e697f35352a9f55b53a9e00745ade20f46ade41747ade61f48ade00741a9e20f42a9e41743a9e61f44a9e82745a9ea2f46a9ec3747a9ee3f48a9f04749a9fd7bd8a801c900b0bd692314706c61796572457870526573756c74006265666f726500616674657200"
)
import struct

player_exp__code = bytearray(player_exp_OMITTED_RESULT_CHAIN_CODE)
player_exp__strings = player_exp__code.index(b"playerExpResult\x00")
player_exp__tail = player_exp__strings - 8
struct.pack_into(
    "<II",
    player_exp__code,
    player_exp__tail,
    3573751839,
    335544320 | (22279424 - (player_exp_CAVE + player_exp__tail + 4)) // 4 & 67108863,
)
player_exp__task_call = player_exp__code.index(bytes.fromhex("fc87e697"))
struct.pack_into("<I", player_exp__code, player_exp__task_call, 3573751839)
player_exp_CODE = bytes(player_exp__code)
player_exp_LEGACY_CODE = bytes.fromhex(
    "fd7ba8a9e00701a9e20f02a9e41703a9e61f04a9e82705a9ea2f06a9ec3707a9ee3f08a9f04709a9e00705ade20f06ade41707ade61f08adf35312a9f55b13a9e00315aa01060010b551e09708004039080d00121f05007141030054f30300aaa1050010ae51e097e1031faa291be197f40300aae00313aa01050070a851e097e1031faa231be197f50300aa9f0200f18b010054bf0214eb4d010054d8872694f60300aa000040f9378ae2971f0014eb81000054e00316aaa10214cb968c2694f35352a9f55b53a9e00745ade20f46ade41747ade61f48ade00741a9e20f42a9e41743a9e61f44a9e82745a9ea2f46a9ec3747a9ee3f48a9f04749a9fd7bd8a8f087e617706c61796572457870526573756c74006265666f726500616674657200"
)


def player_exp_patch(data):
    if (
        data[player_exp_ENTRY : player_exp_ENTRY + 4] == player_exp_BRANCH
        and data[
            player_exp_CAVE : player_exp_CAVE
            + len(player_exp_OMITTED_RESULT_CHAIN_CODE)
        ]
        == player_exp_OMITTED_RESULT_CHAIN_CODE
    ):
        upgrade = bytearray(data)
        upgrade[
            player_exp_CAVE : player_exp_CAVE
            + len(player_exp_OMITTED_RESULT_CHAIN_CODE)
        ] = player_exp_CODE
        return bytes(upgrade)
    if (
        data[player_exp_ENTRY : player_exp_ENTRY + 4] == player_exp_BRANCH
        and data[player_exp_CAVE : player_exp_CAVE + len(player_exp_LEGACY_CODE)]
        == player_exp_LEGACY_CODE
    ):
        upgrade = bytearray(data)
        upgrade[player_exp_CAVE : player_exp_CAVE + len(player_exp_LEGACY_CODE)] = (
            bytes(len(player_exp_LEGACY_CODE))
        )
        upgrade[player_exp_ENTRY : player_exp_ENTRY + 4] = player_exp_OLD
        data = bytes(upgrade)
    if (
        data[player_exp_ENTRY : player_exp_ENTRY + 4] == player_exp_BRANCH
        and data[player_exp_CAVE : player_exp_CAVE + len(player_exp_CODE)]
        == player_exp_CODE
    ):
        return data
    if data[player_exp_ENTRY : player_exp_ENTRY + 4] != player_exp_OLD:
        raise ValueError("Unexpected quest result task trampoline")
    if any(data[player_exp_CAVE : player_exp_CAVE + len(player_exp_CODE)]):
        raise ValueError("Occupied player EXP compatibility padding")
    result = bytearray(data)
    result[player_exp_ENTRY : player_exp_ENTRY + 4] = player_exp_BRANCH
    result[player_exp_CAVE : player_exp_CAVE + len(player_exp_CODE)] = player_exp_CODE
    return bytes(result)


# request_timestamp
"""Refresh an aged queued request before original header/RSA construction.

Original request timestamp is stored at construction, before asset downloads.
Recent timestamps remain stable; gateway freshness and replay checks stay intact.
"""
request_timestamp_ENTRY = 34028572
request_timestamp_CAVE = 22279840
request_timestamp_CODE = bytes.fromhex(
    "fd7bbfa998e6539421008052e5e65394b3a240f9080013cb1fc103f16c0000541fc103b16a000054a0a200f9f30300aafd7bc1a853d12c14"
)
request_timestamp_BRANCH = bytes.fromhex("a12ed317")


def request_timestamp_patch(data):
    data = bytearray(data)
    if data[request_timestamp_ENTRY : request_timestamp_ENTRY + 4] != bytes.fromhex(
        "b3a240f9"
    ):
        raise ValueError("Unexpected original request timestamp load")
    if data[
        request_timestamp_CAVE : request_timestamp_CAVE + len(request_timestamp_CODE)
    ] != bytes(len(request_timestamp_CODE)):
        raise ValueError("Occupied request timestamp helper cave")
    data[request_timestamp_ENTRY : request_timestamp_ENTRY + 4] = (
        request_timestamp_BRANCH
    )
    data[
        request_timestamp_CAVE : request_timestamp_CAVE + len(request_timestamp_CODE)
    ] = request_timestamp_CODE
    return bytes(data)


# royale_result
"""Import the original GvE result model without replaying clear/reward writes.

Original 0x246f6bc/0x2470234, singleton +0x70; isRecorded lives at +0x69.
The snapshot adapter bypasses operation 23100. Preserve the existing PvB helper
and import GvE only when nested score contains the original isRecorded key.
Key addresses use signed table-relative offsets to survive Android ASLR.
"""
import hashlib

royale_result_ENTRY = 22280960
royale_result_CAVE = 28956312
royale_result_TABLE = 28957852
royale_result_OLD = bytes.fromhex("f353baa9")
royale_result_BRANCH = bytes.fromhex("e6761914")
royale_result_CODE = bytes.fromhex(
    "f37bbea9f30300aa05000094e00313aa05000094f37bc2a8c0035fd6f353baa91389e617f353baa9f55b01a9fd7b02a9f30300aaa1b2009021800e91e0c30091d2f8df97e00313aae1c300917fb9f197c00600b4e00313aaa1b2009021800e911952e097f30300aa21cd009021340391e0c30091c5f8df97e00313aae1c3009172b9f197200500b474b90d94143840f9d40400b415000090b5723291a10240b941040034080000900871329101c1218be00313aa0452e097b61a4039e1031faadf06007100010054df160071600000547a1be197050000140e35ec970000261e020000141b1ce197a80a40798802088bdf22007100010054df06007100010054e97b40b21f0009eb00b0899a000100b904000014000100f90200001400010039b5220091deffff17fd7b42a9f55b41a9f353c6a8c0035fd6"
)
royale_result_FIELDS = bytes.fromhex(
    "75339a012800040020359a0140000800038e97014800080034359a0150000500dc4a7d015800080031349a016900010083e190012c00040049359a01600008005d359a018c00050049359a01880004000000000000000000"
)


def royale_result_patch(data):
    if (
        data[royale_result_ENTRY : royale_result_ENTRY + 4] == royale_result_BRANCH
        and data[royale_result_CAVE : royale_result_CAVE + len(royale_result_CODE)]
        == royale_result_CODE
        and (
            data[royale_result_TABLE : royale_result_TABLE + len(royale_result_FIELDS)]
            == royale_result_FIELDS
        )
    ):
        return data
    if (
        data[royale_result_ENTRY : royale_result_ENTRY + 4] == royale_result_BRANCH
        and hashlib.sha256(
            data[royale_result_CAVE : royale_result_CAVE + 300]
            + data[
                royale_result_TABLE : royale_result_TABLE + len(royale_result_FIELDS)
            ]
        ).hexdigest()
        == "19ed4fe0fc50ac815b55aa992e0a645e31873845fcd59e63efc80ebcffdbcba8"
        and (
            not any(
                data[
                    royale_result_CAVE + 300 : royale_result_CAVE
                    + len(royale_result_CODE)
                ]
            )
        )
    ):
        result = bytearray(data)
        result[royale_result_ENTRY : royale_result_ENTRY + 4] = royale_result_OLD
        result[royale_result_CAVE : royale_result_CAVE + len(royale_result_CODE)] = (
            bytes(len(royale_result_CODE))
        )
        result[
            royale_result_TABLE : royale_result_TABLE + len(royale_result_FIELDS)
        ] = bytes(len(royale_result_FIELDS))
        data = bytes(result)
    if (
        data[royale_result_ENTRY : royale_result_ENTRY + 4] != royale_result_OLD
        or any(data[royale_result_CAVE : royale_result_CAVE + len(royale_result_CODE)])
        or any(
            data[royale_result_TABLE : royale_result_TABLE + len(royale_result_FIELDS)]
        )
    ):
        raise ValueError("Unexpected GvE/PvB result helper or occupied padding")
    result = bytearray(data)
    result[royale_result_ENTRY : royale_result_ENTRY + 4] = royale_result_BRANCH
    result[royale_result_CAVE : royale_result_CAVE + len(royale_result_CODE)] = (
        royale_result_CODE
    )
    result[royale_result_TABLE : royale_result_TABLE + len(royale_result_FIELDS)] = (
        royale_result_FIELDS
    )
    return bytes(result)


# rush_result
"""Restore the original Guild Raid result importer before completion UI.

ARM64 callback 0x2477e7c holds the root JSON in x21 and its shared_ptr
control block at [sp]. Original importer 0x2470534 consumes those root keys;
stageInfo instead belongs to Union Raid (0x2392474).
"""
rush_result_HOOK = 38239868
rush_result_WRAPPER = 22279104
rush_result_CODE = bytes.fromhex(
    "fd7bbca95b02003581e600b021641e91e04300919371f997e1430091e00315aa40320b94c00000b4e92340f9f5a702a9e1a30091e0031faa4fc43c94e843403968000036e01340f9ee6ae097fd7bc4a8010001d09be23c14"
)


def rush_result_patch(data):
    if data[rush_result_HOOK : rush_result_HOOK + 4] != bytes.fromhex("418600d0"):
        raise ValueError("Unexpected original Rush completion callback")
    if data[rush_result_WRAPPER : rush_result_WRAPPER + len(rush_result_CODE)] != bytes(
        len(rush_result_CODE)
    ):
        raise ValueError("Rush result wrapper padding is occupied")
    result = bytearray(data)
    result[rush_result_HOOK : rush_result_HOOK + 4] = bytes.fromhex("511dc317")
    result[rush_result_WRAPPER : rush_result_WRAPPER + len(rush_result_CODE)] = (
        rush_result_CODE
    )
    return bytes(result)


# union_result
"""Restore the original Union Raid result model before its result screen.

The snapshot adapter bypasses the Union completion importer. Import presentation state via
original 0x2471aac without replaying rewards, medal writes or mission events.
Chains the existing Royale/PvB helper. Keys remain relative to the load base.
"""
union_result_ENTRY = 22280960
union_result_CAVE = 47398944
union_result_OLD = bytes.fromhex("e6761914")
union_result_BRANCH = bytes.fromhex("48d15f14")
union_result_CODE = bytes.fromhex(
    "f37bbfa9f30300aa9ca5b997e00313aa03000094f37bc1a8c0035fd6ffc303d1f35309a9f55b0aa9f7630ba9f96b0ca9fb730da9fd7b0ea9f30300aa413c00b021f43691e0c301916e9e9997e00313aae1c301911b5fab97a00e00b4e00313aa413c00d021bc0791b5f79997f40300aa413c00b021881e91e0c30191619e9997e00314aae1c301910e5fab97000d00b4e00314aa413c00b021881e91a8f79997e1031faa03fa9997f503002ae06b00b9e00314aae126009021681d91a0f79997e1031faafbf99997f603002ae00314aae126009021901d9199f79997e10300aae0c30091c9e19997e00313aae126009021ac1d9192f79997e10300aae0030191c2e19997e00313aae12300b021dc16918bf79997e1031faae6f99997f70300aae00313aae1260090213c1c9184f79997e1031faaa5c19a97f80300aae00313aa614000b0216808917df79997e1031faa9ec19a97f90300aae00313aae12300b0213c189176f79997e1031faaf1c09a97fa0300aae00313aa413c00b021a429916ff79997e1031faaeac09a97fb0300aae00313aa614000b02134039168f79997e1031faa89c19a97fc0300aa3018f297e10300aa224440b8c43dffd084d01f91e0a3019183008052bea78097800200b4080040b90000088b01028052e2031faa7ea19997e00b00f9ba5ec797005040f9600100b4e10315aae203162ae3c30091e403172ae5030191e603182ae703192afa6f00a9fc6300391df6dc97e0030191cce09997e0c30091cae09997fd7b4ea9fb734da9f96b4ca9f7634ba9f55b4aa9f35349a9ffc30391c0035fd6"
)


def union_result_patch(data):
    if (
        data[union_result_ENTRY : union_result_ENTRY + 4] == union_result_BRANCH
        and data[union_result_CAVE : union_result_CAVE + len(union_result_CODE)]
        == union_result_CODE
    ):
        return data
    if data[union_result_ENTRY : union_result_ENTRY + 4] != union_result_OLD or any(
        data[union_result_CAVE : union_result_CAVE + len(union_result_CODE)]
    ):
        raise ValueError("Unexpected Union/Royale result helper or occupied padding")
    result = bytearray(data)
    result[union_result_ENTRY : union_result_ENTRY + 4] = union_result_BRANCH
    result[union_result_CAVE : union_result_CAVE + len(union_result_CODE)] = (
        union_result_CODE
    )
    return bytes(result)


# Native popularity row geometry.
"""ARM64 popularity row spacing, paired with the JSON overlay.

Original delegate 0x1829428 returns 252 for row zero and 210 otherwise.
Growing the lower cards requires changing its fixed 210px allocation too.
"""
import struct

popularity_LOWER_ROW_HEIGHT = 242
popularity_OFFSET = 53050468
popularity_DELEGATE = 25334824


def popularity_patch(data):
    expected = bytes.fromhex("3f00007169d300d0e8179f1a29913191205968bcc0035fd6")
    if data[popularity_DELEGATE : popularity_DELEGATE + len(expected)] != expected:
        raise ValueError("Unexpected popularity table height delegate")
    old = struct.unpack_from("<f", data, popularity_OFFSET)[0]
    if old not in (210.0, float(popularity_LOWER_ROW_HEIGHT)):
        raise ValueError("Unexpected popularity lower-row height")
    result = bytearray(data)
    struct.pack_into(
        "<f", result, popularity_OFFSET, float(popularity_LOWER_ROW_HEIGHT)
    )
    return bytes(result)
