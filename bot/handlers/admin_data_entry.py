import logging
import asyncio
import re
import time
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait

from bot.config import ADMIN_IDS, STORAGE_CHANNEL_ID
from bot.database.mongo import db
from bot.services.shows import get_cached_data
from bot.utils.ui import safe_answer, normalize_season
from bot.utils.ids import normalize_show_slug, make_id, resolve_id
from bot.utils.logger import logger, track_performance
from bot.utils.backup import trigger_backup

# --- CONFIG & MAPS ---
IMPORT_CATEGORY_MAP = {
    "import_hindi": "Hindi Dubbed",
    "import_regional": "Regional",
    "import_jap": "Japanese Drama",
    "import_c": "C Drama",
    "import_arb": "Arabic",
    "import_pak": "Pakistan",
    "import_anime": "Anime",
}

POSTER_CATEGORY_COMMANDS = {
    "add_poster": "Hindi Dubbed",
    "add_poster_regional": "Regional",
    "add_poster_jap": "Japanese Drama",
    "add_poster_c": "C Drama",
    "add_poster_arb": "Arabic",
    "add_poster_pak": "Pakistan",
    "add_poster_anime": "Anime",
}

DELETE_CATEGORY_COMMANDS = {
    "delete": "Hindi Dubbed",
    "delete_regional": "Regional",
    "delete_jap": "Japanese Drama",
    "delete_c": "C Drama",
    "delete_arb": "Arabic",
    "delete_pak": "Pakistan",
    "delete_anime": "Anime",
}

CATEGORY_ALIASES = {
    "hindi": "Hindi Dubbed",
    "jap": "Japanese Drama",
    "japanese": "Japanese Drama",
    "c": "C Drama",
    "chinese": "C Drama",
    "arb": "Arabic",
    "arabic": "Arabic",
    "regional": "Regional",
    "pak": "Pakistan",
    "pakistan": "Pakistan",
    "anime": "Anime"
}

# State management
import_state = {} # user_id -> state_dict
poster_upload_state = {} # user_id -> state_dict

admin_filter = filters.user(ADMIN_IDS)

# --- IMPORT HANDLERS ---

@track_performance("import_handler")
async def import_command_handler(client: Client, message: Message):
    """Initiate import mode for an episode."""
    cmd = message.command[0]
    category = IMPORT_CATEGORY_MAP.get(cmd)
    if not category: return

    try:
        args_text = message.text.split(" ", 1)[1].strip()
    except IndexError:
        return await message.reply(
            f"**Usage:** `/{cmd} Show_Name S1 E3 720p`\n"
            f"💡 Use quotes for names with spaces if needed, or underscores."
        )

    # Parsing (Same logic as monolith)
    # 1. Quoted: "Show Name" S1 E3 720p
    match = re.match(r'"([^"]+)"\s+[Ss](\d+)\s+[Ee](\d+)\s+(\d+)p?', args_text)
    if not match:
        # 2. Unquoted: Show_Name S1 E3 720p or Show Name S1 E3 720p
        match = re.match(r'(.+?)\s+[Ss](\d+)\s+[Ee](\d+)\s+(\d+)p?', args_text)

    if not match:
        return await message.reply(
            f"❌ **Invalid format**\n\n"
            f"**Usage:** `/{cmd} Show_Name S1 E1 720p`\n"
            f"**Example:** `/{cmd} \"My Drama\" S1 E1 720p`"
        )

    show_name_input = match.group(1).strip()
    season_str = match.group(2)
    episode_num = int(match.group(3))
    quality = match.group(4) + "p" if not match.group(4).endswith("p") else match.group(4)

    # Validate Show and Category
    data = await get_cached_data()
    if category not in data:
        # Try finding by alias
        alias_cat = CATEGORY_ALIASES.get(category.lower())
        if alias_cat: category = alias_cat
        else: return await message.reply(f"❌ Category {category} not found.")

    # Find show (flexible matching as in monolith)
    actual_show_name = None
    search_terms = {
        show_name_input.lower(), 
        show_name_input.replace("_", " ").lower(), 
        show_name_input.replace(" ", "_").lower()
    }
    
    for s_name in data[category]:
        if s_name.lower() in search_terms:
            actual_show_name = s_name
            break
    
    if not actual_show_name:
        return await message.reply(
            f"❌ Show '{show_name_input}' not found in {category}.\n"
            f"💡 **Tip:** Use `/add` to create it first."
        )

    season_key = normalize_season(season_str)
    
    import_state[message.from_user.id] = {
        "category": category,
        "show": actual_show_name,
        "season": season_key,
        "episode_index": episode_num - 1,
        "quality": quality,
        "timestamp": time.time()
    }

    await message.reply(
        f"✅ **Import Mode [Active]**\n\n"
        f"📺 Show: **{actual_show_name}**\n"
        f"📂 Category: **{category}**\n"
        f"🎬 Episode: **S{season_key} E{episode_num}**\n"
        f"🎞 Quality: **{quality}**\n\n"
        f"📤 Send the Video/File/Link now."
    )

