import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from bot.utils.ui import safe_answer, show_loading_sticker
from bot.database.mongo import db
from bot.services.users import upsert_user
from bot.utils.logger import logger, track_performance

logger = logging.getLogger(__name__)

async def trending_cmd(client: Client, message: Message):
    """Display top 10 most viewed shows."""
    loader = await show_loading_sticker(client, message.chat.id)
    try:
        await upsert_user(client, message.from_user, message.chat)
        
        # Sort by views desc from 'shows' collection (assuming stats are there)
        # In monolith it was stats_collection, let's check our mongo setup
        # If views are in shows collection:
        # Fetch top viewed from stats_collection (db.stats)
        top_shows = await db.stats.find().sort("views", -1).limit(10).to_list(10)
        
        if not top_shows:
            return await message.reply("📈 Trending data is building... Check back later!")
            
        me = await client.get_me()
        bot_username = me.username
        
        msg = "📈 **Top 10 Trending Shows**"
        buttons = []
        for idx, item in enumerate(top_shows, 1):
            name = item.get("show_name", "Unknown")
            cat = item.get("category", "N/A")
            views = item.get("views", 0)
            slug = item.get("show_slug", "")
            
            cat_slug = cat.lower().replace(" ", "_")
            btn_text = f"🎬 {idx}. {name} (👁 {views})"
            buttons.append([InlineKeyboardButton(btn_text, url=f"https://t.me/{bot_username}?start={cat_slug}___f{slug}")])
            
        if loader: await loader.delete()
        await message.reply(msg, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        if loader: await loader.delete()
        logger.exception(f"trending_cmd error: {e}")
        await message.reply("❌ Error loading trending shows.")

async def top_favorites_cmd(client: Client, message: Message):
    """Display top 10 most favorited shows."""
    loader = await show_loading_sticker(client, message.chat.id)
    try:
        await upsert_user(client, message.from_user, message.chat)
        
        pipeline = [
            {"$group": {
                "_id": {"show_name": "$show_name", "category": "$category", "show_slug": "$show_slug"},
                "count": {"$sum": 1}
            }},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        
        cursor = db.favorites.aggregate(pipeline)
        top_shows = await cursor.to_list(10)
        
        if not top_shows:
            return await message.reply("📉 No favorites data yet.")
            
        me = await client.get_me()
        bot_username = me.username
        
        msg = "⭐ **Top 10 Most Favorited**"
        buttons = []
        for idx, item in enumerate(top_shows, 1):
            info = item["_id"]
            count = item["count"]
            name = info.get("show_name", "Unknown")
            cat = info.get("category", "N/A")
            slug = info.get("show_slug", "")
            
            cat_slug = cat.lower().replace(" ", "_")
            btn_text = f"🎬 {idx}. {name} (⭐ {count})"
            buttons.append([InlineKeyboardButton(btn_text, url=f"https://t.me/{bot_username}?start={cat_slug}___f{slug}")])
            
        if loader: await loader.delete()
        await message.reply(msg, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        if loader: await loader.delete()
        logger.exception(f"top_favorites_cmd error: {e}")
        await message.reply("❌ Error loading top favorites.")

@track_performance("trending_cb")
async def trending_cb(client: Client, callback_query: CallbackQuery):
    await safe_answer(callback_query)
    callback_query.message.from_user = callback_query.from_user
    await trending_cmd(client, callback_query.message)

def register_trending_handlers(app: Client):
    app.on_message(filters.command(["trending", "top10", "popular"]) & filters.private)(trending_cmd)
    app.on_message(filters.command("fav") & filters.private)(top_favorites_cmd)
    app.on_callback_query(filters.regex("^trending$"))(trending_cb)
