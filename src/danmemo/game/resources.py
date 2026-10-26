"""Original master resource identities shared by rewards and administration."""

import functools
import re
from danmemo.master import reader as master
from danmemo.content.assets import original_url as original_url

NAMES = (
    "currency",
    "expItem",
    "statusBoardItem",
    "heroSublimateItem",
    "heroSublimateUnlockItem",
    "limitBreakItem",
    "generalLimitBreakItem",
    "eventItem",
    "cpItem",
    "staminaTicket",
    "gachaTicket",
    "adventureTicket",
    "keyItem",
    "equipmentMaterial",
    "equipmentEnhanceItem",
    "equipmentEvolveItem",
    "familiaDonationItem",
    "memoriaExpItem",
    "lockAbilityItem",
    "unionRaidMedal",
    "pcTicket",
)
ADMIN_EXTRA_NAMES = (
    "skipTicket",
    "voteItem",
    "underPvpRevengeTicket",
    "underPvpStaminaTicket",
    "serialPvpSpTicketItem",
    "unionRaidNormalSpTicketItem",
    "unionRaidBossSpTicketItem",
    "dpItem",
    "alarmVoiceItem",
    "subscriptionTicket",
)
ADMIN_NAMES = NAMES + ADMIN_EXTRA_NAMES
DATED_TABLES = frozenset(("UserGachaTicket", "UserSubscriptionTicket"))
TABLE_NAMES = {"pcTicket": "UserPCTicket"}


def table_name(name):
    return TABLE_NAMES.get(name, "User" + name[0].upper() + name[1:])


IDENTITY_KEYS = {
    "UserEquipmentEnhanceItem": "enhanceItemId",
    "UserEquipmentEvolveItem": "evolveItemId",
    "UserGeneralLimitBreakItem": "limitBreakItemId",
    "UserUnionRaidMedal": "medalId",
    "UserPCTicket": "pcTicketId",
    "UserLockAbilityItem": "lockAbilityId",
    "UserSerialPvpSpTicketItem": "ticketId",
    "UserUnionRaidNormalSpTicketItem": "ticketId",
    "UserUnionRaidBossSpTicketItem": "ticketId",
}


def identity_key(table):
    return IDENTITY_KEYS.get(table, table[4:5].lower() + table[5:] + "Id")


def stock(table, row):
    return (
        len(row.get("dateList", [])) if table in DATED_TABLES else row.get("amount", 0)
    )


def inventory_limit(table):
    return 10000 if table in DATED_TABLES else 2147483647


def normalize_blacksmith(tables):
    """Recover granted stocks from legacy ID keys without granting new items."""
    changed = []
    for table in (
        "UserEquipmentEnhanceItem",
        "UserEquipmentEvolveItem",
        "UserLockAbilityItem",
        "UserSerialPvpSpTicketItem",
        "UserUnionRaidNormalSpTicketItem",
        "UserUnionRaidBossSpTicketItem",
    ):
        key = identity_key(table)
        old = table[4:5].lower() + table[5:] + "Id"
        rows = tables.get(table, [])
        if not any((old in row for row in rows)):
            continue
        by_id = {}
        for original in rows:
            row = dict(original)
            if old in row:
                legacy = row.pop(old)
                if key in row and row[key] != legacy:
                    raise ValueError("Conflicting blacksmith material identities")
                row[key] = legacy
            iid = row[key]
            if iid not in by_id:
                by_id[iid] = row
            else:
                existing = by_id[iid]
                for amount in ("amount", "reservedAmount", "cumulativeAmount"):
                    value = existing.get(amount, 0) + row.get(amount, 0)
                    if value > 2147483647:
                        raise ValueError(
                            "Blacksmith inventory exceeds client integer limit"
                        )
                    existing[amount] = value
        tables[table] = list(by_id.values())
        changed.append(table)
    return changed


@functools.cache
def catalog():
    result = {}
    for name in ADMIN_NAMES:
        table = table_name(name)
        directory = re.sub("(?<!^)(?=[A-Z])", "_", name).lower()
        items = {}
        for row in master.rows(master.master_bytes(name)):
            iid = row.number(0)
            title = row.text(
                5
                if name == "generalLimitBreakItem"
                else 3
                if name in ("limitBreakItem", "adventureTicket", "memoriaExpItem")
                else 2
            )
            icon = next(
                (
                    url
                    for suffix in ("_frame", "", "_noframe")
                    if (
                        url := original_url(f"files/icon/{directory}/{iid}{suffix}.png")
                    )
                ),
                None,
            )
            items[str(iid)] = {"id": iid, "name": title, "image": icon}
            if name in ADMIN_EXTRA_NAMES:
                items[str(iid)]["description"] = row.text(3)
        result[table] = items
    return result


def add(tables, table, key, iid, amount, uid, now):
    key = IDENTITY_KEYS.get(table, key)
    rows = tables.setdefault(table, [])
    row = next((r for r in rows if r[key] == iid), None)
    if row is None:
        row = {
            "userId": uid,
            key: iid,
            "amount": 0,
            "reservedAmount": 0,
            "cumulativeAmount": 0,
            "signature": 0,
            "bookEntryState": 1,
        }
        rows.append(row)
    if table in DATED_TABLES:
        dates = row.setdefault(
            "dateList", [{"aquiredAt": now} for _ in range(row.get("amount", 0))]
        )
        date = {"aquiredAt": now}
        if table == "UserSubscriptionTicket":
            definition = next(
                (
                    r
                    for r in master.rows(master.master_bytes("subscriptionTicket"))
                    if r.number(0) == iid
                )
            )
            date["expireAt"] = now + definition.number(4)
        dates.extend((dict(date) for _ in range(amount)))
        row.pop("amount", None)
        row.pop("reservedAmount", None)
    else:
        row["amount"] = row.get("amount", 0) + amount
    row["cumulativeAmount"] = row.get("cumulativeAmount", 0) + amount
    return row
