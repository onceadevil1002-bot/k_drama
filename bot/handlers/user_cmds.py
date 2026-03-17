import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from bot.config import ADMIN_IDS, MAIN_CHANNEL_LINK, SECOND_CHANNEL_LINK
from bot.utils.ui import reply_with_ui, main_keyboard, safe_answer
from bot.services.verification import is_subscribed, update_verification
from bot.services.favorites import get_user_favorites
from bot.services.updates import get_recent_updates, format_time_ago

logger = logging.getLogger(__name__)

async def favorites_cmd(client: Client, message: Message):
    if not await is_subscribed(client, message.from_user.id):
        return await message.reply("Please join our channels first!")
        
    favs = await get_user_favorites(message.from_user.id)
    if not favs:
        return await message.reply("⭐ Your favorites list is empty.")
        
    btn_rows = []
    for f in favs:
        # Use bot username if available, else placeholder
        me = await client.get_me()
        bot_username = me.username
        category_slug = f['category'].lower().replace(" ", "_")
        btn_rows.append([InlineKeyboardButton(f"{f['show_name']} ({f['category']})", url=f"https://t.me/{bot_username}?start={category_slug}__{f['show_slug']}")])
        
    await message.reply("⭐ **Your Favorites:**", reply_markup=InlineKeyboardMarkup(btn_rows))

async def recent_updates_cmd(client: Client, message: Message):
    updates = await get_recent_updates(10)
    if not updates:
        return await message.reply("📭 No recent updates found.")
        
    text = "🔥 **Recent Updates:**\n\n"
    for idx, u in enumerate(updates, 1):
        text += f"{idx}. **{u['show_name']}** (S{u['season']} E{u['episode_num']})\n   _{format_time_ago(u['timestamp'])}_\n\n"
        
    await message.reply(text)

from bot.services.favorites import is_favorited
from bot.services.search import search_drama

from bot.services.shows import get_cached_data
from bot.services.users import upsert_user, get_watch_history
from bot.services.requests import submit_request
from bot.utils.ui import show_loading_sticker
from bot.utils.ids import decode_show_slug, make_id, normalize_show_slug
from bot.utils.logger import track_performance

