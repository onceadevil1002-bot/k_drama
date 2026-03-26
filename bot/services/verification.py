import logging
from datetime import datetime
from bot.database.mongo import db
from bot.config import REQUIRED_CHANNELS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RESOLVED CHANNEL ID REGISTRY
# Populated once at startup by resolve_required_channels(client).
# Maps @username (or whatever is in config) -> numeric channel ID (int).
# All DB operations use numeric IDs so they match Telegram event IDs exactly.
# ---------------------------------------------------------------------------
_resolved_channel_ids: dict = {}   # e.g. {"@Seoul_Entertainment_DKD": -1002648019848}

# Short-lived cache for live_check_and_sync results
# Key: user_id, Value: (result_list, timestamp)
# 5 minute TTL — balances accuracy vs API call reduction
_verification_cache: dict = {}
_VERIFICATION_TTL = 300  # seconds


async def resolve_required_channels(client):
    """
    Call once at startup (after super().start()).
    Resolves every entry in REQUIRED_CHANNELS to a numeric ID via get_chat(),
    which also caches the peer in Pyrogram's session so numeric lookups work.
    """
    global _resolved_channel_ids
    _resolved_channel_ids = {}
    for ch in REQUIRED_CHANNELS:
        try:
            chat = await client.get_chat(ch)
            numeric_id = chat.id
            _resolved_channel_ids[str(ch)] = numeric_id
            logger.info(f"Resolved channel {ch} -> {numeric_id}")
        except Exception as e:
            logger.error(f"Failed to resolve channel {ch}: {e}")


def get_resolved_ids() -> list:
    """Return list of resolved numeric channel IDs."""
    return list(_resolved_channel_ids.values())


def channel_key(channel_id) -> str:
    """Normalise any channel reference to a consistent string key for the DB."""
    return str(int(channel_id))


# ---------------------------------------------------------------------------
# BAN CHECKS
# ---------------------------------------------------------------------------

async def is_banned(user_id: int) -> bool:
    doc = await db.banned_users.find_one({"user_id": user_id, "active": True})
    return doc is not None


async def get_ban_doc(user_id: int):
    return await db.banned_users.find_one({"user_id": user_id, "active": True})


async def ban_user(user_id: int, username: str, full_name: str, leave_count: int):
    await db.banned_users.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "banned_at": datetime.now(),
            "leave_count": leave_count,
            "active": True
        }},
        upsert=True
    )
    logger.info(f"Banned user {user_id} ({full_name}) after {leave_count} leave cycles.")


async def unban_user(user_id: int):
    await db.banned_users.update_one(
        {"user_id": user_id},
        {"$set": {"active": False, "unbanned_at": datetime.now()}}
    )
    await db.channel_members.update_many(
        {"user_id": user_id},
        {"$set": {"leave_count": 0, "warned": False}}
    )
    logger.info(f"Unbanned user {user_id}.")


# ---------------------------------------------------------------------------
# MEMBERSHIP CHECKS — DB only, zero live API calls after startup
# ---------------------------------------------------------------------------

async def is_member_in_all_channels(user_id: int) -> bool:
    """Returns True only if user has status='member' in ALL required channels."""
    resolved = get_resolved_ids()
    if not resolved:
        # Channels not resolved yet — fail safe
        return False
    for numeric_id in resolved:
        doc = await db.channel_members.find_one({
            "user_id": user_id,
            "channel_id": channel_key(numeric_id),
            "status": "member"
        })
        if not doc:
            return False
    return True


async def get_missing_channels(user_id: int) -> list:
    """
    Returns list of (config_name, numeric_id) tuples for channels
    the user has NOT joined. Used to build join buttons.
    If channel registry not loaded yet, treat ALL channels as missing (fail safe).
    """
    if not _resolved_channel_ids:
        # Channels not resolved yet — fail safe, block everyone
        return [(ch, None) for ch in REQUIRED_CHANNELS]
    missing = []
    for config_name, numeric_id in _resolved_channel_ids.items():
        doc = await db.channel_members.find_one({
            "user_id": user_id,
            "channel_id": channel_key(numeric_id),
            "status": "member"
        })
        if not doc:
            missing.append((config_name, numeric_id))
    return missing


