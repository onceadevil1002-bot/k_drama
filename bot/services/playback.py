import logging
import asyncio
from pyrogram import Client
from bot.database.mongo import db
from bot.utils.ui import auto_delete_message

logger = logging.getLogger(__name__)

async def send_episode_file(client, user_id, show_name, episode_num, file_id, file_type="video", quality=None):
    """Send an episode file to a user with auto-deletion and view tracking."""
    try:
        caption = f"🎬 **{show_name}** - Episode {episode_num}"
        if quality:
            caption += f" ({quality})"
            
        if file_type == "video":
            sent = await client.send_video(user_id, video=file_id, caption=caption)
        elif file_type == "document":
            sent = await client.send_document(user_id, document=file_id, caption=caption)
        else:
            return None
            
        # Start background tasks
        asyncio.create_task(db.increment_view(show_name))
        asyncio.create_task(auto_delete_message(sent, 180))
        return sent
    except Exception as e:
        logger.error(f"Error sending episode file: {e}")
        return None

async def increment_view(show_name):
    """Increment the view count for a show."""
    await db.shows.update_one(
        {"show_name": show_name},
        {"$inc": {"views": 1}}
    )
