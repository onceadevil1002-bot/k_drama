from gc import callbacks
from datetime import datetime
import os
import json
import dns.resolver
import re
import time
from pymongo import MongoClient
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
    CallbackQuery,
)
import asyncio
from urllib.parse import unquote, quote
def is_valid_url(text):
    url_pattern = r'^https?://[\S]+'
    return re.match(url_pattern, text)

# Load environment variables from .env
load_dotenv()

# === Load Environment Variables ===
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
STORAGE_CHANNEL_ID = os.environ["STORAGE_CHANNEL_ID"]
MAIN_CHANNEL_LINK = os.environ["MAIN_CHANNEL_LINK"]

dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8']
app = Client("kdrama_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# === MongoDB Connection ===

# Load MongoDB URI
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("❌ MONGO_URI not set in environment variables.")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = client['kdrama']
collection = db['shows']

REPORTS = {}
reply_waiting = {}
# Poster upload state
poster_upload_state = {}


# Load data from MongoDB
def load_data():
    data = {}
    for doc in collection.find():
        category = doc.get("category", "Hindi Dubbed")
        show_name = doc["show_name"]
        episodes = doc.get("episodes", {})
        poster = doc.get("poster", [])

        if category not in data:
            data[category] = {}

        # Ensure it's a dictionary and attach poster safely
        if isinstance(episodes, dict):
            episodes["poster"] = poster
            data[category][show_name] = episodes
        else:
            # Handle corrupted data (rare case)
            data[category][show_name] = {"episodes": episodes, "poster": poster}

    return data
def backup_database():
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_dir = "backups"
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, f"mongo_backup_{now}.json")

    data = list(collection.find())
    for item in data:
        item["_id"] = str(item["_id"])  # Convert ObjectId to string for JSON

    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"✅ Database backed up to: {backup_path}")
# Save data to MongoDB
def save_data(data):
    for category, shows in data.items():
        for show_name, show_data in shows.items():
            # Separate out poster safely
            poster = show_data.get("poster", [])
            
            # Create a deep copy of the show data to avoid modifying the original
            episodes = {k: v for k, v in show_data.items() if k != "poster"}

            # Save into MongoDB
            collection.update_one(
                {"category": category, "show_name": show_name},
                {"$set": {
                    "category": category,
                    "show_name": show_name,
                    "episodes": episodes,
                    "poster": poster
                }},
                upsert=True
            )
def slugify_show_name(name: str) -> str:
    return re.sub(r'\W+', '', name.lower().replace(' ', '_'))

upload_state = {}

def migrate_category():
    collection.update_many({"category": {"$exists": False}}, {"$set": {"category": "Hindi Dubbed"}})

migrate_category()

joined_users = set()