async def live_check_and_sync(client, user_id: int) -> list:
    """
    Checks ALL required channels via live Telegram API for this user.
    Called on every /start and every 'I Joined' button press.

    Why live and not DB-only:
    Telegram does NOT reliably send leave events for channels to bots.
    So DB can show 'member' even after user left. Live check is the only
    reliable way to catch leaves.

    Writes result back to DB so channel_events history stays consistent.
    Returns list of (config_name, numeric_id) tuples the user is NOT in.
    Covers all edge cases: admins, creators, race conditions, unreliable leave events.
    """
    from pyrogram.enums import ChatMemberStatus
    from pyrogram.errors import UserNotParticipant, FloodWait
    import asyncio

    if not _resolved_channel_ids:
        return [(ch, None) for ch in REQUIRED_CHANNELS]

    # Check cache — skip 2 API calls if checked recently
    import time as _time
    cached = _verification_cache.get(user_id)
    if cached:
        result, ts = cached
        if _time.time() - ts < _VERIFICATION_TTL:
            return result

    still_missing = []
    for config_name, numeric_id in _resolved_channel_ids.items():
        try:
            member = await client.get_chat_member(numeric_id, user_id)
            is_in = member.status in {
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER
            }
            if is_in:
                # Confirmed in — write/update DB record
                user = member.user
                username = user.username or "" if user else ""
                full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() if user else ""
                await record_join(user_id, numeric_id, username, full_name)
            else:
                # Confirmed out — handle as leave (increments count if was member)
                await _handle_detected_leave(user_id, numeric_id)
                still_missing.append((config_name, numeric_id))
        except UserNotParticipant:
            # Definitely not in — handle as left
            await _handle_detected_leave(user_id, numeric_id)
            still_missing.append((config_name, numeric_id))
        except FloodWait as e:
            logger.warning(f"FloodWait {e.value}s during live_check for {user_id}")
            await asyncio.sleep(e.value)
            still_missing.append((config_name, numeric_id))
        except Exception as e:
            logger.warning(f"live_check_and_sync error for {user_id} in {numeric_id}: {e}")
            doc = await db.channel_members.find_one({
                "user_id": user_id,
                "channel_id": channel_key(numeric_id),
                "status": "member"
            })
            if not doc:
                still_missing.append((config_name, numeric_id))

    # Cache result
    import time as _time
    _verification_cache[user_id] = (still_missing, _time.time())
    # Evict old entries to prevent unbounded growth
    if len(_verification_cache) > 5000:
        oldest = sorted(_verification_cache, key=lambda k: _verification_cache[k][1])[:1000]
        for k in oldest:
            _verification_cache.pop(k, None)

    return still_missing


async def _handle_detected_leave(user_id: int, numeric_id: int):
    """
    Called when live check confirms user is NOT in a channel.
    Only increments leave_count if DB previously had them as 'member'
    — meaning they actually left, not just never joined.
    This is the abuse detection trigger since Telegram leave events
    are unreliable for channels.
    """
    key = channel_key(numeric_id)
    now = datetime.now()

    existing = await db.channel_members.find_one({"user_id": user_id, "channel_id": key})

    if existing and existing.get("status") == "member":
        # They WERE a member — this is a real leave, count it
        await db.channel_members.update_one(
            {"user_id": user_id, "channel_id": key},
            {
                "$set": {"status": "left", "last_left": now},
                "$inc": {"leave_count": 1},
                "$push": {"history": {"event": "leave_detected_live", "timestamp": now}}
            }
        )
        logger.info(f"Leave detected via live check for user {user_id} in {numeric_id}")
    else:
        # Never joined or already marked left — just ensure status is correct
        await db.channel_members.update_one(
            {"user_id": user_id, "channel_id": key},
            {"$set": {"status": "left"}},
            upsert=True
        )

    # Re-fetch to get updated leave_count for abuse check
    return await db.channel_members.find_one({"user_id": user_id, "channel_id": key})


