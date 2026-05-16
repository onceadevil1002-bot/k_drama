import asyncio
import logging
from bot.database.mongo import db

logger = logging.getLogger(__name__)

# ─── Legacy → Canonical category name map ────────────────────────────────────
_LEGACY_CATEGORY_MAP = {
    "Hindi Dubbed":  "K-Hindi",
    "Hindi Dub":     "K-Hindi",
    "hindi dubbed":  "K-Hindi",
    "hindi dub":     "K-Hindi",
    "Regional":      "K-Original",
    "regional":      "K-Original",
    "C Drama":       "CT Drama",
    "c drama":       "CT Drama",
    "CDrama":        "CT Drama",
    "cdrama":        "CT Drama",
    "Arabic":        "Global",
    "arabic":        "Global",
}


# ─── L1 Loader ────────────────────────────────────────────────────────────────

async def load_category_index(category: str) -> list:
    """
    Load show metadata for ONE category from MongoDB.
    Fetches only show_name and poster — NOT episode data.
    This is the L1 loader called on cache miss.
    """
    projection = {"show_name": 1, "poster": 1, "_id": 0}
    results = []
    try:
        # Build query that handles legacy category names stored in DB
        raw_variants = [category] + [
            old for old, canonical in _LEGACY_CATEGORY_MAP.items()
            if canonical == category
        ]
        cursor = db.shows.find({"category": {"$in": raw_variants}}, projection)
        async for doc in cursor:
            results.append({
                "show_name": doc.get("show_name", ""),
                "poster": doc.get("poster", []),
                "category": category,
            })
        return results
    except Exception as e:
        logger.exception(f"load_category_index error for '{category}': {e}")
        return []


# ─── L2 Loader ────────────────────────────────────────────────────────────────

async def load_show_detail(category: str, show_name: str) -> dict | None:
    """
    Load the full document for ONE show from MongoDB.
    Includes all episode data. This is the L2 loader called on cache miss.
    """
    try:
        raw_variants = [category] + [
            old for old, canonical in _LEGACY_CATEGORY_MAP.items()
            if canonical == category
        ]
        doc = await db.shows.find_one({
            "category": {"$in": raw_variants},
            "show_name": show_name,
        })
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return doc
    except Exception as e:
        logger.exception(f"load_show_detail error for '{show_name}': {e}")
        return None


# ─── Public APIs ──────────────────────────────────────────────────────────────

async def get_category_shows(category: str) -> list:
    """
    Returns list of { show_name, poster, category } for a category.
    Uses L1 cache. Fetches from DB on miss.
    """
    from bot.utils.cache import layered_cache
    return await layered_cache.get_category(
        category,
        loader_fn=lambda: load_category_index(category)
    )


async def get_show_detail(category: str, show_name: str) -> dict | None:
    """
    Returns the full show document (seasons, episodes, qualities, file IDs).
    Uses L2 cache. Fetches from DB on miss.
    Triggers a silent background refresh when entry is near expiry.
    """
    from bot.utils.cache import layered_cache
    from bot.utils.ids import normalize_show_slug

    slug = normalize_show_slug(show_name)
    # Include category in cache key to differentiate same show in different categories
    cache_key = f"{category}:{slug}"

    if layered_cache.needs_background_refresh(cache_key):
        asyncio.create_task(_background_refresh_show(category, show_name, cache_key))

    return await layered_cache.get_show(
        cache_key,
        loader_fn=lambda: load_show_detail(category, show_name)
    )


async def _background_refresh_show(category: str, show_name: str, cache_key: str):
    """Silently refresh a show's L2 cache entry before it expires."""
    from bot.utils.cache import layered_cache
    try:
        fresh_data = await load_show_detail(category, show_name)
        if fresh_data:
            layered_cache.set_show(cache_key, fresh_data)
            logger.debug(f"Background refresh complete for '{show_name}'")
    except Exception as e:
        logger.debug(f"Background refresh failed for '{show_name}': {e}")


# ─── Legacy shim — used by admin_cmds selftest only ───────────────────────────

async def get_cached_data() -> dict:
    """
    LEGACY SHIM — returns full {category: {show_name: show_data}} dict.
    Still used by admin selftest to count total shows.
    Do NOT add new callers — use get_category_shows / get_show_detail instead.
    """
    from bot.utils.cache import show_cache
    cached = show_cache.get()
    if cached is not None:
        return cached

    async with show_cache.lock:
        cached = show_cache.get()
        if cached is not None:
            return cached
        data = await _load_all_data()
        show_cache.set(data)
        return data


async def _load_all_data() -> dict:
    """Full monolithic load — used only by legacy get_cached_data shim."""
    data = {}
    projection = {"category": 1, "show_name": 1, "episodes": 1, "poster": 1, "_id": 0}
    try:
        cursor = db.shows.find({}, projection)
        async for doc in cursor:
            raw_category = doc.get("category", "K-Hindi")
            category = _LEGACY_CATEGORY_MAP.get(raw_category, raw_category)
            show_name = doc["show_name"]
            episodes = doc.get("episodes", {})
            poster = doc.get("poster", [])
            if category not in data:
                data[category] = {}
            if isinstance(episodes, dict):
                episodes["poster"] = poster
                data[category][show_name] = episodes
            else:
                data[category][show_name] = {"episodes": episodes, "poster": poster}
        return data
    except Exception as e:
        logger.exception(f"_load_all_data error: {e}")
        return {}


# ─── Utility functions ────────────────────────────────────────────────────────

async def increment_view(category: str, show_name: str):
    """Increment view count for a show."""
    from bot.utils.ids import normalize_show_slug
    from datetime import datetime
    try:
        slug = normalize_show_slug(show_name)
        await db.stats.update_one(
            {"show_slug": slug},
            {
                "$inc": {"views": 1},
                "$set": {
                    "show_name": show_name,
                    "category": category,
                    "last_viewed": datetime.now(),
                },
            },
            upsert=True,
        )
    except Exception as e:
        logger.debug(f"View increment error for '{show_name}': {e}")


async def get_trending_shows(limit: int = 10) -> list:
    """Get top viewed shows from database."""
    try:
        cursor = db.stats.find().sort("views", -1).limit(limit)
        return await cursor.to_list(length=limit)
    except Exception as e:
        logger.exception(f"Error fetching trending shows: {e}")
        return []
