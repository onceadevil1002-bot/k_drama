import asyncio
import logging
from pyrogram import Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.config import ADMIN_IDS

import collections
import time

from bot.utils.logger import logger, track_performance

class RateLimiter:
    """Rate limiter to prevent FloodWait errors."""
    def __init__(self, max_per_second=10):
        self.max_per_second = max_per_second
        self.timestamps = collections.deque()
        
    async def acquire(self):
        """Wait if rate limit would be exceeded."""
        now = time.time()
        
        # Remove timestamps older than 1 second
        while self.timestamps and self.timestamps[0] < now - 1:
            self.timestamps.popleft()
        
        # Check if we need to wait
        if len(self.timestamps) >= self.max_per_second:
            sleep_time = 1 - (now - self.timestamps[0])
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
                now = time.time()
        
        self.timestamps.append(now)

# Global rate limiter for broadcasts
notification_limiter = RateLimiter(max_per_second=8)

from bot.utils.ids import make_id

async def main_keyboard():
    """Build the main category keyboard with hashed IDs."""
    cats = [
        ("🎙️ K-Hindi", "K-Hindi"),
        ("🎌 Japanese Drama", "Japanese Drama"),
        ("🏮 CT Drama", "CT Drama"),
        ("🌍 Global", "Global"),
        ("🇵🇰 Pakistan", "Pakistan"),
        ("🎨 Anime", "Anime"),
        ("🎌 K-Original", "K-Original")
    ]
    
    buttons = []
    for emoji_title, cat_name in cats:
        cat_id = await make_id(cat_name)
        buttons.append([InlineKeyboardButton(emoji_title, callback_data=f"cat|{cat_id}")])
        
    buttons.append([InlineKeyboardButton("🔍 Search", switch_inline_query_current_chat="")])
    buttons.append([
        InlineKeyboardButton("🔥 Trending", callback_data="trending"),
        InlineKeyboardButton("⭐ Favorites", callback_data="my_favorites")
    ])
    buttons.append([InlineKeyboardButton("⚠️ Report Issue", callback_data="report")])
    
    return InlineKeyboardMarkup(buttons)


async def reply_with_ui(message: Message, text: str, reply_markup=None):
    """Reply with a UI message and optional keyboard markup."""
    try:
        return await message.reply(text, reply_markup=reply_markup)
    except Exception as e:
        logger.debug(f"reply_with_ui failed: {e}")
        # fallback: send directly to chat id if message has chat
        try:
            if hasattr(message, 'chat') and hasattr(message.chat, 'id'):
                return await message._client.send_message(message.chat.id, text, reply_markup=reply_markup)
        except Exception:
            pass


def back_button(callback_data="back_to_main"):
    """Standard back button."""
    return [InlineKeyboardButton("🔙 Back", callback_data=callback_data)]

@track_performance("auto_delete_message")
async def auto_delete_message(message: Message, delay: int = 180):
    """
    Auto-delete a message ONLY for normal users.
    Never deletes admin chats, or important menu messages.
    """
    try:
        user_id = message.chat.id

        # 1) DO NOT delete anything for admins
        if user_id in ADMIN_IDS:
            return

        # 2) Only act in private chats
        if message.chat.type.name != "PRIVATE":
            return

        # 3) Messages we should NEVER delete (menus, reports)
        keep_keywords = [
            "Choose a category",
            "📺 Show:",
            "📂 Shows",
            "Episode",
            "Report submitted",
            "User Report",
            "⚠️ Report Issue",
        ]

        text = message.text or message.caption or ""
        if text and any(k in text for k in keep_keywords):
            return  # skip deleting important menu messages

        # 4) Wait and delete
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except Exception as del_err:
            logger.debug(f"auto_delete_message: delete failed: {del_err}")

    except Exception as e:
        logger.debug(f"auto_delete_message error: {e}")

# Loading sticker logic moved below for consistency

async def safe_answer(callback_query, text="", show_alert=False):
    """Answer callback query safely."""
    try:
        await callback_query.answer(text, show_alert=show_alert)
    except Exception:
        pass

from bot.database.mongo import db

async def show_loading_sticker(client: Client, chat_id: int):
    """Sends a thinking sticker (or message fallback) and returns its message object."""
    try:
        config = await db.config.find_one({"_id": "settings"})
        sticker_id = config.get("loading_sticker") if config else None
        
        if sticker_id:
            try:
                return await client.send_sticker(chat_id, sticker=sticker_id)
            except Exception:
                pass # Fallback
                
        return await client.send_message(chat_id, "⏳ **Please wait...**")
    except Exception as e:
        logger.debug(f"Failed to send loading message: {e}")
        return None

async def update_media_or_text(message: Message, text: str, reply_markup=None):
    """
    Intelligently updates a message regardless of whether it's a photo or text.
    Ensures posters persist by editing captions instead of replacing messages.
    """
    try:
        if message.photo:
            await message.edit_caption(caption=text, reply_markup=reply_markup)
        else:
            await message.edit_text(text=text, reply_markup=reply_markup)
    except Exception as e:
        logger.debug(f"update_media_or_text: primary edit failed ({e}), trying reply fallback")
        # Fallback if editing fails (e.g. caption too long or message type mismatch)
        try:
            await message.reply(text, reply_markup=reply_markup)
            await message.delete()
        except Exception as fallback_err:
            logger.debug(f"update_media_or_text: fallback reply also failed: {fallback_err}")

def normalize_season(season_text: str) -> str:
    """Normalize season text (e.g., 'S1', 'Season 1') to plain number '1'."""
    import re
    s = str(season_text).strip().lower()
    s = re.sub(r'^season\s*', '', s)
    s = re.sub(r'^s', '', s)
    s = re.sub(r'[^0-9]', '', s).strip()
    return s if s else "1"
