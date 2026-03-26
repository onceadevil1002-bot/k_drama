import logging
from pyrogram import Client, filters
from pyrogram.types import ChatMemberUpdated
from pyrogram.enums import ChatMemberStatus
from bot.config import ADMIN_IDS
from bot.services.verification import (
    record_join, record_leave,
    is_warned, set_warned,
    ban_user, is_banned,
    get_resolved_ids
)

logger = logging.getLogger(__name__)

WARN_AT = 3
BAN_AT  = 4


def _extract_user_info(user):
    username  = user.username or ""
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return username, full_name


def _contact_admin_button(bot_username: str):
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📩 Contact Admin",
            url=f"https://t.me/{bot_username}?start=support"
        )
    ]])


async def channel_member_update_handler(client: Client, update: ChatMemberUpdated):
    """
    Fires on every chat member update.
    We only act on required channels (matched by numeric ID).
    """
    try:
        event_channel_id = update.chat.id   # always numeric from Telegram

        # Only care about our required channels
        if event_channel_id not in get_resolved_ids():
            return

        user = update.new_chat_member.user if update.new_chat_member else None
        if not user or user.is_bot:
            return

        user_id = user.id
        # Skip admins
        if user_id in ADMIN_IDS:
            return

        username, full_name = _extract_user_info(user)
        new_status = update.new_chat_member.status if update.new_chat_member else None

        joined = new_status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER
        }
        left = new_status in {
            ChatMemberStatus.LEFT,
            ChatMemberStatus.BANNED
        }

        if joined:
            await record_join(user_id, event_channel_id, username, full_name)
            logger.info(f"User {user_id} ({full_name}) joined {event_channel_id}")

        elif left:
            doc = await record_leave(user_id, event_channel_id, username, full_name)
            leave_count = doc.get("leave_count", 0) if doc else 0
            logger.info(f"User {user_id} ({full_name}) left {event_channel_id} (leave_count={leave_count})")

            # Already banned — nothing more to do
            if await is_banned(user_id):
                return

            if leave_count >= BAN_AT:
                await ban_user(user_id, username, full_name, leave_count)
                try:
                    bot_me = await client.get_me()
                    await client.send_message(
                        user_id,
                        "⛔ **You have been banned from using this bot.**\n\n"
                        "You repeatedly joined and left the required channels.\n\n"
                        "To appeal, contact the admin:",
                        reply_markup=_contact_admin_button(bot_me.username)
                    )
                except Exception:
                    pass

            elif leave_count >= WARN_AT and not await is_warned(user_id):
                await set_warned(user_id)
                try:
                    await client.send_message(
                        user_id,
                        "⚠️ **Warning!**\n\n"
                        "You have repeatedly joined and left the required channels.\n"
                        "If you do this **one more time**, you will be permanently "
                        "banned from using this bot."
                    )
                except Exception:
                    pass

    except Exception as e:
        logger.exception(f"channel_member_update_handler error: {e}")


def register_channel_handlers(app: Client):
    app.on_chat_member_updated()(channel_member_update_handler)
