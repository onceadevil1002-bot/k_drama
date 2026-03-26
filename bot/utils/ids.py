import hashlib
import base64
import time
import logging
from collections import OrderedDict
from bot.database.mongo import db

logger = logging.getLogger(__name__)

_hash_memory_cache = OrderedDict()


async def make_id(value: str) -> str:
    """
    Create stable hash with MongoDB persistence.
    OPTIMIZATION: If hash already in memory cache, skip DB write entirely.
    Previously every show button build was hitting MongoDB even for known hashes.
    """
    if not value:
        return "empty"

    h = hashlib.sha256(value.strip().lower().encode()).hexdigest()[:16]

    # Already cached — skip DB write completely
    if h in _hash_memory_cache:
        _hash_memory_cache.move_to_end(h)
        return h

    # Not cached — write to DB then cache
    try:
        await db.hash_lookup.update_one(
            {"hash": h},
            {"$set": {"hash": h, "value": value, "last_accessed": time.time()}},
            upsert=True
        )
    except Exception as e:
        logger.debug(f"Hash storage error: {e}")

    _hash_memory_cache[h] = value
    if len(_hash_memory_cache) > 10000:
        _hash_memory_cache.popitem(last=False)

    return h


async def resolve_id(h: str) -> str:
    if not h or h == "empty":
        return h

    if isinstance(h, bytes):
        try:
            h = h.decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
            return h

    if h in _hash_memory_cache:
        _hash_memory_cache.move_to_end(h)
        return _hash_memory_cache[h]

    try:
        result = await db.hash_lookup.find_one(
            {"hash": h},
            {"value": 1, "_id": 0}
        )
        if result:
            value = result["value"]
            _hash_memory_cache[h] = value
            if len(_hash_memory_cache) > 10000:
                _hash_memory_cache.popitem(last=False)
            return value
    except Exception as e:
        logger.debug(f"Hash lookup error: {e}")

    return h


async def warm_hash_cache():
    """
    Pre-load all hashes from DB into memory on startup.
    After this runs, make_id and resolve_id are pure in-memory (~0ms).
    Called once from Bot.start() before handlers begin processing.
    """
    try:
        count = 0
        async for doc in db.hash_lookup.find({}, {"hash": 1, "value": 1, "_id": 0}):
            _hash_memory_cache[doc["hash"]] = doc["value"]
            count += 1
        while len(_hash_memory_cache) > 10000:
            _hash_memory_cache.popitem(last=False)
        logger.info(f"Hash cache warmed: {count} entries loaded")
    except Exception as e:
        logger.warning(f"Hash cache warm error: {e}")


def normalize_show_slug(show_name: str) -> str:
    if not show_name:
        return "unknown"
    encoded = base64.urlsafe_b64encode(show_name.encode('utf-8')).decode('ascii')
    return encoded.rstrip('=')


def decode_show_slug(slug: str) -> str:
    try:
        padded = slug + "=" * ((4 - len(slug) % 4) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode('utf-8')).decode('utf-8')
        return decoded
    except Exception:
        return slug.replace("_", " ")
