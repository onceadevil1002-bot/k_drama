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
    
    await db.recent_updates.insert_one(update)
    logger.debug(f"Added recent update: {show_name} S{season} E{episode_num}")
    
    # Flush entries older than 7 days (weekly retention)
    seven_days_ago = int(time.time()) - (7 * 24 * 3600)
    await db.recent_updates.delete_many({"timestamp": {"$lt": seven_days_ago}})

async def get_recent_updates(limit=1000):
    """Retrieve the most recent updates from the last 7 days."""
    seven_days_ago = int(time.time()) - (7 * 24 * 3600)
    query = {"timestamp": {"$gte": seven_days_ago}}
    
    cursor = db.recent_updates.find(query).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)

def get_formatted_recent_updates(updates):
    """
    Groups updates by show and season, sorts episodes, 
    and returns a list of formatted strings.
    """
    if not updates:
        return []

    # Grouping structure: {(show_name, season): {"episodes": set(), "timestamp": max_ts}}
    grouped = {}
    for u in updates:
        key = (u["show_name"], u["season"])
        if key not in grouped:
            grouped[key] = {"episodes": set(), "timestamp": u["timestamp"]}
        
        grouped[key]["episodes"].add(int(u["episode_num"]))
        if u["timestamp"] > grouped[key]["timestamp"]:
            grouped[key]["timestamp"] = u["timestamp"]

    # Convert to list and sort by timestamp DESC (latest first)
    sorted_groups = sorted(
        grouped.items(), 
        key=lambda x: x[1]["timestamp"], 
        reverse=True
    )

    formatted_results = []
    for (show_name, season), data in sorted_groups:
        sorted_episodes = sorted(list(data["episodes"]))
        ep_str = ", ".join([f"E{e}" for e in sorted_episodes])
        
        # Format date as DD Mon (e.g., 24 Mar)
        import datetime
        dt = datetime.datetime.fromtimestamp(data["timestamp"])
        date_str = dt.strftime("%d %b")
        
        formatted_results.append(
            f"**{show_name}** (S{season}: {ep_str}) — {date_str}"
        )

    return formatted_results

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
