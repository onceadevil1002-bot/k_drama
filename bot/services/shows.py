import logging
import time
from bot.database.mongo import db
from bot.utils.cache import show_cache

logger = logging.getLogger(__name__)

# ─── Legacy → Canonical category name map ────────────────────────────────────
# ONLY add entries here for categories that were renamed.
# The DB records are NOT modified — this is a runtime-only normalization.
_LEGACY_CATEGORY_MAP = {
    # Old Hindi-dubbed names
    "Hindi Dubbed":  "K-Hindi",
    "Hindi Dub":     "K-Hindi",
    "hindi dubbed":  "K-Hindi",
    "hindi dub":     "K-Hindi",
    # Old regional/original names
    "Regional":      "K-Original",
    "regional":      "K-Original",
    # Old C-Drama names
    "C Drama":       "CT Drama",
    "c drama":       "CT Drama",
    "CDrama":        "CT Drama",
    "cdrama":        "CT Drama",
    # Old Global/Arabic names
    "Arabic":        "Global",
    "arabic":        "Global",
}


async def load_data():
    """Load show data from MongoDB, normalizing legacy category names."""
    data = {}
    projection = {
        "category": 1,
        "show_name": 1,
        "episodes": 1,
        "poster": 1,
        "_id": 0
    }

    try:
        cursor = db.shows.find({}, projection)
        async for doc in cursor:
            raw_category = doc.get("category", "K-Hindi")
            # Normalize any old category name to the current canonical name
            category = _LEGACY_CATEGORY_MAP.get(raw_category, raw_category)
            if category != raw_category:
                logger.debug(
                    f"load_data: normalized category '{raw_category}' → '{category}' "
                    f"for show '{doc.get('show_name', '?')}'"
                )
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
        logger.exception(f"Error loading data: {e}")
        return {}


async def get_cached_data():
    """
    Get show data from cache or load it.
    Uses an asyncio.Lock to prevent cache stampede:
    when the cache expires, only ONE coroutine fetches from MongoDB
    while all others wait for the refreshed result.
    """
    cached = show_cache.get()
    if cached is not None:
        return cached

    # Acquire lock — only one coroutine proceeds to fetch from DB
    async with show_cache.lock:
        # Double-check: another coroutine may have populated cache while we waited
        cached = show_cache.get()
        if cached is not None:
            return cached

        data = await load_data()
        show_cache.set(data)
        return data


async def increment_view(category, show_name):
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
                    "last_viewed": datetime.now()
                }
            },
            upsert=True
        )
    except Exception as e:
        logger.debug(f"View increment error for '{show_name}': {e}")


async def get_trending_shows(limit=10):
    """Get top viewed shows from database."""
    try:
        cursor = db.stats.find().sort("views", -1).limit(limit)
        return await cursor.to_list(length=limit)
    except Exception as e:
        logger.exception(f"Error fetching trending shows: {e}")
        return []