async def handle_import_receive(client: Client, message: Message):
    """Receive and save the imported episode data."""
    user_id = message.from_user.id
    state = import_state.get(user_id)
    if not state or (message.text and message.text.startswith("/")): return

    # Cleanup state after or on error
    def cleanup(): import_state.pop(user_id, None)

    proc = await message.reply("⏳ Processing import...")
    file_type = "video"
    file_id = None
    if message.video: 
        file_id = message.video.file_id
    elif message.document: 
        file_id = message.document.file_id
        file_type = "document"
    elif message.text and re.match(r'https?://\S+', message.text): 
        file_id = message.text
        file_type = "link"
    
    if not file_id:
        await proc.edit("❌ Please send a video, document, or valid link.")
        return

    try:
        category = state["category"]
        show_name = state["show"]
        season = state["season"]
        ep_idx = state["episode_index"]
        quality = state["quality"]

        # Update MongoDB
        # We store multi-quality episodes as list of dicts at index
        # [ { "qualities": { "720p": {"type": "...", "content": "..."} } }, ... ]
        
        show_doc = await db.shows.find_one({"category": category, "show_name": show_name})
        if not show_doc:
            await proc.edit("❌ Show data lost during process.")
            cleanup()
            return

        episodes = show_doc.get("episodes", {}).get(season, [])
        # Ensure list is long enough
        while len(episodes) <= ep_idx:
            episodes.append({})
        
        # Add specific quality
        current_ep = episodes[ep_idx]
        if not isinstance(current_ep, dict):
            # Convert legacy single-file ep to multi-quality dict
            current_ep = {"qualities": {"default": {"type": "video", "content": current_ep}}} if current_ep else {"qualities": {}}
            
        if "qualities" not in current_ep:
            if "type" in current_ep:
                current_ep = {"qualities": {"default": current_ep}}
            else:
                migrated_quals = {}
                for q, v in current_ep.items():
                    if isinstance(v, str):
                        migrated_quals[q] = {"type": "link" if v.startswith("http") else "video", "content": v}
                    else:
                        migrated_quals[q] = v
                current_ep = {"qualities": migrated_quals}
                
        current_ep["qualities"][quality] = {"type": file_type, "content": file_id}
        episodes[ep_idx] = current_ep

        await db.shows.update_one(
            {"category": category, "show_name": show_name},
            {"$set": {f"episodes.{season}": episodes}}
        )

        # Clear cache
        from bot.utils.cache import show_cache
        show_cache.clear()

        await proc.edit(f"✅ Success! Updated **S{season} E{ep_idx+1}** [{quality}] for **{show_name}**.")
        asyncio.create_task(trigger_backup())
        cleanup()

    except Exception as e:
        logger.exception(f"Import process error: {e}")
        await proc.edit(f"❌ Error: {e}")
        cleanup()

# --- POSTER HANDLERS ---

