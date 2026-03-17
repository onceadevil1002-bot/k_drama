import logging
import time
from bot.database.mongo import db
from bot.utils.cache import show_cache

logger = logging.getLogger(__name__)

async def load_data():
    """Load show data from MongoDB."""
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
            category = doc.get("category", "Hindi Dubbed")
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
    """Get show data from cache or load it."""
    cached = show_cache.get()
    if cached:
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
        logger.debug(f"View increment error: {e}")

async def get_trending_shows(limit=10):
    """Get top viewed shows from database."""
    try:
        cursor = db.stats.find().sort("views", -1).limit(limit)
        return await cursor.to_list(length=limit)
    except Exception as e:
        logger.exception(f"Error fetching trending shows: {e}")
        return []
