import logging
import time
from bot.database.mongo import db

logger = logging.getLogger(__name__)

async def add_recent_update(category, show_name, season, episode_num):
    """Add a new entry to the recent updates list."""
    update = {
        "timestamp": int(time.time()),
        "category": category,
        "show_name": show_name,
        "season": season,
        "episode_num": episode_num
    }
    
    await db.updates.insert_one(update)
    # Keep only most recent 20
    count = await db.updates.count_documents({})
    if count > 20:
        # Find the oldest and delete
        oldest = await db.updates.find().sort("timestamp", 1).limit(count - 20).to_list(None)
        ids = [doc["_id"] for doc in oldest]
        await db.updates.delete_many({"_id": {"$in": ids}})

async def get_recent_updates(limit=10):
    """Retrieve the most recent updates."""
    cursor = db.updates.find().sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)

def format_time_ago(timestamp):
    """Format timestamp as 'X min/hours/days ago'."""
    now = int(time.time())
    diff = now - timestamp
    
    if diff < 60:
        return "just now"
    elif diff < 3600:
        mins = diff // 60
        return f"{mins} min ago" if mins == 1 else f"{mins} mins ago"
    elif diff < 86400:
        hours = diff // 3600
        return f"{hours} hour ago" if hours == 1 else f"{hours} hours ago"
    else:
        days = diff // 86400
        return f"{days} day ago" if days == 1 else f"{days} days ago"
