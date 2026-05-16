import logging
from bot.database.mongo import db
from bot.utils.cache import layered_cache, _make_show_cache_key
from bot.utils.ids import normalize_show_slug

logger = logging.getLogger(__name__)


async def update_poster(category, show_name, file_ids):
    """Update posters for a show."""
    try:
        result = await db.shows.update_one(
            {"show_name": show_name, "category": category},
            {"$set": {"poster": file_ids}}
        )
        cache_key = _make_show_cache_key(category, normalize_show_slug(show_name))
        layered_cache.invalidate_show(cache_key)
        return result.matched_count > 0
    except Exception as e:
        logger.error(f"Error updating poster: {e}")
        return False


async def add_episode(category, show_name, season_number, episode_data):
    """Add a new episode to a show (handles both season and flat structure)."""
    try:
        # Verify show exists directly from DB — no full cache load
        exists = await db.shows.find_one(
            {"category": category, "show_name": show_name},
            {"_id": 1}
        )
        if not exists:
            return False, "Show not found."

        query = {"category": category, "show_name": show_name}
        if season_number:
            update = {"$push": {f"episodes.{season_number}": episode_data}}
        else:
            update = {"$push": {"episodes.episodes": episode_data}}

        await db.shows.update_one(query, update)
        cache_key = _make_show_cache_key(category, normalize_show_slug(show_name))
        layered_cache.invalidate_show(cache_key)
        return True, "✅ Episode added."
    except Exception as e:
        logger.error(f"Error adding episode: {e}")
        return False, str(e)


async def convert_to_split(category, show_name, season_number, episode_num):
    """Convert a single episode to a split (2 parts)."""
    try:
        from bot.services.shows import get_show_detail
        show_doc = await get_show_detail(category, show_name)
        if not show_doc:
            return False, "Show not found."

        episodes_dict = show_doc.get("episodes", {})
        key = season_number if season_number else "episodes"
        episodes = episodes_dict.get(key, [])

        idx = episode_num - 1
        if idx < 0 or idx >= len(episodes):
            return False, "Episode not found."

        entry = episodes[idx]
        if isinstance(entry, list):
            return False, "Already split."

        new_entry = [entry["content"] if isinstance(entry, dict) else entry, None]

        await db.shows.update_one(
            {"category": category, "show_name": show_name},
            {"$set": {f"episodes.{key}.{idx}": new_entry}}
        )
        cache_key = _make_show_cache_key(category, normalize_show_slug(show_name))
        layered_cache.invalidate_show(cache_key)
        return True, "✅ Converted to split."
    except Exception as e:
        logger.error(f"Error converting to split: {e}")
        return False, str(e)
