import logging
import asyncio
from bot.database.mongo import db
from bot.utils.rate_limit import notification_limiter
from pyrogram.errors import FloodWait

logger = logging.getLogger(__name__)

async def broadcast_message(client, message, target_ids, is_reply=False):
    """Broadcast a message to multiple targets."""
    sent = 0
    failed = 0
    
    for target_id in target_ids:
        try:
            await notification_limiter.acquire()
            if is_reply:
                await message.copy(target_id)
            else:
                await client.send_message(target_id, message)
            sent += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            # Retry once
            try:
                if is_reply: await message.copy(target_id)
                else: await client.send_message(target_id, message)
                sent += 1
            except: failed += 1
        except Exception:
            failed += 1
            
    return sent, failed

async def get_all_recipients():
    """Fetch all unique user and group IDs for broadcasting."""
    # This matches the aggregation logic in bot.py
    pipeline = [
        {"$unwind": "$chats"},
        {"$group": {"_id": "$chats.chat_id"}}
    ]
    cursor = db.userdb.aggregate(pipeline)
    group_ids = [doc["_id"] async for doc in cursor]
    
    user_cursor = db.userdb.find({"is_bot": False}, {"user_id": 1})
    user_ids = [doc["user_id"] async for doc in user_cursor]
    
    return list(set(group_ids + user_ids))