@track_performance("add_poster_handler")
async def add_poster_command(client: Client, message: Message):
    """Initiate poster upload mode."""
    cmd = message.command[0]
    category = POSTER_CATEGORY_COMMANDS.get(cmd, "Hindi Dubbed")
    
    try:
        show_input = message.text.split(" ", 1)[1].strip()
    except IndexError:
        return await message.reply(f"**Usage:** `/{cmd} <Show Name>`")

    data = await get_cached_data()
    actual_show = None
    for s in data.get(category, {}):
        if s.lower() == show_input.lower() or s.replace("_", " ").lower() == show_input.lower():
            actual_show = s
            break
    
    if not actual_show:
        return await message.reply(f"❌ Show '{show_input}' not found in {category}.")

    poster_upload_state[message.from_user.id] = {
        "category": category,
        "show": actual_show
    }
    await message.reply(f"🖼 **Poster Mode Active** for **{actual_show}**\n\nReply with a Photo.")

async def handle_poster_receive(client: Client, message: Message):
    """Save the new poster."""
    user_id = message.from_user.id
    state = poster_upload_state.get(user_id)
    if not state or not message.photo: return

    def cleanup(): poster_upload_state.pop(user_id, None)
    
    proc = await message.reply("⏳ Uploading poster...")
    try:
        photo_id = message.photo.file_id
        
        # Safe update to handle legacy string posters
        show_name = state["show"]
        category = state["category"]
        
        show_doc = await db.shows.find_one({"category": category, "show_name": show_name})
        if not show_doc:
            await proc.edit("❌ Show data lost.")
            return cleanup()
            
        current_posters = show_doc.get("poster", [])
        if isinstance(current_posters, str):
            current_posters = [current_posters]
        
        current_posters.append(photo_id)
        
        await db.shows.update_one(
            {"_id": show_doc["_id"]},
            {"$set": {"poster": current_posters}}
        )
        
        from bot.utils.cache import show_cache
        show_cache.clear()
        
        await proc.edit(f"✅ Poster added for **{state['show']}**!")
        cleanup()
    except Exception as e:
        logger.exception(f"handle_poster_receive error: {e}")
        await proc.edit(f"❌ Error: {e}")
        cleanup()

# --- GENERAL ADMIN ---

@track_performance("add_show_cmd")
async def add_show_cmd(client: Client, message: Message):
    """Add a new show and/or season with flexible parsing."""
    cmd = message.command[0]
    if len(message.command) < 2:
        return await message.reply(
            "**Usage:**\n"
            "• `/add Show Name` (Defaults to Hindi)\n"
            "• `/add Show Name 1` (Show + Season 1)\n"
            "• `/add Show Name > Category`\n"
            "• `/add Show Name > Category 1`"
        )

    args = message.text.split(" ", 1)[1].strip()
    category = "Hindi Dubbed"
    show_name = None
    season_number = None

    # Support /add_hindi variants
    cmd_cat = cmd.replace("add_", "")
    if cmd_cat in CATEGORY_ALIASES:
        category = CATEGORY_ALIASES[cmd_cat]

    if ">" in args:
        try:
            show_part, rest = args.split(">", 1)
            show_part = show_part.strip()
            rest_parts = rest.strip().split()

            show_name = show_part
            if rest_parts and rest_parts[-1].isdigit():
                season_number = rest_parts[-1]
                cat_input = " ".join(rest_parts[:-1]).lower()
                category = CATEGORY_ALIASES.get(cat_input, cat_input.title()) if cat_input else category
            else:
                cat_input = " ".join(rest_parts).lower()
                category = CATEGORY_ALIASES.get(cat_input, cat_input.title())
        except Exception:
            return await message.reply("❌ Invalid format. Use `/add Show Name > Category 1`")
    else:
        parts = args.rsplit(" ", 1)
        if parts[-1].isdigit():
            show_name = parts[0].strip()
            season_number = parts[1].strip()
        else:
            show_name = args.strip()

    try:
        # Check if show exists
        show_doc = await db.shows.find_one({"show_name": show_name, "category": category})
        
        if not show_doc:
            await db.shows.insert_one({
                "category": category,
                "show_name": show_name,
                "episodes": {},
                "poster": [],
                "created_at": datetime.now()
            })
            await message.reply(f"Added show: **{show_name}** under **{category}**")
            asyncio.create_task(trigger_backup())
        
        if season_number:
            season_key = normalize_season(season_number)
            # Add season if not exists
            res = await db.shows.update_one(
                {"show_name": show_name, "category": category, f"episodes.{season_key}": {"$exists": False}},
                {"$set": {f"episodes.{season_key}": []}}
            )
            if res.modified_count:
                await message.reply(f"Added **Season {season_key}** under **{show_name}**")
                asyncio.create_task(trigger_backup())
            else:
                await message.reply(f"ℹ️ Season {season_key} already exists for **{show_name}**.")

        # Clear cache
        from bot.utils.cache import show_cache
        show_cache.clear()
        
    except Exception as e:
        logger.exception(f"Add show error: {e}")
        await message.reply(f"❌ Error adding show: {e}")

