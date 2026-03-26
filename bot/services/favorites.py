import logging
from bot.database.mongo import db

from datetime import datetime
logger = logging.getLogger(__name__)

async def add_favorite(user_id, category, show_name, show_slug):
    """Add a show to user favorites."""
    try:
        await db.favorites.update_one(
            {"user_id": user_id, "show_slug": show_slug},
            {"$set": {
                "category": category,
                "show_name": show_name,
                "added_at": datetime.now()
            }},
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"Error adding favorite: {e}")
        return False

async def remove_favorite(user_id, show_slug):
    """Remove a show from user favorites."""
    try:
        await db.favorites.delete_one({"user_id": user_id, "show_slug": show_slug})
        return True
    except Exception as e:
        logger.error(f"Error removing favorite: {e}")
        return False

async def is_favorited(user_id, show_slug):
    """Check if a show is in user favorites."""
    count = await db.favorites.count_documents({"user_id": user_id, "show_slug": show_slug})
    return count > 0

async def get_user_favorites(user_id):
    """Retrieve all favorites for a user."""
    cursor = db.favorites.find({"user_id": user_id}).sort("added_at", -1)
    return await cursor.to_list(length=100)
