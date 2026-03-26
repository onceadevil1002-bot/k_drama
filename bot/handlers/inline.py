from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton
from bot.services.search import search_drama
from bot.utils.ids import normalize_show_slug
import asyncio
from bot.utils.logger import logger, track_performance
from bot.utils.behavior import track_behavior

@track_performance("inline_search_handler")
async def inline_search_handler(client: Client, inline_query: InlineQuery):
    """Universal inline search handler."""
    query = (inline_query.query or "").strip()
    if not query:
        return await inline_query.answer([], cache_time=5, is_personal=True)
    
    try:
        results = await search_drama(query, limit=20)
        if not results:
            return await inline_query.answer([
                InlineQueryResultArticle(
                    id="nores",
                    title="❌ No results found",
                    description="Try different keywords or correct spelling",
                    input_message_content=InputTextMessageContent(
                        "No dramas found for your search."
                    ),
                )
            ], cache_time=5, is_personal=True)

        articles = []
        bot_username = (await client.get_me()).username
        
        for idx, doc in enumerate(results):
            title = doc.get("show_name", "").replace("_", " ")
            category = doc.get("category", "KDrama")
            slug = normalize_show_slug(title)
            
            # Build deep-link
            safe_cat = category.replace(" ", "_").lower()
            url = f"https://t.me/{bot_username}?start={safe_cat}__{slug}"
            
            articles.append(InlineQueryResultArticle(
                id=str(idx),
                title=title,
                description=f"{category}",
                input_message_content=InputTextMessageContent(
                    f"🎬 <b>{title}</b>\n📂 {category}\n\nTap below to open details 👇",
                    parse_mode=ParseMode.HTML
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📺 Open in Bot", url=url)]
                ])
            ))
        
        asyncio.create_task(track_behavior(
            inline_query.from_user.id, 'search',
            {'query': query, 'results_count': len(results), 'source': 'inline'}
        ))
        await inline_query.answer(articles, cache_time=10, is_personal=False)
    except Exception as e:
        logger.exception(f"Inline search error: {e}")
        await inline_query.answer([], cache_time=5, is_personal=True)

def register_inline_handlers(app: Client):
    app.on_inline_query()(inline_search_handler)