@track_performance("delete_command_handler")
async def delete_command_handler(client: Client, message: Message):
    """Delete a show, season, episode, or quality."""
    cmd = message.command[0]
    category = DELETE_CATEGORY_COMMANDS.get(cmd, "Hindi Dubbed")

    try:
        args_text = message.text.split(" ", 1)[1].strip()
    except IndexError:
        return await message.reply(
            f"**Usage:**\n"
            f"• `/{cmd} Show Name` (Delete whole show)\n"
            f"• `/{cmd} Show Name 1` (Delete Season 1)\n"
            f"• `/{cmd} Show Name 1 2` (Delete S1 E2)\n"
            f"• `/{cmd} Show Name 1 2 720p` (Delete S1 E2 720p)"
        )

    parts = args_text.split()
    
    quality = None
    if parts[-1].lower() in ["480p", "720p", "1080p", "4k"]:
        quality = parts.pop().lower()
        
    episode_num = None
    if parts and parts[-1].isdigit():
        episode_num = int(parts.pop())
        
    season_number = None
    if parts and parts[-1].isdigit():
        season_number = parts.pop()
    elif parts and parts[-1].lower().startswith("s") and parts[-1][1:].isdigit():
        season_number = parts.pop()[1:]
    
    show_name_input = " ".join(parts).strip()
    if episode_num is not None and season_number is None and quality is None:
        season_number = str(episode_num)
        episode_num = None

    data = await get_cached_data()
    actual_show_name = None
    for s_name in data.get(category, {}):
        if s_name.lower() == show_name_input.lower() or s_name.replace("_", " ").lower() == show_name_input.lower():
            actual_show_name = s_name
            break
            
    if not actual_show_name:
        return await message.reply(f"❌ Show '{show_name_input}' not found in {category}.")

    # Logic to delete
    doc = await db.shows.find_one({"category": category, "show_name": actual_show_name})
    if not doc:
        return await message.reply(f"❌ Show missing in DB.")

    if quality and episode_num and season_number:
        season_key = str(int(season_number))
        ep_idx = episode_num - 1
        episodes = doc.get("episodes", {}).get(season_key, [])
        if ep_idx < len(episodes):
            ep = episodes[ep_idx]
            if isinstance(ep, dict) and "qualities" in ep and quality in ep["qualities"]:
                del ep["qualities"][quality]
                episodes[ep_idx] = ep
                await db.shows.update_one({"_id": doc["_id"]}, {"$set": {f"episodes.{season_key}": episodes}})
                from bot.utils.cache import show_cache; show_cache.clear()
                asyncio.create_task(trigger_backup())
                return await message.reply(f"✅ Deleted **{quality}** from **S{season_key} E{episode_num}** of **{actual_show_name}**.")
        return await message.reply("❌ Quality/Episode not found.")

    if episode_num and season_number:
        season_key = str(int(season_number))
        ep_idx = episode_num - 1
        episodes = doc.get("episodes", {}).get(season_key, [])
        if ep_idx < len(episodes):
            episodes.pop(ep_idx)
            await db.shows.update_one({"_id": doc["_id"]}, {"$set": {f"episodes.{season_key}": episodes}})
            from bot.utils.cache import show_cache; show_cache.clear()
            asyncio.create_task(trigger_backup())
            return await message.reply(f"✅ Deleted **S{season_key} E{episode_num}** from **{actual_show_name}**.")
        return await message.reply("❌ Episode not found.")
        
    if season_number:
        season_key = str(int(season_number))
        if season_key in doc.get("episodes", {}):
            await db.shows.update_one({"_id": doc["_id"]}, {"$unset": {f"episodes.{season_key}": ""}})
            from bot.utils.cache import show_cache; show_cache.clear()
            asyncio.create_task(trigger_backup())
            return await message.reply(f"✅ Deleted **Season {season_key}** from **{actual_show_name}**.")
        return await message.reply("❌ Season not found.")

    res = await db.shows.delete_one({"_id": doc["_id"]})
    if res.deleted_count:
        from bot.utils.cache import show_cache; show_cache.clear()
        asyncio.create_task(trigger_backup())
        return await message.reply(f"✅ Deleted entire show **{actual_show_name}** from {category}.")
    await message.reply("❌ Delete failed.")

