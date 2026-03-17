import logging
import time
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from bot.utils.ui import safe_answer, main_keyboard
from bot.utils.ids import make_id, resolve_id
from bot.services.shows import get_cached_data
from bot.services.sessions import create_group_session, get_group_session

logger = logging.getLogger(__name__)

async def open_here_handler(client: Client, callback_query: CallbackQuery):
    """Handle 'Open Here' button in groups."""
    try:
        chat_id = callback_query.message.chat.id
        await create_group_session(chat_id)
        
        await callback_query.message.edit_text(
            "🎬 **Group Viewing Mode Activated!**\n\nChoose a category to browse in this chat:",
            reply_markup=await main_keyboard()
        )
        await safe_answer(callback_query)
    except Exception as e:
        logger.exception(f"open_here_handler error: {e}")
        await safe_answer(callback_query, "Error activating group mode.", show_alert=True)

async def group_season_handler(client: Client, callback_query: CallbackQuery):
    """Season browser for groups (no deep links needed as it stays in the group)."""
    # This is similar to normal season_handler but can have group-specific UI if needed
    from bot.handlers.callbacks import season_handler
    await season_handler(client, callback_query)

def register_group_handlers(app: Client):
    app.on_callback_query(filters.regex(r"^open_here$"))(open_here_handler)
    # Most group interactions use same handlers as private ones
    # but we can add specific filters if needed.
