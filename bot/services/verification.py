import logging
import asyncio
from datetime import datetime, timedelta
from bot.database.mongo import db
from bot.utils.cache import verify_cache
from bot.config import REQUIRED_CHANNELS
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant

logger = logging.getLogger(__name__)

async def get_verification_status(user_id):
    """Check membership verification status with caching."""
    # Check memory cache first
    cached = verify_cache.get(user_id)
    if cached:
        return True
    
    try:
        doc = await db.user_verification.find_one({"user_id": user_id})
        if not doc:
            return False
        
        last_verified = doc.get("last_verified")
        if not last_verified:
            return False
        
        # 48 hour verification window
        is_verified = datetime.now() - last_verified < timedelta(hours=48)
        if is_verified:
            verify_cache[user_id] = True
        return is_verified
    except Exception as e:
        logger.debug(f"Verification check error: {e}")
        return False

async def update_verification(user_id):
    """Update verification timestamp in DB and cache."""
    try:
        await db.user_verification.update_one(
            {"user_id": user_id},
            {"$set": {"last_verified": datetime.now()}},
            upsert=True
        )
        verify_cache[user_id] = True
    except Exception as e:
        logger.debug(f"Verification update error: {e}")

async def check_membership(client, user_id):
    """Verify if user is a member of required channels."""
    missing = []

    async def resolve_channel(channel_ref):
        if isinstance(channel_ref, (int, str)) and str(channel_ref).lstrip("-").isdigit():
            return int(channel_ref)
        channel_text = str(channel_ref).strip()
        if "t.me/" in channel_text:
            channel_text = channel_text.split("t.me/", 1)[1].strip("/ ")
        if channel_text.startswith("@"):
            channel_text = channel_text[1:]
        return channel_text

    for ch in REQUIRED_CHANNELS:
        resolved = await resolve_channel(ch)
        try:
            chat = await client.get_chat(resolved)
            member = await client.get_chat_member(chat.id, user_id)
            if member.status not in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
                missing.append(chat)
        except UserNotParticipant:
            try:
                chat = await client.get_chat(resolved)
                missing.append(chat)
            except Exception:
                missing.append(type('Chat', (), {'id': ch, 'title': 'Required Channel', 'username': None})())
        except Exception as e:
            logger.warning(f"Error checking membership for {ch}: {e}")
            continue

    return missing

async def is_subscribed(client, user_id):
    """Convenience wrapper to check if user is subscribed to all required channels."""
    missing = await check_membership(client, user_id)
    return len(missing) == 0