@track_performance("get_link_cmd")
async def get_link_cmd(client: Client, message: Message):
    """Resolve a slug or ID to its full details/links."""
    if len(message.command) < 2:
        return await message.reply("Usage: `/get <slug|hash>`")
    
    query = message.command[1]
    # Try hash resolution first
    identity = await resolve_id(query)
    if identity != query:
        return await message.reply(f"🔍 **Resolved Hash:**\n`{identity}`")
    
    # Try slug search
    show = await db.shows.find_one({"show_name": {"$regex": f"^{query.replace('_', ' ')}$", "$options": "i"}})
    if show:
        import json
        details = f"📺 **{show['show_name']}**\n📂 Category: {show['category']}\n\n"
        details += f"Data: `{json.dumps(show.get('episodes', {}), indent=2)}`"
        if len(details) > 4000: details = details[:4000] + "..."
        return await message.reply(details)
    
    await message.reply("❌ Nothing found for that query.")

@track_performance("user_search_cmd")
async def user_search_cmd(client: Client, message: Message):
    """Advanced user search by ID, Username, or Name."""
    if len(message.command) < 2:
        return await message.reply("Usage: `/user_search <ID|@username|Name>`")
    
    query = message.text.split(" ", 1)[1].strip().replace("@", "")
    
    # Search Canonical Users
    filter_query = {
        "$or": [
            {"username": {"$regex": f"^{query}$", "$options": "i"}},
            {"full_name": {"$regex": query, "$options": "i"}},
            {"past_usernames": {"$regex": f"^{query}$", "$options": "i"}},
            {"past_names": {"$regex": query, "$options": "i"}}
        ]
    }
    if query.isdigit():
        filter_query["$or"].append({"user_id": int(query)})

    users = await db.userdb.find(filter_query).limit(10).to_list(10)
    if not users:
        return await message.reply("❌ No users found matching that query.")
    
    for u in users:
        is_premium = u.get("is_premium", False)
        status_str = "Active"
        uid = u['user_id']
        
        text = (
            f"👤 **USER MAX-PROFILE**\n"
            f"🆔 ID: `{uid}`\n"
            f"👤 First Name: {u.get('first_name', '')}\n"
            f"👤 Last Name: {u.get('last_name', '')}\n"
            f"📛 Username: @{u.get('username', 'None')}\n"
            f"📝 Full Name: {u.get('full_name', 'Unknown')}\n\n"
            f"💎 Premium: {'✅ Yes' if is_premium else '❌ No'}\n"
            f"🌐 Language: {u.get('language_code', 'Unknown')}\n"
            f"🏢 Known Groups: {len([c for c in u.get('chats', []) if str(c.get('type')) != 'ChatType.PRIVATE'])} groups\n"
            f"📊 Status: {status_str}\n"
            f"⏱ Last Online: {u.get('last_interaction', 'N/A')}\n"
        )
        
        btns = []
        
        # Row 1: Profile Pics
        pic_row = [InlineKeyboardButton("🖼 Current Pic", callback_data=f"mp_pic_curr|{uid}")]
        past_pics = u.get("profile_pic_history", [])
        for i, p in enumerate(past_pics[-3:], 1): # Show up to 3 individual past pic buttons
            pic_row.append(InlineKeyboardButton(f"🖼 Past {i}", callback_data=f"mp_pic_idx|{uid}|{len(past_pics)-i}"))
        btns.append(pic_row)
        
        # Row 2: Name History & Group List
        btns.append([
            InlineKeyboardButton("📜 Name History", callback_data=f"mp_names|{uid}"),
            InlineKeyboardButton("🏢 Group List", callback_data=f"mp_groups|{uid}")
        ])
        
        # Row 3: Last Seen & Watch History
        btns.append([
            InlineKeyboardButton("🕒 Last Seen Hist", callback_data=f"mp_lastseen|{uid}"),
            InlineKeyboardButton("🗂 Watch History", callback_data=f"user_history_{uid}")
        ])
        
        if u.get("profile_pic"):
            await client.send_photo(message.chat.id, u["profile_pic"], caption=text, reply_markup=InlineKeyboardMarkup(btns))
        else:
            await message.reply(text, reply_markup=InlineKeyboardMarkup(btns))

