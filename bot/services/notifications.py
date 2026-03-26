import logging
import asyncio
from pyrogram.errors import FloodWait
from bot.database.mongo import db
from bot.services.admin import broadcast_message

logger = logging.getLogger(__name__)

async def notify_new_content(client, category, show_name, show_slug, is_new_show):
    """Notify relevant users about new content (new show or new episode)."""
    # 1. Get users who favorited this show
    faved_cursor = db.favorites.find({"show_slug": show_slug}, {"user_id": 1})
    faved_ids = [doc["user_id"] async for doc in faved_cursor]
    
    # 2. If it's a new show, get users with global notifications enabled
    global_ids = []
    if is_new_show:
        global_cursor = db.userdb.find({"allow_global_notifications": True}, {"user_id": 1})
        global_ids = [doc["user_id"] async for doc in global_cursor]
    
    # Merge and deduplicate
    targets = list(set(faved_ids + global_ids))
    
    message = (
        f"📺 **New Content Added!**\n\n"
        f"🎬 Show: **{show_name}**\n"
        f"📂 Category: {category}\n\n"
        f"{'🔥 This is a brand new show!' if is_new_show else '🆕 A new episode has been uploaded.'}\n\n"
        f"Tap /start to watch now!"
    )
    
    await broadcast_message(client, message, targets)