@track_performance("search_drama")
async def search_cmd(client: Client, message: Message):
    """Handle /search command. Now available globally in groups and private."""
    query = ""
    # Support /search <query> inline
    if len(message.command) > 1:
        query = message.text.split(" ", 1)[1].strip()
        
    loader = await show_loading_sticker(client, message.chat.id)
    
    # Track user
    await upsert_user(client, message.from_user, message.chat)
    
    if not query:
        if loader: await loader.delete()
        return await message.reply("🔍 Usage: `/search drama_name`")
    
    try:
        results = await search_drama(query, limit=10)
        if loader: await loader.delete()
        
        if not results:
            return await message.reply("❌ No results found. Try different keywords.")
            
        me = await client.get_me()
        bot_username = me.username
        
        text = f"🔍 **Search Results for:** `{query}`\n\n"
        buttons = []
        for doc in results:
            title = doc.get("show_name", "Unknown")
            cat = doc.get("category", "KDrama")
            slug = normalize_show_slug(title)
            cat_slug = cat.lower().replace(" ", "_")
            
            url = f"https://t.me/{bot_username}?start={cat_slug}__{slug}"
            buttons.append([InlineKeyboardButton(f"▶ {title} ({cat})", url=url)])
            
        await message.reply(text, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        if loader: await loader.delete()
        logger.exception(f"search_cmd error: {e}")
        await message.reply("❌ Error performing search.")

async def start_cmd(client: Client, message: Message):
    """Handle /start command with deep linking support and loading sticker."""
    # Show loading sticker for premium UX
    loader = await show_loading_sticker(client, message.chat.id)
    
    # Track user
    await upsert_user(client, message.from_user, message.chat)
    
    # Check subscription
    if not await is_subscribed(client, message.from_user.id):
        keyboard = [
            [InlineKeyboardButton("Join Channel 1", url=MAIN_CHANNEL_LINK)],
            [InlineKeyboardButton("Join Channel 2", url=SECOND_CHANNEL_LINK)],
            [InlineKeyboardButton("I Joined Both", callback_data="joined")]
        ]
        if loader: await loader.delete()
        return await message.reply(
            "🎬 **Welcome to K-Drama Bot!**\n\nPlease join our channels to use the bot.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # Decode deep link parameter if any
    args = message.text.split()
    slug = args[1] if len(args) > 1 else None
    
    if slug and "__" in slug:
        try:
            category_part, show_part = slug.split("__", 1)
            category_key = category_part.replace("_", " ").lower().strip()

            data = await get_cached_data()
            matched_category = None
            for c in data:
                if c.lower().strip() == category_key:
                    matched_category = c
                    break

            if matched_category:
                decoded_show_name = decode_show_slug(show_part).strip()
                matched_show = None

                # Exact match
                for db_show in data[matched_category]:
                    if db_show.lower() == decoded_show_name.lower():
                        matched_show = db_show
                        break

                # Partial match fallback (parity with monolith)
                if not matched_show:
                    for db_show in data[matched_category]:
                        if decoded_show_name.lower() in db_show.lower():
                            matched_show = db_show
                            break

                if matched_show:
                    shows = sorted(data[matched_category].keys())
                    if matched_show in shows:
                        shows.remove(matched_show)
                    shows.insert(0, matched_show)  # Pin to top

                    page_shows = shows[:10]
                    total_pages = (len(shows) + 9) // 10

                    cat_id = await make_id(matched_category)
                    buttons = []
                    for s in page_shows:
                        emoji = "⭐" if s == matched_show else "🎬"
                        show_id = await make_id(s)
                        buttons.append([InlineKeyboardButton(f"{emoji} {s}", callback_data=f"show|{cat_id}|{show_id}")])

                    if total_pages > 1:
                        star_id = await make_id(matched_show)
                        buttons.append([
                            InlineKeyboardButton(f"📄 1/{total_pages}", callback_data="noop"),
                            InlineKeyboardButton("➡️ Next", callback_data=f"page|{cat_id}|2|{star_id}")
                        ])

                    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])

                    text = f"📂 **{matched_category}**\n\nSelect a show:"
                    if loader:
                        await loader.delete()
                    return await message.reply(text, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            logger.exception(f"start_cmd deep-link decode error: {e}")

    if loader:
        await loader.delete()
    await reply_with_ui(message, "🎬 **Welcome back!**\n\nChoose a category to browse:", reply_markup=await main_keyboard())

async def request_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply("Usage: /request <drama name>")
    drama_name = " ".join(message.command[1:])
    await submit_request(message.from_user.id, drama_name)
    await message.reply(f"✅ Your request for **{drama_name}** has been submitted!")

    # Duplicate search_cmd removed to respect the primary global search_cmd at line 51

async def history_cmd(client: Client, message: Message):
    loader = await show_loading_sticker(client, message.chat.id)
    try:
        history = await get_watch_history(message.from_user.id)
        if loader: await loader.delete()
        
        if not history:
            return await message.reply("📜 Your watch history is empty.")
        text = "📜 **Your Watch History:**\n\n"
        for item in history:
            text += f"• {item}\n"
        await message.reply(text)
    except Exception as e:
        if loader: await loader.delete()
        logger.exception(f"history_cmd error: {e}")
        await message.reply("❌ Error loading history.")

@track_performance("my_favorites_cb")
async def my_favorites_cb(client: Client, callback_query: CallbackQuery):
    await safe_answer(callback_query)
    callback_query.message.from_user = callback_query.from_user
    await favorites_cmd(client, callback_query.message)

@track_performance("global_metadata_harvester")
async def silent_metadata_harvester(client: Client, message: Message):
    """Silently harvests user data on every witnessed message globally without interrupting flow."""
    if getattr(message, "from_user", None):
        await upsert_user(client, message.from_user, message.chat)

def register_user_handlers(app: Client):
    # Harvester bound to group=-2, ensuring it runs passively first before main command handlers
    app.on_message(filters.incoming, group=-2)(silent_metadata_harvester)
    
    app.on_message(filters.command("start") & filters.private)(start_cmd)
    app.on_message(filters.command("help") & filters.private)(lambda c, m: m.reply("📚 **K-Drama Bot Help**\n\n/start - Browse categories\n/search - Find dramas\n/favorites - Your list\n/recent_updates - New content\n/request - Request a drama\n/history - Watch history"))
    app.on_message(filters.command("favorites") & filters.private)(favorites_cmd)
    app.on_callback_query(filters.regex("^my_favorites$"))(my_favorites_cb)
    app.on_message(filters.command("recent_updates") & filters.private)(recent_updates_cmd)
    app.on_message(filters.command("request") & filters.private)(request_cmd)
    app.on_message(filters.command("history") & filters.private)(history_cmd)
    app.on_message(filters.command("search"))(search_cmd)