@track_performance("report_search_cmd")
async def report_search_cmd(client: Client, message: Message):
    """Search for reports by show or user."""
    if len(message.command) < 2:
        return await message.reply("Usage: `/report_search <Show_Name|User_ID>`")
    
    query = message.text.split(" ", 1)[1].strip()
    filter_q = {
        "$or": [
            {"report": {"$regex": query, "$options": "i"}},
            {"user.username": {"$regex": query, "$options": "i"}},
            {"user.full_name": {"$regex": query, "$options": "i"}}
        ]
    }
    if query.isdigit(): filter_q["$or"].append({"user.user_id": int(query)})
    
    reports = await db.reports.find(filter_q).sort("created_at", -1).limit(5).to_list(5)
    if not reports:
        return await message.reply("❌ No reports found.")
    
    for r in reports:
        await message.reply(
            f"🚩 **Report Search Result**\n"
            f"👤 User: {r['user']['full_name']} (@{r['user'].get('username')})\n"
            f"📄 Report: {r['report']}\n"
            f"⏰ Created: {r['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
            f"🚦 Status: {r['status']}"
        )

@track_performance("user_history_cb")
async def user_history_cb(client: Client, callback_query: CallbackQuery):
    """Handle the 'History' button from max-profile/search."""
    try:
        target_uid = int(callback_query.data.split("_")[2])
        from bot.services.users import get_watch_history
        history = await get_watch_history(target_uid)
        
        if not history:
            return await safe_answer(callback_query, "Watch history is empty.", show_alert=True)
            
        text = f"📜 **Watch History for User `{target_uid}`:**\n\n"
        for item in history:
            text += f"• {item}\n"
        await callback_query.message.reply(text)
        await safe_answer(callback_query)
    except Exception as e:
        logger.exception(f"user_history_cb error: {e}")
        await safe_answer(callback_query, "Error loading history.", show_alert=True)

