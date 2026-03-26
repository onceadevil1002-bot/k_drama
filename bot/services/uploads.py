import logging
import time
from bot.database.mongo import db
from bot.services.shows import get_cached_data
from bot.utils.cache import show_cache

logger = logging.getLogger(__name__)

async def update_poster(category, show_name, file_ids):
    """Update posters for a show."""
    try:
        result = await db.shows.update_one(
            {"show_name": show_name, "category": category},
            {"$set": {"poster": file_ids}}
        )
        show_cache.clear()
        return result.matched_count > 0
    except Exception as e:
        logger.error(f"Error updating poster: {e}")
        return False

async def add_episode(category, show_name, season_number, episode_data):
    """Add a new episode to a show (handles both season and flat structure)."""
    try:
        data = await get_cached_data()
        if category not in data or show_name not in data[category]:
            return False, "Show not found."

        query = {"category": category, "show_name": show_name}
        if season_number:
            update = {"$push": {f"episodes.{season_number}": episode_data}}
        else:
            update = {"$push": {"episodes.episodes": episode_data}}
            
        await db.shows.update_one(query, update)
        show_cache.clear()
        return True, "✅ Episode added."
    except Exception as e:
        logger.error(f"Error adding episode: {e}")
        return False, str(e)

async def convert_to_split(category, show_name, season_number, episode_num):
    """Convert a single episode to a split (2 parts)."""
    try:
        data = await get_cached_data()
        show_data = data.get(category, {}).get(show_name, {})
        key = season_number if season_number else "episodes"
        episodes = show_data.get(key, [])
        
        idx = episode_num - 1
        if idx < 0 or idx >= len(episodes):
            return False, "Episode not found."
            
        entry = episodes[idx]
        if isinstance(entry, list):
            return False, "Already split."
            
        # Convert to list [content, None]
        new_entry = [entry["content"] if isinstance(entry, dict) else entry, None]
        
        await db.shows.update_one(
            {"category": category, "show_name": show_name},
            {"$set": {f"episodes.{key}.{idx}": new_entry}}
        )
        show_cache.clear()
        return True, "✅ Converted to split."
    except Exception as e:
        logger.error(f"Error converting to split: {e}")
        return False, str(e)