# ---------------------------------------------------------------------------
# MEMBER RECORD MANAGEMENT — always uses numeric channel_id string
# ---------------------------------------------------------------------------

async def record_join(user_id: int, channel_id: int, username: str = "", full_name: str = ""):
    # Invalidate verification cache so next /start reflects join immediately
    _verification_cache.pop(user_id, None)
    now = datetime.now()
    key = channel_key(channel_id)
    await db.channel_members.update_one(
        {"user_id": user_id, "channel_id": key},
        {
            "$set": {
                "status": "member",
                "last_joined": now,
                "username": username,
                "full_name": full_name
            },
            "$inc": {"join_count": 1},
            "$push": {"history": {"event": "join", "timestamp": now}},
            "$setOnInsert": {
                "leave_count": 0,
                "warned": False,
                "first_seen": now
            }
        },
        upsert=True
    )


async def record_leave(user_id: int, channel_id: int, username: str = "", full_name: str = "") -> dict:
    now = datetime.now()
    key = channel_key(channel_id)
    await db.channel_members.update_one(
        {"user_id": user_id, "channel_id": key},
        {
            "$set": {
                "status": "left",
                "last_left": now,
                "username": username,
                "full_name": full_name
            },
            "$inc": {"leave_count": 1},
            "$push": {"history": {"event": "leave", "timestamp": now}}
        },
        upsert=True
    )
    return await db.channel_members.find_one({"user_id": user_id, "channel_id": key})


async def set_warned(user_id: int):
    await db.channel_members.update_many(
        {"user_id": user_id},
        {"$set": {"warned": True}}
    )


async def is_warned(user_id: int) -> bool:
    doc = await db.channel_members.find_one({"user_id": user_id, "warned": True})
    return doc is not None


async def get_leave_count(user_id: int) -> int:
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$user_id", "max_leaves": {"$max": "$leave_count"}}}
    ]
    result = await db.channel_members.aggregate(pipeline).to_list(1)
    return result[0]["max_leaves"] if result else 0


# ---------------------------------------------------------------------------
# CHANNEL SCAN — startup + midnight
# ---------------------------------------------------------------------------

async def scan_channel_members(client, numeric_id: int):
    """Scan all current members of a channel and seed/sync the DB."""
    key = channel_key(numeric_id)
    logger.info(f"Scanning channel {numeric_id}...")
    found_ids = set()
    count = 0
    try:
        async for member in client.get_chat_members(numeric_id):
            user = member.user
            if not user or user.is_bot:
                continue
            user_id = user.id
            found_ids.add(user_id)
            username = user.username or ""
            full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
            now = datetime.now()
            await db.channel_members.update_one(
                {"user_id": user_id, "channel_id": key},
                {
                    "$set": {
                        "status": "member",
                        "username": username,
                        "full_name": full_name,
                        "last_scanned": now
                    },
                    "$setOnInsert": {
                        "join_count": 1,
                        "leave_count": 0,
                        "warned": False,
                        "first_seen": now,
                        "last_joined": now,
                        "history": [{"event": "join", "timestamp": now}]
                    }
                },
                upsert=True
            )
            count += 1

        # Anyone previously 'member' but not in scan = left between rescans
        await db.channel_members.update_many(
            {
                "channel_id": key,
                "status": "member",
                "user_id": {"$nin": list(found_ids)}
            },
            {
                "$set": {"status": "left", "last_left": datetime.now()},
                "$push": {"history": {"event": "leave_detected_on_scan", "timestamp": datetime.now()}}
            }
        )
        logger.info(f"Scan complete for {numeric_id}: {count} members found.")
    except Exception as e:
        logger.exception(f"scan_channel_members error for {numeric_id}: {e}")


async def scan_all_channels(client):
    """Scan all required channels. Called on startup and midnight."""
    logger.info("Starting full channel member scan...")
    for numeric_id in get_resolved_ids():
        await scan_channel_members(client, numeric_id)
    logger.info("Full channel member scan complete.")