@track_performance("max_profile_view_history")
async def mp_history_cb(client: Client, callback_query: CallbackQuery):
    """Handle Max-Profile dynamic button views."""
    data = callback_query.data.split("|")
    action = data[0]
    uid = int(data[1])
    
    u = await db.userdb.find_one({"user_id": uid})
    if not u:
        return await safe_answer(callback_query, "❌ User data not found.", show_alert=True)
        
    if action == "mp_names":
        msg = f"📜 **Name History for {uid}** (Recent 20)\n\n"
        has_data = False
        if u.get("username_history"):
            has_data = True
            msg += "**Usernames:**\n"
            for h in u["username_history"][-20:]:
                msg += f"• `{h.get('old', 'None')}` -> `{h.get('new', 'None')}` (On {h.get('changed_at')})\n"
            msg += "\n"
        if u.get("first_name_history"):
            has_data = True
            msg += "**First Names:**\n"
            for h in u["first_name_history"][-20:]:
                msg += f"• `{h.get('old', 'None')}` -> `{h.get('new', 'None')}` (On {h.get('changed_at')})\n"
            msg += "\n"
        if u.get("full_name_history"):
            has_data = True
            msg += "**Full Names:**\n"
            for h in u["full_name_history"][-20:]:
                msg += f"• `{h.get('old', 'None')}` -> `{h.get('new', 'None')}` (On {h.get('changed_at')})\n"
                
        if not has_data: msg = "No historical name changes recorded."
        await callback_query.message.reply(msg)
        await safe_answer(callback_query)
        
    elif action == "mp_groups":
        msg = f"🏢 **Known Groups for {uid}** (Recent 20)\n\n"
        group_found = False
        for c in u.get("chats", []):
            if str(c.get("type")) == "ChatType.PRIVATE": continue
            msg += f"• **{c.get('title', 'Unknown')}**\n  ID: `{c.get('chat_id')}`\n  Type: {c.get('type')}\n\n"
            group_found = True
            
        if not group_found: msg = "No groups recorded."
        await callback_query.message.reply(msg)
        await safe_answer(callback_query)

    elif action == "mp_lastseen":
        msg = f"🕒 **Last Seen History for {uid}** (Recent 20)\n\n"
        for c in u.get("chats", [])[-20:]:
            title = c.get('title', 'DM/Unknown')
            msg += f"• {title}: `{c.get('last_seen')}`\n"
            
        if not u.get("chats"): msg = "No history recorded."
        await callback_query.message.reply(msg)
        await safe_answer(callback_query)
        
    elif action == "mp_pic_idx":
        try:
            idx = int(data[2])
            pics = u.get("profile_pic_history", [])
            if 0 <= idx < len(pics):
                p = pics[idx]
                await safe_answer(callback_query)
                await client.send_photo(callback_query.message.chat.id, p["file_id"], caption=f"🖼 Past Profile Picture #{idx+1} from {p.get('changed_at')}")
            else:
                await safe_answer(callback_query, "Picture no longer in records.", show_alert=True)
        except Exception:
            await safe_answer(callback_query, "Error fetching picture.", show_alert=True)
            
    elif action == "mp_pic_past":
        pics = u.get("profile_pic_history", [])
        if not pics:
            return await safe_answer(callback_query, "No past profile pictures found.", show_alert=True)
            
        await safe_answer(callback_query)
        for i, p in enumerate(pics[-5:]): # Show up to 5 oldest
            await client.send_photo(callback_query.message.chat.id, p["file_id"], caption=f"🖼 Past Profile Picture from {p.get('changed_at')}")
            
    elif action == "mp_pic_curr":
        if not u.get("profile_pic"):
            return await safe_answer(callback_query, "No current profile picture.", show_alert=True)
            
        await safe_answer(callback_query)
        await client.send_photo(callback_query.message.chat.id, u["profile_pic"], caption=f"🖼 Current Profile Picture for {uid}")

# --- REGISTRATION ---

def register_admin_data_handlers(app: Client):
    # Import commands
    app.on_message(filters.command(list(IMPORT_CATEGORY_MAP.keys())) & admin_filter & filters.private)(import_command_handler)
    
    # Poster commands
    app.on_message(filters.command(list(POSTER_CATEGORY_COMMANDS.keys())) & admin_filter & filters.private)(add_poster_command)
    
    # General Admin
    app.on_message(filters.command(["add", "add_hindi", "add_regional", "add_jap", "add_c", "add_arb", "add_pak", "add_anime"]) & admin_filter & filters.private)(add_show_cmd)
    app.on_message(filters.command(list(DELETE_CATEGORY_COMMANDS.keys())) & admin_filter & filters.private)(delete_command_handler)
    app.on_message(filters.command("get") & admin_filter & filters.private)(get_link_cmd)
    app.on_message(filters.command("user_search") & admin_filter & filters.private)(user_search_cmd)
    app.on_message(filters.command("report_search") & admin_filter & filters.private)(report_search_cmd)
    app.on_callback_query(filters.regex(r"^user_history_\d+$") & admin_filter)(user_history_cb)
    app.on_callback_query(filters.regex(r"^mp_") & admin_filter)(mp_history_cb)
    
    # Stateful reception
    app.on_message(filters.private & filters.incoming & ~filters.command(["start", "help"]), group=-1)(handle_import_receive)
    app.on_message(filters.photo & filters.private & filters.incoming, group=-1)(handle_poster_receive)