@app.on_message(filters.command("start"))
async def start(client, message: Message):
    user_id = message.from_user.id
    data = load_data()
    args = message.text.split()
    slug = args[1] if len(args) > 1 else None

    if not slug and user_id not in joined_users:
        keyboard = [
            [InlineKeyboardButton("📣 Join Main Channel", url=MAIN_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ I Joined", callback_data="joined")]
        ]
        return await message.reply("🔒 Please join our channel to use the bot.", reply_markup=InlineKeyboardMarkup(keyboard))

    if slug and "__" in slug:
        try:
            category_part, show_part = slug.split("__", 1)
            category = category_part.replace("_", " ").lower().strip()
            show_name = show_part.replace("_", " ").strip()

            matched_category = None
            for existing_category in data:
                if existing_category.lower().strip() == category:
                    matched_category = existing_category
                    break

            if not matched_category or show_name not in data[matched_category]:
                return await message.reply("❌ Show not found or category mismatch.")
        except:
            return await message.reply("❌ Invalid link format.")

        buttons = [[InlineKeyboardButton(f"⭐️ {show_name}", callback_data=f"show_{matched_category}_{show_name}")]]
        for s in sorted(data[matched_category]):
            if s != show_name:
                if matched_category == "Hindi Dubbed":
                    emoji = "🎞"
                elif matched_category == "Regional":
                    emoji = "🌐"
                elif matched_category == "Japanese Drama":
                    emoji = "🎌"
                elif matched_category == "C Drama":
                    emoji = "📺"
                elif matched_category == "Arabic":
                    emoji = "🌍"
                else:
                    emoji = "📁"
                buttons.append([InlineKeyboardButton(f"{emoji} {s}", callback_data=f"show_{matched_category}_{s}")])

        category_titles = {
            "Hindi Dubbed": "🎞 Hindi Dubbed Shows:",
            "Regional": "🌐 Regional Shows:",
            "Japanese Drama": "🎌 Japanese Shows:",
            "C Drama": "📺 C Drama Shows:",
            "Arabic": "🌍 Arabic Shows:"
        }
        title = category_titles.get(matched_category, "📁 All Shows:")

        joined_users.add(user_id)
        return await message.reply(title, reply_markup=InlineKeyboardMarkup(buttons))

    joined_users.add(user_id)
    keyboard = [
        [InlineKeyboardButton("📂 Hindi Dubbed", callback_data="category_hindi")],
        [InlineKeyboardButton("📂 Japanese Drama", callback_data="category_japanese")],
        [InlineKeyboardButton("📂 C Drama", callback_data="category_c_drama")],
        [InlineKeyboardButton("📂 Arabic", callback_data="category_arabic")],
        [InlineKeyboardButton("🌐 Regional", callback_data="category_regional")],
        [InlineKeyboardButton("❗ Report For Show/Episodes", callback_data="report")]
    ]
    return await message.reply("🎬 Welcome to K-Drama Bot! Choose a category :\n"
                               " 😉please Join to Main channel we will Grow'😙", reply_markup=InlineKeyboardMarkup(keyboard))


@app.on_callback_query(filters.regex("^category_hindi$"))
async def category_hindi(client, callback):
    data = load_data()
    if "Hindi Dubbed" not in data:
        return await callback.answer("❌ No shows found.")
    buttons = [
        [InlineKeyboardButton(f"🎞 {show}", callback_data=f"show_Hindi Dubbed_{show}")]
        for show in sorted(data["Hindi Dubbed"])
    ]
    await callback.message.edit("🎞 Hindi Dubbed Shows:", reply_markup=InlineKeyboardMarkup(buttons))


@app.on_callback_query(filters.regex("^category_regional$"))
async def category_regional(client, callback):
    data = load_data()
    if "Regional" not in data:
        return await callback.answer("❌ No shows found.")
    buttons = [
        [InlineKeyboardButton(f"🌍 {show}", callback_data=f"show_Regional_{show}")]
        for show in sorted(data["Regional"])
    ]
    await callback.message.edit("🌍 Regional Shows:", reply_markup=InlineKeyboardMarkup(buttons))

# === Unified Category Show Listing ===
@app.on_callback_query(filters.regex("^category_"))
async def category_handler(client, callback_query):
    category_code = callback_query.data.split("_", 1)[1]  # e.g., 'japanese', 'c_drama'
    
    category_map = {
        "japanese": "Japanese Drama",
        "c_drama": "C Drama",
        "arabic": "Arabic"
    }
    matched_category = category_map.get(category_code)
    if not matched_category:
        return await callback_query.answer("❌ Unknown category.", show_alert=True)

    data = load_data()

    if matched_category in data:
        buttons = []
        for show_name in sorted(data[matched_category]):
            buttons.append([
                InlineKeyboardButton(
                    f"{show_name}", callback_data=f"show_{matched_category.replace('_', ' ')}_{show_name}"
                )
            ])
        await callback_query.message.edit_text(
            f"📺 {matched_category} Shows:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await callback_query.answer("No shows found in this category.", show_alert=True)



@app.on_callback_query(filters.regex("^joined$"))
async def joined_channel(client, callback: CallbackQuery):
    user_id = callback.from_user.id
    joined_users.add(user_id)

    keyboard = [
        [InlineKeyboardButton("📂 Hindi Dubbed", callback_data="category_hindi")],
        [InlineKeyboardButton("📂 Japanese Drama", callback_data="category_japanese")],
        [InlineKeyboardButton("📂 C Drama", callback_data="category_c_drama")],
        [InlineKeyboardButton("📂 Arabic", callback_data="category_arabic")],
        [InlineKeyboardButton("🌐 Regional", callback_data="category_regional")],
        [InlineKeyboardButton("❗ Report For Show/Episodes", callback_data="report")]
    ]
    await callback.message.edit("🎬 Welcome to K-Drama Bot! Choose a category:", reply_markup=InlineKeyboardMarkup(keyboard))

# ✅ Combined Add Show/Season Handler
@app.on_message(filters.command(["add", "add_hindi", "add_regional", "add_jap", "add_c", "add_arb"]) & filters.user(ADMIN_ID))
async def add_show(client, message):
    cmd = message.command[0]
    if len(message.command) < 2:
        return await message.reply(
            "❗️ Usage:\n"
            "/add Show Name\n"
            "/add Show Name > Category\n"
            "/add Show Name 1\n"
            "/add Show Name > Category 1\n"
            "/add_jap Show Name\n"
            "/add_c Show Name\n"
            "/add_arb Show Name"
        )

    args = message.text.split(" ", 1)[1].strip()
    category = "Hindi Dubbed"  # default
    show_name = None
    season_number = None

    # === SET CATEGORY BASED ON COMMAND ===
    if cmd == "add_hindi":
        category = "Hindi Dubbed"
    elif cmd == "add_regional":
        category = "Regional"
    elif cmd == "add_jap":
        category = "Japanese Drama"
    elif cmd == "add_c":
        category = "C Drama"
    elif cmd == "add_arb":
        category = "Arabic"

    # === ADVANCED PARSING FOR /add ONLY ===
    if cmd == "add" and ">" in args:
        try:
            show_part, rest = args.split(">", 1)
            show_part = show_part.strip()
            rest = rest.strip().split()

            show_name = show_part
            if rest and rest[-1].isdigit():
                season_number = rest[-1]
                category = " ".join(rest[:-1]).title() if len(rest) > 1 else category
            else:
                category = " ".join(rest).title()
        except:
            return await message.reply("❗️ Invalid format. Use /add Show Name > Category 1")
    else:
        parts = args.rsplit(" ", 1)
        if parts[-1].isdigit():
            show_name = parts[0].strip()
            season_number = parts[1].strip()
        else:
            show_name = args.strip()

    data = load_data()

    # === STRUCTURE INIT ===
    if category not in data:
        data[category] = {}

    if show_name not in data[category]:
        data[category][show_name] = {}
        data[category][show_name]["poster"] = []  # ✅ Targeted change: add poster list

        await message.reply(f"✅ Added show: *{show_name}* under *{category}*")

    if season_number:
        if season_number in data[category][show_name]:
            return await message.reply("⚠️ Season already exists.")
        data[category][show_name][season_number] = []
        await message.reply(f"✅ Added *Season {season_number}* under *{show_name}*")
    backup_database()
    save_data(data)

# Top-level alias map
CATEGORY_ALIASES = {
    "hindi": "Hindi Dubbed",
    "jap": "Japanese Drama",
    "japanese": "Japanese Drama",
    "c": "C Drama",
    "c-drama": "C Drama",
    "chinese": "C Drama",
    "arb": "Arabic Drama",
    "arabic": "Arabic Drama",
    "regional": "Regional"
}

@app.on_message(filters.command("add_poster") & filters.user(ADMIN_ID))
async def add_poster_cmd(client, message):
    try:
        cmd = message.text.split(" ", 1)[1].strip()
        if ">" not in cmd:
            return await message.reply("❌ Use format: /add_poster Show Name > Category", quote=True)

        show_name, raw_category = map(str.strip, cmd.split(">"))
        cat_key = raw_category.strip().lower()
        category = CATEGORY_ALIASES.get(cat_key, raw_category.strip().title())

        # ✅ Check if the show exists under this category
        data = load_data()
        if category not in data or show_name not in data[category]:
            return await message.reply(f"❌ Show *{show_name}* not found under *{category}*.")

        # ✅ Store state
        poster_upload_state[message.from_user.id] = {
            "show_name": show_name,
            "category": category,
            "file_ids": [],
            "deadline": time.time() + 60
        }

        await message.reply("🖼 Send 1–6 poster screenshots now (within 60 seconds)...")

    except IndexError:
        await message.reply("❌ Use format: /add_poster Show Name > Category", quote=True)
@app.on_message(filters.photo & filters.user(ADMIN_ID))
async def collect_poster(client, message):
    user_id = message.from_user.id
    state = poster_upload_state.get(user_id)

    if not state:
        return

    if time.time() > state["deadline"]:
        del poster_upload_state[user_id]
        return await message.reply("⏱️ Time expired! Please run /add_poster again.")

    if len(state["file_ids"]) >= 6:
        return await message.reply("❌ Max 6 posters allowed.")

    file_id = message.photo.file_id
    state["file_ids"].append(file_id)

    if len(state["file_ids"]) >= 1:
        await finalize_poster_upload(client, message)

@app.on_message(filters.command("done") & filters.user(ADMIN_ID))
async def manual_done_poster(client, message):
    user_id = message.from_user.id
    if user_id in poster_upload_state:
        await finalize_poster_upload(client, message)

async def finalize_poster_upload(client, message):
    user_id = message.from_user.id
    state = poster_upload_state.pop(user_id, None)

    if not state or not state["file_ids"]:
        return await message.reply("❌ No posters received.")

    result = collection.update_one(
        {"show_name": state["show_name"], "category": state["category"]},
        {"$set": {"poster": state["file_ids"]}}
    )

    if result.matched_count > 0:
        await message.reply(f"✅ Poster uploaded for *{state['show_name']}*.")
    else:
        await message.reply("❌ Show not found or poster not saved.")
@app.on_message(filters.command([
    "upload", "upload_hindi", "upload_regional",
    "upload_jap", "upload_c", "upload_arb"
]) & filters.user(ADMIN_ID))
async def upload_handler(client, message: Message):
    cmd = message.command[0]
    if len(message.command) < 2:
        return await message.reply("❗️ Usage:\n"
                                   "/upload Show Name\n"
                                   "/upload Show Name 1\n"
                                   "/upload Show Name > Category\n"
                                   "/upload Show Name > Category 2")

    args = message.text.split(" ", 1)[1].strip()
    data = load_data()

    # Default category (used if not overridden)
    category = "Hindi Dubbed"
    show_name = None
    season_number = None

    # ✅ Map shortcut commands to categories
    cmd_category_map = {
        "upload_hindi": "Hindi Dubbed",
        "upload_regional": "Regional",
        "upload_jap": "Japanese Drama",
        "upload_c": "C Drama",
        "upload_arb": "Arabic"
    }
    if cmd in cmd_category_map:
        category = cmd_category_map[cmd]

    # ✅ Handle category inline with > symbol
    if ">" in args:
        try:
            show_part, rest = args.split(">", 1)
            show_part = show_part.strip()
            rest = rest.strip().split()

            show_name = show_part
            if rest and rest[-1].isdigit():
                season_number = rest[-1]
                category = " ".join(rest[:-1]).title() if len(rest) > 1 else category
            else:
                category = " ".join(rest).title()
        except:
            return await message.reply("❗️ Invalid format. Use /upload Show Name > Category 1")
    else:
        # ✅ Handle legacy: "Show Name 1"
        parts = args.rsplit(" ", 1)
        if parts[-1].isdigit():
            show_name = parts[0].strip()
            season_number = parts[1].strip()
        else:
            show_name = args.strip()

    # ✅ Validation: Ensure show exists
    if category not in data or show_name not in data[category]:
        return await message.reply("❗️ Show not found. Use /add command first.")

    # 🔐 Split Episode Check
    if season_number:
        if season_number not in data[category][show_name]:
            return await message.reply("❗️ Season not found. Use /add to create it first.")
        episodes = data[category][show_name][season_number]
        if episodes and isinstance(episodes[-1], list) and None in episodes[-1]:
            return await message.reply("⚠️ Last episode appears split and incomplete. Use /upload_split to upload parts safely.")
        upload_state[message.from_user.id] = {
            "show": show_name,
            "season": season_number,
            "category": category
        }
            # ✅ Also allow links directly after /upload
        if is_valid_url(message.text.strip()):
            data = load_data()
            episodes = data[category][show_name][season_number] if season_number else data[category][show_name]["episodes"]

            episodes.append({
                "type": "link",
                "content": message.text.strip()
            })
            backup_database()
            save_data(data)
            return await message.reply("🔗 Link episode saved successfully.")

        return await message.reply(f"📤 Send videos now for *{show_name}* Season *{season_number}*", quote=True)
    
    else:
        flat_eps = data[category][show_name]["episodes"]
        if flat_eps and isinstance(flat_eps[-1], list):
            return await message.reply("⚠️ Last episode appears split. Use /upload_split to upload parts safely.")
        
        upload_state[message.from_user.id] = {
            "show": show_name,
            "season": None,
            "category": category
        }
        return await message.reply(f"📤 Send videos now for *{show_name}*", quote=True)



# ===== FIXED VIDEO SAVING =====
@app.on_message(filters.video & filters.user(ADMIN_ID))
async def handle_video(client, message: Message):
    user_id = message.from_user.id
    if user_id not in upload_state:
        return await message.reply("⚠️ First use /upload ShowName or /upload ShowName SeasonNumber")

    state = upload_state[user_id]
    show_name = state["show"]
    season_number = state["season"]
    category = state["category"]

    try:
        # ✅ Forward to storage channel
        fwd = await message.forward(STORAGE_CHANNEL_ID, disable_notification=True)
        file_id = fwd.video.file_id

        # ✅ NEW: delete from storage channel to keep chat invisible
        await fwd.delete()

        # ✅ Save to MongoDB in correct category path
        data = load_data()

        if "split_index" in state:
            # 🔥 Uploading to a split episode
            split_index = state["split_index"]
            part = state["part"]

            episodes = data[category][show_name][season_number]
            if not isinstance(episodes[split_index], list):
                return await message.reply("❌ Target episode is not split. Use /split_episode first.")

            # Ensure list has space for two parts
            while len(episodes[split_index]) <= part:
                episodes[split_index].append(None)

            episodes[split_index][part] = file_id
            backup_database()
            save_data(data)
            return await message.reply(f"✅ Uploaded part {part + 1} of split episode {split_index + 1} in *{show_name}* Season {season_number}")

        episode_data = {
            "type": "video",
            "content": file_id
        }

        if season_number:
            data[category][show_name].setdefault(season_number, []).append(episode_data)
        else:
            data[category][show_name].setdefault("episodes", []).append(episode_data)
        backup_database()
        save_data(data)

        await message.reply(
            f"✅ Uploaded and saved for *{show_name}*{' Season ' + season_number if season_number else ''}.",
            quote=True
        )

    except Exception as e:
        print("Upload error:", e)
        await message.reply("❌ Failed to upload. Please check bot permissions or channel ID.")


@app.on_callback_query(filters.regex("^multi_"))
async def multi_part_episode_menu(client, callback: CallbackQuery):
    try:
        _, category, show_name, season_or_key, index = callback.data.split("_", 4)
        index = int(index) - 1
        data = load_data()

        multi_parts = data[category][show_name][season_or_key][index]
        if not isinstance(multi_parts, list) or len(multi_parts) != 2:
            return await callback.answer("⚠️ Multi-part not found.")

        buttons = [
            [InlineKeyboardButton("▶️ Episode", callback_data=f"mp_ep_{category}_{show_name}_{season_or_key}_{index}_0")],
            [InlineKeyboardButton("▶️ Episode .5", callback_data=f"mp_ep_{category}_{show_name}_{season_or_key}_{index}_1")]
        ]

        await callback.message.edit(
            f"🎬 Parts for Episode {index + 1}",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    except Exception as e:
        print("Multi-part submenu error:", e)
        await callback.answer("❌ Error showing split parts.")

@app.on_callback_query(filters.regex("^mp_ep_"))
async def send_multi_part_episode(client, callback: CallbackQuery):
    try:
        _, category, show_name, season_or_key, index_str, part_str = callback.data.split("_", 5)
        index = int(index_str)
        part = int(part_str)
        data = load_data()

        file_id = data[category][show_name][season_or_key][index][part]
        caption = f"🎬 {show_name} - Episode {index + 1}{'.5' if part == 1 else ''}"
        sent = await client.send_video(chat_id=callback.from_user.id, video=file_id, caption=caption)

        await callback.answer("✅ Sent! Auto-delete in 3 mins.")
        await asyncio.sleep(180)
        await sent.delete()

    except Exception as e:
        print("Multi-part send error:", e)
        await callback.answer("❌ Couldn't send episode.")




@app.on_callback_query(filters.regex("^season_(.+?)_(.+?)_(.+)$"))
async def season_menu(client, callback_query):
    category, show_name, season_number = callback_query.data.split("_", 3)[1:]
    data = load_data()

    if show_name not in data.get(category, {}) or season_number not in data[category][show_name]:
        await callback_query.answer("Season not found.")
        return

    buttons = []
    for idx, file_id in enumerate(data[category][show_name][season_number], start=1):
        buttons.append([
            InlineKeyboardButton(
                f"Episode {idx}",
                callback_data=f"episode_{category}_{show_name}_{season_number}_{idx}"
            )
        ])

    caption = f"🎬 {show_name.replace('_', ' ')} - Season {season_number}"
    poster_list = data[category][show_name].get("poster", [])
    poster = poster_list[-1] if isinstance(poster_list, list) and poster_list else None

    if poster:
        await client.send_photo(
            chat_id=callback_query.from_user.id,
            photo=poster,
            caption=caption,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await callback_query.message.edit(
            caption,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    await callback_query.answer()


@app.on_callback_query(filters.regex("^show_(.+?)_(.+)$"))
async def show_menu(client, callback_query):
    category, show_name = callback_query.data.split("_", 2)[1:]
    data = load_data()

    if show_name not in data.get(category, {}):
        await callback_query.answer("Show not found.")
        return

    buttons = []
    episodes = data[category][show_name]

    # Handle flat episodes
    if "episodes" in episodes:
        for idx, ep in enumerate(episodes["episodes"], start=1):
            if isinstance(ep, list):  # split episode
                buttons.append([
                    InlineKeyboardButton(
                        f"📁 Episode {idx} (split)",
                        callback_data=f"multi_{category}_{show_name}_episodes_{idx}"
                    )
                ])
            else:
                buttons.append([
                    InlineKeyboardButton(
                        f"Episode {idx}",
                        callback_data=f"episode_{category}_{show_name}_episodes_{idx}"
                    )
                ])

    # Handle seasons
    for season in episodes:
        if season not in ["episodes", "poster"]:
            buttons.append([
                InlineKeyboardButton(
                    f"📁 Season {season}",
                    callback_data=f"season_{category}_{show_name}_{season}"
                )
            ])

    if not buttons:
        buttons.append([InlineKeyboardButton("🚫 No videos uploaded yet", callback_data="noop")])

    caption = f"📺 Show: {show_name.replace('_', ' ')}"
    poster_list = data[category][show_name].get("poster", [])
    poster = poster_list[-1] if isinstance(poster_list, list) and poster_list else None

    if poster:
        await client.send_photo(
            chat_id=callback_query.from_user.id,
            photo=poster,
            caption=caption,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await callback_query.message.edit(
            caption,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    await callback_query.answer()


@app.on_message(filters.photo & filters.user(ADMIN_ID))
async def handle_poster_photo(client, message: Message):
    user_id = message.from_user.id
    if user_id not in poster_upload_state:
        return
    
    state = poster_upload_state[user_id]
    
    # Check if we've reached the max posters (6)
    if len(state["file_ids"]) >= 6:
        return await message.reply("❌ Maximum 6 posters allowed. Use /add_poster again if needed.")
    
    # Forward to storage channel and get file_id
    try:
        fwd = await message.forward(STORAGE_CHANNEL_ID, disable_notification=True)
        state["file_ids"].append(fwd.photo.file_id)
        await fwd.delete()  # Clean up storage channel
        
        remaining = 6 - len(state["file_ids"])
        if remaining > 0:
            await message.reply(f"🖼 Poster received ({len(state['file_ids'])}/6). Send more or wait to auto-finalize.")
        else:
            await finalize_poster_upload(client, message)
            
    except Exception as e:
        print("Poster upload error:", e)
        await message.reply("❌ Failed to process poster.")


@app.on_message(filters.command(["split_hindi", "split_jap", "split_c", "split_arb"]) & filters.user(ADMIN_ID))
async def split_episode_command(client, message: Message):
    try:
        cmd = message.command[0]
        if len(message.command) < 2 or "/" not in message.text:
            return await message.reply(
                "❗️ Usage:\n"
                "/split_hindi Show Name Season No/Episode No\n"
                "/split_jap Show Name Season No/Episode No\n"
                "/split_c Show Name Season No/Episode No\n"
                "/split_arb Show Name Season No/Episode No"
            )

        # Extracting the information
        args = message.text.split(" ", 1)[1].strip()
        left, episode_part = args.rsplit("/", 1)
        episode_num = int(episode_part.strip())  # e.g., 1
        episode_index = episode_num - 1  # Python index adjustment

        tokens = left.strip().split()
        show_name = " ".join(tokens[:-1]) if tokens[-1].isdigit() else " ".join(tokens)
        season_number = tokens[-1] if tokens[-1].isdigit() else None

        # Mapping command to categories
        cmd_category_map = {
            "split_hindi": "Hindi Dubbed",
            "split_jap": "Japanese Drama",
            "split_c": "C Drama",
            "split_arb": "Arabic"
        }
        category = cmd_category_map.get(cmd)
        
        data = load_data()

        if category not in data or show_name not in data[category]:
            return await message.reply("❌ Show not found in the selected category.")

        # Determine the episodes list based on season or flat
        episodes = (
            data[category][show_name].get(season_number)
            if season_number else data[category][show_name].get("episodes")
        )
        
        if not episodes:
            return await message.reply("❌ No episodes found for the selected show.")
        
        # Split the episode
        if episode_index < 0 or episode_index >= len(episodes):
            return await message.reply("❌ Episode index out of range.")

        original = episodes[episode_index]

        # Check if it's already split
        if isinstance(original, list):
            return await message.reply("⚠️ Episode is already split.")

        # Mark the episode as split (into part 1 and part 2)
        episodes[episode_index] = [original, None]
        backup_database()
        save_data(data)

        label = f"{episode_num} (S{season_number})" if season_number else f"{episode_num}"
        return await message.reply(f"✅ Episode {label} is now split into two parts (waiting for part 2).")

    except Exception as e:
        print("[split_episode_command] error:", e)
        return await message.reply("❌ Failed to process split episode.")
    
@app.on_message(filters.command(["upload_split_hindi", "upload_split_regional", "upload_split_jap", "upload_split_c", "upload_split_arb"]) & filters.user(ADMIN_ID))
async def upload_split_handler(client, message: Message):
    try:
        cmd = message.command[0]
        cmd_category_map = {
            "upload_split_hindi": "Hindi Dubbed",
            "upload_split_regional": "Regional",
            "upload_split_jap": "Japanese Drama",
            "upload_split_c": "C Drama",
            "upload_split_arb": "Arabic"
        }
        category = cmd_category_map[cmd]

        if len(message.command) < 2:
            return await message.reply("❗️ Usage:\n/upload_split_hindi Show Season Episode Part")

        args = message.text.split(" ", 1)[1].strip().split()
        if len(args) < 4:
            return await message.reply("❌ Format error. Provide Show, Season, Episode, Part")

        show_name = " ".join(args[:-3])
        season = args[-3]
        episode = int(args[-2])
        part_index = int(args[-1]) - 1  # 0 or 1

        if part_index not in (0, 1):
            return await message.reply("❌ Part must be 1 or 2")

        data = load_data()

        if category not in data or show_name not in data[category]:
            return await message.reply("❌ Show not found.")
        if season not in data[category][show_name]:
            return await message.reply("❌ Season not found.")

        episodes = data[category][show_name][season]
        if episode < 1 or episode > len(episodes):
            return await message.reply("❌ Episode index out of range.")
        if not isinstance(episodes[episode - 1], list):
            return await message.reply("❌ Episode not marked as split. Use /split_hindi first.")

        upload_state[message.from_user.id] = {
            "show": show_name,
            "season": season,
            "category": category,
            "split_index": episode - 1,
            "part": part_index
        }

        return await message.reply(
            f"📤 Send video for *{show_name}* S{season} Ep {episode} Part {part_index + 1}"
        )
    except Exception as e:
        print("[upload_split_handler] error:", e)
        return await message.reply("❌ Failed to process upload split.")

async def send_poster_if_exists(client, chat_id, show_name, category):
    doc = collection.find_one({"show_name": show_name, "category": category})
    posters = doc.get("poster", []) if doc else []
    for file_id in posters:
        await client.send_photo(chat_id, file_id)
    
@app.on_callback_query(filters.regex("^noop$"))
async def do_nothing(client, callback_query: CallbackQuery):
    await callback_query.answer("Nothing to show here.")


@app.on_callback_query(filters.regex("^multi_"))
async def split_parts_view(client, callback_query):
    try:
        _, category, show_name, key, index = callbacks.data.split("_", 4)
        index = int(index) - 1
        data = load_data()

        if show_name not in data[category] or key not in data[category][show_name]:
            return await callback_query.answer("❌ Not found.")

        parts = data[category][show_name][key][index]
        if not isinstance(parts, list):
            return await callback_query.answer("❌ Not a split episode.")

        buttons = []
        if parts[0]:
            buttons.append([InlineKeyboardButton("🅰️ Part 1", callback_data=f"episode_{category}_{show_name}_{key}_{index + 1}_0")])
        if parts[1]:
            buttons.append([InlineKeyboardButton("🅱️ Part 2", callback_data=f"episode_{category}_{show_name}_{key}_{index + 1}_1")])

        await callback_query.message.edit(
            f"🎬 {show_name} - Episode {index + 1} Split Parts",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        print("split_parts_view error:", e)
        await callback_query.answer("⚠️ Error opening split episode.")


@app.on_message(filters.command("list") & filters.user(ADMIN_ID))
async def list_content(client, message):
    data = load_data()
    output = "📋 Available Shows:\n"
    for show_name, seasons in data.items():
        output += f"- {show_name}\n"
        if "episodes" in seasons:
            output += "  - Episodes\n"
        for season_number, episodes in seasons.items():
            if season_number != "episodes":
                output += f"  - Season {season_number}\n"
                for idx in range(len(episodes)):
                    output += f"    - Episode {idx + 1}\n"
    await message.reply(output)



@app.on_message(filters.command("get_links") & filters.user(ADMIN_ID))
async def get_links(client, message):
    data = load_data()
    if not data:
        return await message.reply("❌ No shows found.")

    bot_username = (await client.get_me()).username
    links = []
    added = set()

    for category in data:
        for show_name, content in data[category].items():
            # Skip if it's a nested season inside another show (not valid show)
            if not isinstance(content, dict):
                continue

            # Only generate once per unique show name
            if show_name in added:
                continue

            slug = f"{category.replace(' ', '_')}__{show_name.replace(' ', '_')}"
            link = f"https://t.me/{bot_username}?start={slug}"
            links.append(f"🔗 [{show_name} ({category})]({link})")
            added.add(show_name)

    await message.reply(
        "**🎬 Direct Show Links:**\n\n" + "\n".join(links),
        disable_web_page_preview=True
    )


# === Help Command (Updated) ===
@app.on_message(filters.command("help"))
async def help_command(client, message):
    is_admin = message.from_user.id == ADMIN_ID

    admin_help = """
📘 ADMIN COMMANDS PANEL

━━━━━━━━━━━━━━━
📁 ADD SHOWS/SEASONS
━━━━━━━━━━━━━━━

🔸 Hindi Dubbed:
  /add_hindi Show Name
  /add Show Name > Hindi Dubbed
  /add Show Name > Hindi Dubbed Season

🔸 Regional:
  /add_regional Show Name
  /add Show Name > Regional
  /add Show Name > Regional Season

🔸 Japanese Drama:
  /add_jap Show Name
  /add Show Name > Japanese Drama
  /add Show Name > Japanese Drama Season

🔸 C Drama:
  /add_c Show Name
  /add Show Name > C Drama
  /add Show Name > C Drama Season

🔸 Arabic:
  /add_arb Show Name
  /add Show Name > Arabic
  /add Show Name > Arabic Season

━━━━━━━━━━━━━━━
📤 UPLOAD VIDEOS
━━━━━━━━━━━━━━━

🔸 Hindi Dubbed:
  /upload_hindi Show Name
  /upload Show Name > Hindi Dubbed
  /upload Show Name > Hindi Dubbed Season

🔸 Regional:
  /upload_regional Show Name
  /upload Show Name > Regional
  /upload Show Name > Regional Season

🔸 Japanese Drama:
  /upload_jap Show Name
  /upload Show Name > Japanese Drama
  /upload Show Name > Japanese Drama Season

🔸 C Drama:
  /upload_c Show Name
  /upload Show Name > C Drama
  /upload Show Name > C Drama Season

🔸 Arabic:
  /upload_arb Show Name
  /upload Show Name > Arabic
  /upload Show Name > Arabic Season

━━━━━━━━━━━━━━━
🎬 SPLIT & UPLOAD SPLIT PARTS
━━━━━━━━━━━━━━━

🔸 Split Episodes:
  /split_episode Show Name /EpNo > Category
  (e.g. /split_episode Tokyo Love /3 > Japanese Drama)

🔸 Upload Split Episodes:
  /upload_split_hindi Show Season Ep Part
  /upload_split_regional Show Season Ep Part
  /upload_split_jap Show Season Ep Part
  /upload_split_c Show Season Ep Part
  /upload_split_arb Show Season Ep Part

━━━━━━━━━━━━━━━
🧹 Delete Commands:

🟥 Hindi Dubbed
/delete Show Name
/delete Show Name Season
/delete Show Name Season /Episode
/delete Show Name Season /SplitEpisode

🟦 Regional
/delete_regional Show Name
/delete_regional Show Name Season
/delete_regional Show Name Season /Episode
/delete_regional Show Name Season /SplitEpisode

🟨 Japanese Drama
/delete_jap Show Name
/delete_jap Show Name Season
/delete_jap Show Name Season /Episode
/delete_jap Show Name Season /SplitEpisode

🟩 C Drama
/delete_c Show Name
/delete_c Show Name Season
/delete_c Show Name Season /Episode
/delete_c Show Name Season /SplitEpisode

🟧 Arabic
/delete_arb Show Name
/delete_arb Show Name Season
/delete_arb Show Name Season /Episode
/delete_arb Show Name Season /SplitEpisode


━━━━━━━━━━━━━━━
🛠 UTILITIES
━━━━━━━━━━━━━━━

  /get_links – Get shareable links
  /list – Display all shows & structure
  /test_forward – Check forwarding
  /report – Handle user reports
  /add_poster Mercy for None > Hindi Dubbed

━━━━━━━━━━━━━━━
🔗 CHANNEL
━━━━━━━━━━━━━━━
https://t.me/KDRAMAAVIL
"""

    user_help = """
👤 USER COMMANDS

- Use buttons to browse and stream shows
- Videos auto-delete after 3 minutes
- Use /report to request missing episodes
- Updates: [Join Channel](https://t.me/KDRAMAAVIL)
"""

    await message.reply(admin_help if is_admin else user_help, disable_web_page_preview=True)
@app.on_message(filters.command([
    "delete", "delete_regional", "delete_jap", "delete_c", "delete_arb"
]) & filters.user(ADMIN_ID))
async def delete_content(client, message: Message):
    try:
        cmd = message.command[0]
        if len(message.command) < 2:
            return await message.reply("❗ Usage:\n/delete Show\n/delete Show Season\n/delete Show /Episode\n/delete Show Season /Episode")

        args = message.text.split(" ", 1)[1].strip()

        # 🔁 Detect episode deletion like /delete Show /3 (season-less)
        if " /" in args:
            show_part, ep_str = args.rsplit("/", 1)
            show_name = show_part.strip()
            season = None
            episode_index = int(ep_str.strip()) - 1
        elif "/" in args:
            show_season, ep_str = args.rsplit("/", 1)
            parts = show_season.rsplit(" ", 1)
            if parts[-1].isdigit():
                show_name = parts[0].strip()
                season = parts[1].strip()
            else:
                show_name = show_season.strip()
                season = None
            episode_index = int(ep_str.strip()) - 1
        else:
            parts = args.rsplit(" ", 1)
            if parts[-1].isdigit():
                show_name = parts[0].strip()
                season = parts[1].strip()
            else:
                show_name = args.strip()
                season = None
            episode_index = None

        # 🔎 Determine category
        CATEGORY_ALIASES = {
            "hindi": "Hindi Dubbed",
            "jap": "Japanese Drama",
            "japanese": "Japanese Drama",
            "c": "C Drama",
            "c-drama": "C Drama",
            "chinese": "C Drama",
            "arb": "Arabic Drama",
            "arabic": "Arabic Drama",
            "regional": "Regional"
        }
        category = cmd_category_map.get(cmd, "Hindi Dubbed")
        data = load_data()

        if show_name not in data.get(category, {}):
            return await message.reply(f"❌ Show not found in *{category}*")

        # === Handle full show deletion ===
        if season is None and episode_index is None:
            del data[category][show_name]
            backup_database()
            save_data(data)
            return await message.reply(f"✅ Deleted *{show_name}* from *{category}*")

        # === Handle season deletion ===
        if season and episode_index is None:
            if season not in data[category][show_name]:
                return await message.reply("❌ Season not found.")
            del data[category][show_name][season]
            backup_database()
            save_data(data)
            return await message.reply(f"✅ Deleted *Season {season}* from *{show_name}*")

        # === Handle episode deletion ===
        key = season if season else "episodes"
        if key not in data[category][show_name]:
            return await message.reply("❌ Season or episode list not found.")

        episodes = data[category][show_name][key]
        if episode_index < 0 or episode_index >= len(episodes):
            return await message.reply("❌ Episode index out of range.")
        del episodes[episode_index]
        backup_database()
        save_data(data)
        return await message.reply(f"✅ Deleted Episode {episode_index + 1} from *{show_name}* {f'Season {season}' if season else ''}")

    except Exception as e:
        print("❌ Delete Error:", e)
        return await message.reply("⚠️ Something went wrong while deleting.")

@app.on_callback_query(filters.regex("^episode_"))
async def send_episode(client, callback_query: CallbackQuery):
    try:
        payload = callback_query.data[len("episode_"):]
        parts = payload.split("_")

        if len(parts) < 4:
            return await callback_query.answer("❌ Invalid episode data.")

        category = parts[0]
        index_str = parts[-1]
        season_or_key = parts[-2]
        show_name_parts = parts[1:-2]
        show_name = "_".join(show_name_parts)
        index = int(index_str) - 1
        data = load_data()
        


        if show_name not in data.get(category, {}):
            return await callback_query.answer("❌ Show not found.")

        if season_or_key not in data[category][show_name]:
            return await callback_query.answer("❌ Season or episode list not found.")
        
        if season_or_key == "poster":
            return await callback_query.answer("⚠️ Poster is not an episode.")

        episode_list = data[category][show_name][season_or_key]

        if index < 0 or index >= len(episode_list):
            return await callback_query.answer("❌ Episode index out of range.")

        episode_data = episode_list[index]

        # ✅ Check if it's a split episode
        if isinstance(episode_data, list):
            buttons = []
            for i, part_file_id in enumerate(episode_data):
                if part_file_id is not None:
                    buttons.append([
                        InlineKeyboardButton(
                            f"▶️ Part {i+1}",
                            callback_data=f"splitpart_{category}_{show_name}_{season_or_key}_{index}_{i}"
                        )
                    ])
            if not buttons:
                return await callback_query.answer("⚠️ Split parts not uploaded yet.")
            return await callback_query.message.edit_text(
                f"🎬 {show_name.replace('_', ' ')} - Episode {index+1} (Split)",
                reply_markup=InlineKeyboardMarkup(buttons)
            )

        # Normal full episode
        # Handle old plain string format
        if isinstance(episode_data, str):
            episode_data = { "type": "video", "content": episode_data }

        if episode_data["type"] == "video":
            sent_msg = await client.send_video(
                chat_id=callback_query.from_user.id,
                video=episode_data["content"],
                caption=f"🎬 {show_name.replace('_', ' ')} - Episode {index + 1}"
            )
            await callback_query.answer("✅ Sent video. It will auto-delete in 3 minutes.")
            await asyncio.sleep(180)
            await sent_msg.delete()

        elif episode_data["type"] == "link":
            await client.send_message(
                chat_id=callback_query.from_user.id,
                text=(
                    f"🎬 {show_name.replace('_', ' ')} - Episode {index + 1}\n"
                    f"📥 Download Link 👇"
                ),
                reply_markup=InlineKeyboardMarkup([[ 
                    InlineKeyboardButton("▶ Watch Episode", url=episode_data["content"])
                ]]),
                disable_web_page_preview=False
            )
            await callback_query.answer("✅ Sent link.")
            return  # prevent fallthrough to final video logic


        await callback_query.answer("✅ Sent video. It will auto-delete in 3 minutes.")
        await asyncio.sleep(180)
        await sent_msg.delete()

    except Exception as e:
        print(f"[send_episode] ERROR: {e}")
        await callback_query.answer("⚠️ Error while sending episode.")

@app.on_callback_query(filters.regex("^splitpart_"))
async def send_split_part(client, callback_query: CallbackQuery):
    try:
        payload = callback_query.data[len("splitpart_"):]
        parts = payload.split("_")
        if len(parts) < 5:
            return await callback_query.answer("❌ Invalid split part data.")

        category, *name_parts, season_or_key, ep_index_str, part_index_str = parts
        show_name = "_".join(name_parts)
        ep_index = int(ep_index_str)
        part_index = int(part_index_str)
        data = load_data()

        file_id = data[category][show_name][season_or_key][ep_index][part_index]

        sent_msg = await client.send_video(
            chat_id=callback_query.from_user.id,
            video=file_id,
            caption=f"🎬 {show_name.replace('_', ' ')} - Part {part_index + 1}"
        )
        await callback_query.answer("✅ Sent part. Auto-deletes in 3 minutes.")
        await asyncio.sleep(180)
        await sent_msg.delete()

    except Exception as e:
        print(f"[send_split_part] ERROR: {e}")
        await callback_query.answer("⚠️ Could not send split part.")

@app.on_callback_query(filters.regex("^play_"))
async def play_split_part(client, callback_query: CallbackQuery):
    try:
        _, category, show_name, season_or_key, index_str, part_str = callback_query.data.split("_", 5)
        index = int(index_str)
        part = int(part_str)

        data = load_data()
        episodes = data.get(category, {}).get(show_name, {}).get(season_or_key)
        episode = episodes[index]
        file_id = episode[part]

        if not file_id:
            return await callback_query.answer(f"❌ Part {part+1} not uploaded yet.")

        sent_msg = await client.send_video(
            chat_id=callback_query.from_user.id,
            video=file_id,
            caption=f"🎬 {show_name.replace('_', ' ')} - Episode {index+1} Part {part+1}"
        )
        await callback_query.answer("✅ Sent part. Will auto-delete in 3 minutes.")
        await asyncio.sleep(180)
        await sent_msg.delete()

    except Exception as e:
        print(f"[play_split_part] ERROR: {e}")
        await callback_query.answer("⚠️ Error while sending part.")

@app.on_message(filters.command("test_forward"))
async def test_forward(client, message):
    try:
        # Attempt to forward a test message to the storage channel
        await client.forward_messages(
            chat_id=STORAGE_CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_ids=message.message_id,
            disable_notification=True
        )
        await message.reply("✅ Forwarding successful!")
    except Exception as e:
        await message.reply(f"❌ Error forwarding: {e}")

@app.on_callback_query(filters.regex("^report$"))
async def handle_report(client, callback_query: CallbackQuery):
    # Edit the message to prompt the user to type the name of the missing show/episode
    await callback_query.message.edit("✍️ Please type the name of the missing show/episode.")
    return




# === USER REPORT HANDLER ===
@app.on_message(filters.text & ~filters.command(["start", "help", "add", "upload", "delete"]) & ~filters.user(ADMIN_ID))
async def handle_user_report(client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()

    if not text:
        return await message.reply("❗ Empty message, Please Enter your report.")

    try:
        # ✅ send report to admin with inline reply button
        report_msg = await client.send_message(
            ADMIN_ID,
            f"📩 New Report from `{user_id}`:\n\n{message.text}",
            reply_markup=InlineKeyboardMarkup([[ 
                InlineKeyboardButton("💬 Reply", callback_data=f"reply_to_{user_id}")
            ]])
        )
        
        # ✅ save users id using admin message id
        REPORTS[str(report_msg.message_id)] = user_id

        # ✅ Confirm to the user
        await message.reply("✅ Report sent! the admin will reply soon if needed ")
    
    except Exception as e:
        # if anything falls 
        await message.reply("❌ Failed to send report. Please try again later.")
        print("[handle_users_report] Error:", e)
        return
      






# === BUTTON REPLY FROM ADMIN ===
@app.on_callback_query(filters.regex("^reply_to_"))
async def handle_reply_button(client, callback: CallbackQuery):
    try:
        user_id = int(callback.data.split("_")[2])
        reply_waiting[callback.from_user.id] = user_id
        await callback.message.reply("✍️ Send your reply now. It will be forwarded to the user.")
        await callback.answer()
     
    except Exception as e:
        print("[reply_to_] Error:", e)
        await callback.answer("⚠️ Failed to initiate reply.")


@app.on_message(filters.text & ~filters.regex(r"^/\w+") & filters.user(ADMIN_ID))
async def handle_link(client, message: Message):
    user_id = message.from_user.id
    if user_id not in upload_state:
        return  # not in upload mode

    from re import match

    url = message.text.strip()
    if not match(r"^https?://", url):
        return  # not a link

    state = upload_state[user_id]
    show = state["show"]
    season = state["season"]
    category = state["category"]

    data = load_data()

    if season:
        data[category][show].setdefault(season, []).append({
            "type": "link",
            "content": url
        })
    else:
        data[category][show].setdefault("episodes", []).append({
            "type": "link",
            "content": url
        })
    backup_database()
    save_data(data)
    await message.reply("🔗 Link episode saved successfully.")

# === ACTUAL REPLY FROM ADMIN ===
@app.on_message(filters.text & filters.user(ADMIN_ID))
async def admin_reply_to_user(client, message: Message):
    admin_id = message.from_user.id
    target_id = reply_waiting.get(admin_id)

    if not target_id:
        return  # Ignore regular admin messages not linked to a reply

    try:
        await client.send_message(
            target_id,
            f"📢 Admin replied to your report:\n\n{message.text}"
        )
        await message.reply("✅ Your reply has been sent to the user.")
    except Exception as e:
        await message.reply(f"❌ Failed to send reply: {e}")

    # Clean up
    del reply_waiting[admin_id]

@app.on_message()
async def debug_all(client, message):
    print(f"[DEBUG] Message from {message.from_user.id}: {message.text}")





if __name__ == "__main__":
    print("Bot is running...")
    app.run()