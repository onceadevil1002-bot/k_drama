from gc import callbacks
from datetime import datetime, timedelta
import os
import json
import dns.resolver
import re
import time
from pymongo import MongoClient
from dotenv import load_dotenv
from pyrogram.client import Client
from pyrogram import filters
from pyrogram.enums import ChatMemberStatus, ChatType, ParseMode  # Add ParseMode import
from pyrogram.errors import UserNotParticipant
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
    CallbackQuery,
)
import asyncio
from urllib.parse import unquote, quote
import logging
from functools import wraps
# --- Add this near the imports ---
from flask import Flask
import threading

# Create Flask app for dummy web server
web_app = Flask(__name__)

@web_app.route("/")
def index():
    return "✅ K-Drama Bot is running", 200

def run_flask():
    # Bind to 0.0.0.0 so Render/Heroku can access it
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
# --- lightweight logger (non-blocking) ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


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
app = Client(
    "kdrama_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=ParseMode.MARKDOWN,
    sleep_threshold=5
)

# === REQUIRED CHANNELS FOR VERIFICATION ===
REQUIRED_CHANNELS = ["-1002648019848"]

# === MongoDB Connection ===
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("MONGO_URI not set in environment variables.")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=40000, connectTimeoutMS=4000)
db = client['kdrama']
collection = db['shows']
user_verification_collection = db['user_verification']

# --- ensure indexes for faster lookups (safe if already present) ---
try:
    collection.create_index([("category", 1), ("show_name", 1)], unique=True)
    user_verification_collection.create_index("user_id", unique=True)
except Exception as e:
    logger.warning("Index creation warning: %s", e)


REPORTS = {}
reply_waiting = {}
poster_upload_state = {}
upload_state = {}
async def auto_delete_message(msg, delay: int = 180):
    """Run in background to delete a sent message after `delay` seconds."""
    try:
        await asyncio.sleep(delay)
        await msg.delete()
    except Exception as e:
        logger.debug("auto_delete failed: %s", e)

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Hindi Dubbed", callback_data="category_hindi")],
        [InlineKeyboardButton("📂 Japanese Drama", callback_data="category_japanese")],
        [InlineKeyboardButton("📂 C Drama", callback_data="category_c_drama")],
        [InlineKeyboardButton("📂 Arabic", callback_data="category_arabic")],
        [InlineKeyboardButton("🌍 Regional", callback_data="category_regional")],
        [InlineKeyboardButton("❗️ Report For Show/Episodes", callback_data="report")]
    ])

# === USER VERIFICATION TRACKING ===
def get_user_verification_status(user_id):
    """Check if user needs reverification (48 hours)"""
    doc = user_verification_collection.find_one({"user_id": user_id})
    if not doc:
        return False
    
    last_verified = doc.get("last_verified")
    if not last_verified:
        return False
    
    # Check if 48 hours have passed
    time_diff = datetime.now() - last_verified
    return time_diff.total_seconds() < (48 * 60 * 60)  # 48 hours in seconds

def update_user_verification(user_id):
    """Update user verification timestamp"""
    user_verification_collection.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "last_verified": datetime.now()}},
        upsert=True
    )

# === MONITORING FUNCTIONS (Silent - No Responses) ===
def monitor_group_activity(message):
    """Monitor group activity without responding"""
    chat = message.chat
    user_id = message.from_user.id if message.from_user else None
    
    if not user_id:
        return
    
    # Log activity based on chat type
    if chat.type == ChatType.SUPERGROUP:
        # Check if it's a discussion-like group
        if chat.title and any(keyword in chat.title.lower() for keyword in ["discussion", "chat", "talk", "kdrama", "drama"]):
            print(f"[DISCUSSION-GROUP] User {user_id} in {chat.title}: {message.text[:30] if message.text else 'media'}...")
        else:
            print(f"[SUPERGROUP] User {user_id} in {chat.title}")
    elif chat.type == ChatType.GROUP:
        print(f"[GROUP] User {user_id} in {chat.title or 'Unknown Group'}")
    elif chat.type == ChatType.CHANNEL:
        print(f"[CHANNEL] Activity in {chat.title or 'Unknown Channel'}")
    
    # Check verification status (but don't respond in group)
    if not get_user_verification_status(user_id):
        print(f"[UNVERIFIED-USER] User {user_id} in group needs verification")

# === GROUP/CHANNEL MESSAGE MONITOR (NO RESPONSES) ===
@app.on_message(~filters.private)
async def monitor_non_private_messages(client, message):
    """
    Monitor all group/channel messages but NEVER respond
    This handler catches all non-private messages first
    """
    monitor_group_activity(message)
    # Never send any response - just return silently
    return

# === YOUR EXISTING VERIFICATION LOGIC ===
async def check_user_membership(client, user_id):
    """Check if user is member of required channels with better error handling"""
    missing = []
    
    for ch in REQUIRED_CHANNELS:
        try:
            # First try to get the chat
            chat = await client.get_chat(ch)
            
            # Then check membership
            member = await client.get_chat_member(chat.id, user_id)
            status = getattr(member, "status", None)
            
            if status not in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
                missing.append(chat)
                
        except UserNotParticipant:
            # User is not a participant
            try:
                chat = await client.get_chat(ch)
                missing.append(chat)
            except Exception as e:
                print(f"[JOIN-CHECK] Could not get chat info for {ch}: {e}")
                # Create a dummy chat object with channel info
                missing.append(type('Chat', (), {
                    'id': ch,
                    'title': 'Required Channel',
                    'username': None
                })())
                
        except Exception as e:
            error_msg = str(e).lower()
            print(f"[JOIN-CHECK] Error checking {ch}: {e}")
            
            if "peer_id_invalid" in error_msg:
                # Bot hasn't encountered this chat yet
                try:
                    # Try to resolve using username if available
                    if ch == "-1002648019848":
                        chat = await client.get_chat("@KDRAMAAVIL")
                    elif ch == "-1002874700170":
                        chat = await client.get_chat("@akddrama20")
                    else:
                        raise Exception("Unknown channel")
                    
                    # Now try membership check again
                    member = await client.get_chat_member(chat.id, user_id)
                    status = getattr(member, "status", None)
                    
                    if status not in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
                        missing.append(chat)
                        
                except UserNotParticipant:
                    # User is not a participant
                    missing.append(chat if 'chat' in locals() else type('Chat', (), {
                        'id': ch,
                        'title': 'Required Channel',
                        'username': 'KDRAMAAVIL' if ch == "-1002648019848" else 'akddrama20'
                    })())
                    
                except Exception as e2:
                    print(f"[JOIN-CHECK] Fallback failed for {ch}: {e2}")
                    # Add as missing with basic info
                    missing.append(type('Chat', (), {
                        'id': ch,
                        'title': 'K-Drama Channel' if ch == "-1002648019848" else 'AKD Drama Channel',
                        'username': 'KDRAMAAVIL' if ch == "-1002648019848" else 'akddrama20'
                    })())
            else:
                # Other error, assume user needs to join
                missing.append(type('Chat', (), {
                    'id': ch,
                    'title': 'Required Channel',
                    'username': None
                })())
    
    return missing
# === YOUR EXISTING FUNCTIONS (Keep these unchanged) ===
def load_data():
    data = {}
    for doc in collection.find():
        category = doc.get("category", "Hindi Dubbed")
        show_name = doc["show_name"]
        episodes = doc.get("episodes", {})
        poster = doc.get("poster", [])

        if category not in data:
            data[category] = {}

        if isinstance(episodes, dict):
            episodes["poster"] = poster
            data[category][show_name] = episodes
        else:
            data[category][show_name] = {"episodes": episodes, "poster": poster}

    return data
# --- simple TTL cache for load_data to reduce Mongo reads from button spam ---
_cache = {"data": None, "ts": 0, "ttl": 10}  # ttl seconds

def load_data_cached():
    now = time.time()
    if _cache["data"] is None or (now - _cache["ts"]) > _cache["ttl"]:
        _cache["data"] = load_data()
        _cache["ts"] = now
    return _cache["data"]

def clear_data_cache():
    _cache["data"] = None
    _cache["ts"] = 0


def backup_database():
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_dir = "backups"
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, f"mongo_backup_{now}.json")

    data = list(collection.find())
    for item in data:
        item["_id"] = str(item["_id"])

    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Database backed up to: {backup_path}")

def save_data(data):
    for category, shows in data.items():
        for show_name, show_data in shows.items():
            poster = show_data.get("poster", [])
            episodes = {k: v for k, v in show_data.items() if k != "poster"}
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
            clear_data_cache()


def migrate_category():
    collection.update_many({"category": {"$exists": False}}, {"$set": {"category": "Hindi Dubbed"}})

def find_show_category(show_name_raw, data):
    """
    Search for a show across all categories in the data.
    Returns (category, show_key) if found, or (None, None) if not found.
    """
    show_name_clean = show_name_raw.lower().replace("_", " ").strip()

    for category, shows in data.items():
        for show_key in shows.keys():
            show_key_clean = show_key.lower().replace("_", " ").strip()
            if show_key_clean == show_name_clean:
                return category, show_key

    return None, None

migrate_category()

# === PRIVATE MESSAGE HANDLERS (These will work normally) ===

# Just the key changes you need to make:

# 1. Move this function definition right after your imports and before the start handler:



@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_id = message.from_user.id
    data = load_data_cached()
    args = message.text.split()
    slug = args[1] if len(args) > 1 else None

    # Check if user is verified within last 48 hours
    if get_user_verification_status(user_id):
        # Handle slug for verified users
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
            update_user_verification(user_id)
            return await message.reply(title, reply_markup=InlineKeyboardMarkup(buttons))

        # No slug, show main menu for verified users
        return await message.reply(
            "🎬 Welcome back! Choose a category:",
            reply_markup=main_keyboard()
        )

    # User needs verification (first time or 48 hours passed)
    keyboard = [
        [InlineKeyboardButton("Join Channel 1", url="https://t.me/KDRAMAAVIL")],
        [InlineKeyboardButton("Join Channel 2", url="https://t.me/AKDDRAMA20")],
        [InlineKeyboardButton("I Joined Both", callback_data="joined")]
    ]
    return await message.reply("Please join both channels to use the bot.",
                               reply_markup=InlineKeyboardMarkup(keyboard))

# 3. Update your joined_channel handler:


@app.on_callback_query(filters.regex("^joined$"))
async def joined_channel(client, callback: CallbackQuery):
    user_id = callback.from_user.id
    missing = await check_user_membership(client, user_id)

    if missing:
        lines = ["You still need to join these channels:"]
        for m in missing:
            if hasattr(m, "username") and m.username:
                lines.append(f"👉 https://t.me/{m.username} ({m.title})")
            elif hasattr(m, "title"):
                lines.append(f"👉 🔒 {m.title} (private)")
            else:
                lines.append(f"👉 {m}")
        return await callback.message.edit("\n".join(lines))

    # Verified - update verification timestamp
    update_user_verification(user_id)

    await callback.message.edit(
        "🎬 Welcome to K-Drama Bot! Choose a category:\n😉please Join to Main channel we will Grow'😙", 
        reply_markup=main_keyboard()
    )


# Add all your other existing handlers here (categories, episodes, admin commands, etc.)
# They will work normally in private chats

@app.on_callback_query(filters.regex("^category_hindi$"))
async def category_hindi(client, callback):
    data = load_data_cached()
    if "Hindi Dubbed" not in data:
        return await callback.answer("No shows found.")
    buttons = [
        [InlineKeyboardButton(f"🎞 {show}", callback_data=f"show_Hindi Dubbed_{show}")]
        for show in sorted(data["Hindi Dubbed"])
    ]
    await callback.message.edit("🎞 Hindi Dubbed Shows:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex("^category_regional$"))
async def category_regional(client, callback):
    data = load_data_cached()
    if "Regional" not in data:
        return await callback.answer("No shows found.")
    buttons = [
        [InlineKeyboardButton(f"🌍 {show}", callback_data=f"show_Regional_{show}")]
        for show in sorted(data["Regional"])
    ]
    await callback.message.edit("🌍 Regional Shows:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex("^category_"))
async def category_handler(client, callback_query):
    category_code = callback_query.data.split("_", 1)[1]
    
    category_map = {
        "japanese": "Japanese Drama",
        "c_drama": "C Drama",
        "arabic": "Arabic"
    }
    matched_category = category_map.get(category_code)
    if not matched_category:
        return await callback_query.answer("Unknown category.", show_alert=True)

    data = load_data_cached()

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


# === ALL ADMIN COMMANDS REMAIN UNCHANGED ===
@app.on_message(filters.command(["add", "add_hindi", "add_regional", "add_jap", "add_c", "add_arb"]) & filters.user(ADMIN_ID) & filters.private)
async def add_show(client, message):
    cmd = message.command[0]
    if len(message.command) < 2:
        return await message.reply(
            "Usage:\n"
            "/add Show Name\n"
            "/add Show Name > Category\n"
            "/add Show Name 1\n"
            "/add Show Name > Category 1\n"
            "/add_jap Show Name\n"
            "/add_c Show Name\n"
            "/add_arb Show Name"
        )

    args = message.text.split(" ", 1)[1].strip()
    category = "Hindi Dubbed"
    show_name = None
    season_number = None

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
            return await message.reply("Invalid format. Use /add Show Name > Category 1")
    else:
        parts = args.rsplit(" ", 1)
        if parts[-1].isdigit():
            show_name = parts[0].strip()
            season_number = parts[1].strip()
        else:
            show_name = args.strip()

    data = load_data_cached()

    if category not in data:
        data[category] = {}

    if show_name not in data[category]:
        data[category][show_name] = {}
        data[category][show_name]["poster"] = []
        await message.reply(f"Added show: *{show_name}* under *{category}*")

    if season_number:
        if season_number in data[category][show_name]:
            return await message.reply("Season already exists.")
        data[category][show_name][season_number] = []
        await message.reply(f"Added *Season {season_number}* under *{show_name}*")
    
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

CATEGORY_ALIASES = {
    "Hindi Dubbed": "Hindi Dubbed",
    "Regional": "Regional",
    "Japanese Drama": "jap",
    "Japanese": "jap",
    "jap": "jap",
    "C Drama": "c",
    "Chinese": "c",
    "c": "c",
    "Arabic": "arb",
    "Arabic Drama": "arb",
    "arb": "arb"
}

POSTER_CATEGORY_COMMANDS = {
    "add_poster": "Hindi Dubbed",
    "add_poster_regional": "Regional",
    "add_poster_jap": "Japanese Drama",
    "add_poster_c": "C Drama",
    "add_poster_arb": "Arabic Drama"
}

for cmd, category in POSTER_CATEGORY_COMMANDS.items():
    @app.on_message(filters.command(cmd) & filters.user(ADMIN_ID) & filters.private)
    async def add_poster_command(client, message, category=category):
        try:
            show_name = message.text.split(" ", 1)[1].strip()
        except IndexError:
            return await message.reply("Usage: /{} Show Name".format(cmd))

        data = load_data_cached()
        if category not in data or show_name not in data[category]:
            return await message.reply(f"Show *{show_name}* not found under *{category}*.")

        poster_upload_state[message.from_user.id] = {
            "show_name": show_name,
            "category": category,
            "file_ids": [],
            "deadline": time.time() + 60
        }

        await message.reply("Send 1–6 poster screenshots now (within 60 seconds)...")

@app.on_message(filters.photo & filters.user(ADMIN_ID) & filters.private)
async def collect_poster(client, message):
    user_id = message.from_user.id
    state = poster_upload_state.get(user_id)

    if not state:
        return

    if time.time() > state["deadline"]:
        del poster_upload_state[user_id]
        return await message.reply("Time expired! Please run /add_poster again.")

    if len(state["file_ids"]) >= 6:
        return await message.reply("Max 6 posters allowed.")

    file_id = message.photo.file_id
    state["file_ids"].append(file_id)

    if len(state["file_ids"]) >= 1:
        await finalize_poster_upload(client, message)

@app.on_message(filters.command("done") & filters.user(ADMIN_ID) & filters.private)
async def manual_done_poster(client, message):
    user_id = message.from_user.id
    if user_id in poster_upload_state:
        await finalize_poster_upload(client, message)

async def finalize_poster_upload(client, message):
    user_id = message.from_user.id
    state = poster_upload_state.pop(user_id, None)

    if not state or not state["file_ids"]:
        return await message.reply("No posters received.")

    result = collection.update_one(
        {"show_name": state["show_name"], "category": state["category"]},
        {"$set": {"poster": state["file_ids"]}}
    )
    clear_data_cache()


    if result.matched_count > 0:
        await message.reply(f"Poster uploaded for *{state['show_name']}*.")
    else:
        await message.reply("Show not found or poster not saved.")

@app.on_message(filters.command([
    "upload", "upload_hindi", "upload_regional",
    "upload_jap", "upload_c", "upload_arb"
]) & filters.user(ADMIN_ID) & filters.private)
async def upload_handler(client, message: Message):
    cmd = message.command[0]
    if len(message.command) < 2:
        return await message.reply("Usage:\n"
                                   "/upload Show Name\n"
                                   "/upload Show Name 1\n"
                                   "/upload Show Name > Category\n"
                                   "/upload Show Name > Category 2")

    args = message.text.split(" ", 1)[1].strip()
    data = load_data_cached()

    category = "Hindi Dubbed"
    show_name = None
    season_number = None

    cmd_category_map = {
        "upload_hindi": "Hindi Dubbed",
        "upload_regional": "Regional",
        "upload_jap": "Japanese Drama",
        "upload_c": "C Drama",
        "upload_arb": "Arabic"
    }
    if cmd in cmd_category_map:
        category = cmd_category_map[cmd]

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
            return await message.reply("Invalid format. Use /upload Show Name > Category 1")
    else:
        parts = args.rsplit(" ", 1)
        if parts[-1].isdigit():
            show_name = parts[0].strip()
            season_number = parts[1].strip()
        else:
            show_name = args.strip()

    if category not in data or show_name not in data[category]:
        return await message.reply("Show not found. Use /add command first.")

    if season_number:
        if season_number not in data[category][show_name]:
            return await message.reply("Season not found. Use /add to create it first.")
        episodes = data[category][show_name][season_number]
        if episodes and isinstance(episodes[-1], list) and None in episodes[-1]:
            return await message.reply("Last episode appears split and incomplete. Use /upload_split to upload parts safely.")
        upload_state[message.from_user.id] = {
            "show": show_name,
            "season": season_number,
            "category": category
        }

        if is_valid_url(message.text.strip()):
            data = load_data_cached()
            episodes = data[category][show_name][season_number] if season_number else data[category][show_name]["episodes"]

            episodes.append({
                "type": "link",
                "content": message.text.strip()
            })
            backup_database()
            save_data(data)
            return await message.reply("Link episode saved successfully.")

        return await message.reply(f"Send videos now for *{show_name}* Season *{season_number}*", quote=True)
    
    else:
        flat_eps = data[category][show_name]["episodes"]
        if flat_eps and isinstance(flat_eps[-1], list):
            return await message.reply("Last episode appears split. Use /upload_split to upload parts safely.")
        
        upload_state[message.from_user.id] = {
            "show": show_name,
            "season": None,
            "category": category
        }
        return await message.reply(f"Send videos now for *{show_name}*", quote=True)

@app.on_message(filters.video & filters.user(ADMIN_ID) & filters.private)
async def handle_video(client, message: Message):
    user_id = message.from_user.id
    if user_id not in upload_state:
        return await message.reply("First use /upload ShowName or /upload ShowName SeasonNumber")

    state = upload_state[user_id]
    show_name = state["show"]
    season_number = state["season"]
    category = state["category"]

    try:
        fwd = await message.forward(STORAGE_CHANNEL_ID, disable_notification=True)
        file_id = fwd.video.file_id
        await fwd.delete()

        data = load_data_cached()

                # --- Split Upload Mode ---
        if state.get("mode") == "upload_split_part":
            category = state["category"]
            show = state["show"]
            season = state["season"]
            idx = state["index"]
            part = state["part"]

            episodes = data[category][show][season]
            if not isinstance(episodes[idx], list):
                upload_state.pop(user_id, None)
                return await message.reply("❌ This episode is no longer a split.")

            while len(episodes[idx]) < 2:
                episodes[idx].append(None)

            episodes[idx][part] = file_id
            backup_database()
            save_data(data)
            upload_state.pop(user_id, None)
            return await message.reply(f"✅ Uploaded Part {part+1} for {show} S{season} Ep{idx+1}.")


    # Special mode: upload into a split episode part
        if state and state.get("mode") == "upload_split_part":
            try:
                fwd = await message.forward(STORAGE_CHANNEL_ID, disable_notification=True)
                if not fwd.video:
                    return await message.reply("❌ Only videos allowed in split episodes.")
                file_id = fwd.video.file_id
                try:
                    await fwd.delete()
                except:
                    pass

                category = state["category"]
                show = state["show"]
                season = state["season"]
                idx = state["index"]
                part = state["part"]

                data = load_data_cached()
                episodes = data[category][show][season]
                if not isinstance(episodes[idx], list):
                    return await message.reply("❌ Target is no longer split.")

                while len(episodes[idx]) <= part:
                    episodes[idx].append(None)

                episodes[idx][part] = file_id
                backup_database()
                save_data(data)
                upload_state.pop(user_id, None)

                await message.reply(f"✅ Uploaded video into {show} S{season} Ep{idx+1} Part {part+1}.")
                return
            except Exception as e:
                logger.exception("handle_video upload_split_part error: %s", e)
                return await message.reply("Error uploading split part.")

        
        if season_number:
            data[category][show_name].setdefault(season_number, []).append({
                "type": "video",
                "content": file_id
            })
        else:
            data[category][show_name].setdefault("episodes", []).append({
                "type": "video",
                "content": file_id
            })


        backup_database()
        save_data(data)

        await message.reply(
            f"Uploaded and saved for *{show_name}*{' Season ' + season_number if season_number else ''}.",
            quote=True
        )

    except Exception as e:
        print("Upload error:", e)
        await message.reply("Failed to upload. Please check bot permissions or channel ID.")
@app.on_message(filters.command(["upload_part"]) & filters.user(ADMIN_ID) & filters.private)
async def upload_part_command(client, message: Message):
    """
    Upload into a split episode (works across all categories).
    Usage: /upload_part Show Name Season Episode Part
    Example: /upload_part Ghost Train 1 2 2
    """
    try:
        tokens = message.text.split()[1:]
        if len(tokens) < 4:
            return await message.reply("Usage: /upload_part ShowName Season Episode Part")

        season_number = tokens[-3]
        episode_num = int(tokens[-2])
        part_no = int(tokens[-1])
        show_name_raw = " ".join(tokens[:-3]).strip()

        ep_index = episode_num - 1
        part_index = part_no - 1
        if part_index not in (0, 1):
            return await message.reply("❌ Only parts 1 or 2 are allowed.")

        data = load_data_cached()

        # find show across all categories
        found_category, found_show = None, None
        for category, shows in data.items():
            for show_key in shows.keys():
                if show_key.lower().replace("_", " ") == show_name_raw.lower().replace("_", " "):
                    found_category, found_show = category, show_key
                    break
            if found_category:
                break

        if not found_category:
            return await message.reply("❌ Show not found in any category.")

        season_list = data[found_category][found_show].setdefault(season_number, [])
        if ep_index < 0 or ep_index >= len(season_list):
            return await message.reply("❌ Episode not found. Upload episode first.")

        entry = season_list[ep_index]
        if not isinstance(entry, list):
            return await message.reply("❌ Target episode is not split. Use /split first.")

        # make sure exactly 2 slots
        while len(entry) < 2:
            entry.append(None)

        if entry[part_index] is not None:
            return await message.reply("⚠️ That part already has a video.")

        # save state so next video goes into the right slot
        upload_state[message.from_user.id] = {
            "mode": "upload_split_part",
            "category": found_category,
            "show": found_show,
            "season": season_number,
            "index": ep_index,
            "part": part_index,
        }
        return await message.reply(
            f"📥 Ready to upload Part {part_no} for {found_show} (S{season_number} Ep{episode_num}). Send video now."
        )

    except Exception as e:
        logger.exception("upload_part_command error: %s", e)
        await message.reply("Error preparing split upload.")



# === Continue with all other existing handlers but add filters.private ===


@app.on_callback_query(filters.regex("^season_(.+?)_(.+?)_(.+)$"))
async def season_menu(client, callback_query):
    category, show_name, season_number = callback_query.data.split("_", 3)[1:]
    data = load_data_cached()

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
    data = load_data_cached()

    if show_name not in data.get(category, {}):
        await callback_query.answer("Show not found.")
        return

    buttons = []
    episodes = data[category][show_name]

    if "episodes" in episodes:
        for idx, ep in enumerate(episodes["episodes"], start=1):
            if isinstance(ep, list):
                buttons.append([
                    InlineKeyboardButton(
                        f"📂 Episode {idx} (split)",
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

    for season in episodes:
        if season not in ["episodes", "poster"]:
            buttons.append([
                InlineKeyboardButton(
                    f"📂 Season {season}",
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

@app.on_message(filters.photo & filters.user(ADMIN_ID) & filters.private)
async def handle_poster_photo(client, message: Message):
    user_id = message.from_user.id
    if user_id not in poster_upload_state:
        return
    
    state = poster_upload_state[user_id]
    
    if len(state["file_ids"]) >= 6:
        return await message.reply("Maximum 6 posters allowed. Use /add_poster again if needed.")
    
    try:
        fwd = await message.forward(STORAGE_CHANNEL_ID, disable_notification=True)
        state["file_ids"].append(fwd.photo.file_id)
        await fwd.delete()
        
        remaining = 6 - len(state["file_ids"])
        if remaining > 0:
            await message.reply(f"Poster received ({len(state['file_ids'])}/6). Send more or wait to auto-finalize.")
        else:
            await finalize_poster_upload(client, message)
            
    except Exception as e:
        print("Poster upload error:", e)
        await message.reply("Failed to process poster.")

@app.on_message(filters.command(["split_hindi", "split_regional", "split_jap", "split_c", "split_arb"]) & filters.user(ADMIN_ID) & filters.private)
async def split_episode_command(client, message: Message):
    """
    Convert an existing single video episode into a split (max 2 parts).
    Usage examples:
      /split_hindi My Show 1/5      → Season 1 Episode 5
      /split_regional My Show 2/7   → Season 2 Episode 7
    """
    try:
        cmd = message.command[0]
        if len(message.command) < 2 or "/" not in message.text:
            return await message.reply("Usage:\n/split_hindi ShowName Season/Episode")

        args = message.text.split(" ", 1)[1].strip()
        left, episode_str = args.rsplit("/", 1)
        episode_num = int(episode_str.strip())
        ep_index = episode_num - 1

        # detect season vs no season
        tokens = left.strip().split()
        if tokens and tokens[-1].isdigit():
            season_number = tokens[-1]
            show_name = " ".join(tokens[:-1]).strip()
        else:
            season_number = "1"
            show_name = " ".join(tokens).strip()

        # map category
        cmd_category_map = {
            "split_hindi": "Hindi Dubbed",
            "split_regional": "Regional",
            "split_jap": "Japanese Drama",
            "split_c": "C Drama",
            "split_arb": "Arabic"
        }
        category = cmd_category_map.get(cmd, "Hindi Dubbed")

        data = load_data_cached()
        if category not in data or show_name not in data[category]:
            return await message.reply("❌ Show not found in database.")

        season_list = data[category][show_name].setdefault(season_number, [])
        if ep_index < 0 or ep_index >= len(season_list):
            return await message.reply("❌ Episode not found. Upload it first before splitting.")

        entry = season_list[ep_index]

        # --- handle different formats ---
        if isinstance(entry, dict) and entry.get("type") == "link":
            return await message.reply("❌ Links cannot be split. Use /upload link normally.")

        if isinstance(entry, dict) and entry.get("type") == "video":
            season_list[ep_index] = [entry["content"], None]
            backup_database()
            save_data(data)
            return await message.reply(f"✅ Converted Ep{episode_num} into split (slot for Part 2 ready). Use /upload_part.")
        
        elif isinstance(entry, str):  # old raw file_id style
            season_list[ep_index] = [entry, None]
            backup_database()
            save_data(data)
            return await message.reply(f"✅ Converted Ep{episode_num} into split (slot for Part 2 ready). Use /upload_part.")

        elif isinstance(entry, list):
            if len(entry) >= 2:
                return await message.reply("⚠️ Only 2 parts allowed per episode.")
            if entry[1] is not None:
                return await message.reply("⚠️ Part 2 already uploaded.")
            return await message.reply(f"Slot already prepared for Part 2 in Ep{episode_num}. Use /upload_part.")

        else:
            return await message.reply("❌ Unsupported episode format. Try re-uploading the episode.")
    except Exception as e:
        logger.exception("split_episode_command error: %s", e)
        await message.reply("Error while splitting episode.")


    
@app.on_message(filters.command(["upload_split_hindi", "upload_split_regional", "upload_split_jap", "upload_split_c", "upload_split_arb"]) & filters.user(ADMIN_ID) & filters.private)
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
        category = cmd_category_map.get(cmd)
        if not category:
            return await message.reply("Unknown split upload command.")

        if len(message.command) < 2:
            return await message.reply("Usage:\n/upload_split_hindi Show Season Episode Part")

        args = message.text.split(" ", 1)[1].strip().split()
        if len(args) < 4:
            return await message.reply("Format error. Provide Show, Season, Episode, Part")

        show_name_raw = " ".join(args[:-3]).strip()
        season = args[-3]
        episode = int(args[-2])
        part_index = int(args[-1]) - 1

        if part_index not in (0, 1):
            return await message.reply("Part must be 1 or 2")

        data = load_data_cached()

        # Try to match in the given category first, otherwise search across categories
        if category not in data or show_name_raw not in data[category]:
            found_cat, found_key = find_show_category(show_name_raw, data)
            if found_cat:
                category = found_cat
                show_key = found_key
            else:
                return await message.reply("Show not found.")
        else:
            show_key = show_name_raw

        if season not in data[category][show_key]:
            return await message.reply("Season not found.")

        episodes = data[category][show_key][season]
        if episode < 1 or episode > len(episodes):
            return await message.reply("Episode index out of range.")
        if not isinstance(episodes[episode - 1], list):
            return await message.reply("Episode not marked as split. Use /split_* first.")

        # set state in unified shape
        upload_state[message.from_user.id] = {
            "mode": "upload_split_part",
            "category": category,
            "show": show_key,
            "season": season,
            "index": episode - 1,
            "part": part_index
        }

        return await message.reply(
            f"📥 Send video for *{show_key}* S{season} Ep {episode} Part {part_index + 1}"
        )

    except Exception as e:
        logger.exception("[upload_split_handler] error: %s", e)
        return await message.reply("Failed to process upload split.")


@app.on_callback_query(filters.regex("^noop$"))
async def do_nothing(client, callback_query: CallbackQuery):
    await callback_query.answer("Nothing to show here.")

@app.on_callback_query(filters.regex("^multi_"))
async def split_parts_view(client, callback_query):
    try:
        _, category, show_name, key, index = callback_query.data.split("_", 4)
        index = int(index) - 1
        data = load_data_cached()

        if show_name not in data[category] or key not in data[category][show_name]:
            return await callback_query.answer("Not found.")

        parts = data[category][show_name][key][index]
        if not isinstance(parts, list):
            return await callback_query.answer("Not a split episode.")

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
        await callback_query.answer("Error opening split episode.")

@app.on_message(filters.command("list") & filters.user(ADMIN_ID) & filters.private)
async def list_content(client, message):
    data = load_data_cached()
    output = "Available Shows:\n"
    for category in data:
        output += f"- {category}\n"
        for show_name, seasons in data[category].items():
            output += f"  - {show_name}\n"
            if "episodes" in seasons:
                output += "    - Episodes\n"
            for season_number, episodes in seasons.items():
                if season_number not in ["episodes", "poster"]:
                    output += f"    - Season {season_number}\n"
    await message.reply(output)

@app.on_message(filters.command("get_links") & filters.user(ADMIN_ID) & filters.private)
async def get_links(client, message):
    data = load_data_cached()
    if not data:
        return await message.reply("No shows found.")

    bot_username = (await client.get_me()).username
    links = []
    added = set()

    for category in data:
        for show_name, content in data[category].items():
            if not isinstance(content, dict):
                continue

            if show_name in added:
                continue

            slug = f"{category.replace(' ', '_')}__{show_name.replace(' ', '_')}"
            link = f"https://t.me/{bot_username}?start={slug}"
            links.append(f"[{show_name} ({category})]({link})")
            added.add(show_name)

    await message.reply(
        "**Direct Show Links:**\n\n" + "\n".join(links),
        disable_web_page_preview=True
    )

@app.on_message(filters.command("help") & filters.private)
async def help_command(client, message):
    is_admin = message.from_user.id == ADMIN_ID

    admin_help = """
ADMIN COMMANDS PANEL

ADD SHOWS/SEASONS
Hindi Dubbed:
  /add_hindi Show Name
  /add Show Name > Hindi Dubbed

Regional:
  /add_regional Show Name
  /add Show Name > Regional

Japanese Drama:
  /add_jap Show Name
  /add Show Name > Japanese Drama

C Drama:
  /add_c Show Name
  /add Show Name > C Drama

Arabic:
  /add_arb Show Name
  /add Show Name > Arabic

Poster Upload Commands:
 /add_poster Show Name— Hindi Dubbed
 /add_poster_regional Show Name— Regional
 /add_poster_jap Show Name— Japanese
 /add_poster_c Show Name— Chinese
 /add_poster_arb Show Name— Arabic

UPLOAD VIDEOS
Hindi Dubbed:
  /upload_hindi Show Name
  /upload Show Name > Hindi Dubbed

Regional:
  /upload_regional Show Name
  /upload Show Name > Regional

Japanese Drama:
  /upload_jap Show Name
  /upload Show Name > Japanese Drama

C Drama:
  /upload_c Show Name
  /upload Show Name > C Drama

Arabic:
  /upload_arb Show Name
  /upload Show Name > Arabic

Split Episodes (max 2 parts):
  /split_hindi ShowName Season/Episode
  /split_jap ShowName Season/Episode
  /split_c ShowName Season/Episode
  /split_arb ShowName Season/Episode
Upload Split Part:
  /upload_part ShowName Season EpNo 2   → then send second video


Delete Commands:
Hindi Dubbed
/delete Show Name
/delete Show Name Season
/delete Show Name Season /Episode

Regional
/delete_regional Show Name
/delete_regional Show Name Season
/delete_regional Show Name Season /Episode

Japanese Drama
/delete_jap Show Name
/delete_jap Show Name Season
/delete_jap Show Name Season /Episode

C Drama
/delete_c Show Name
/delete_c Show Name Season
/delete_c Show Name Season /Episode

Arabic
/delete_arb Show Name
/delete_arb Show Name Season
/delete_arb Show Name Season /Episode

UTILITIES
  /get_links — Get shareable links
  /list — Display all shows & structure
  /test_forward — Check forwarding
  /report — Handle user reports
"""

    user_help = """
USER COMMANDS

- Use buttons to browse and stream shows
- Videos auto-delete after 3 minutes
- Use /report to request missing episodes
- Updates: Join Channel
"""

    await message.reply(admin_help if is_admin else user_help, disable_web_page_preview=True)

@app.on_message(filters.command([
    "delete", "delete_regional", "delete_jap", "delete_c", "delete_arb"
]) & filters.user(ADMIN_ID) & filters.private)
async def delete_content(client, message: Message):
    try:
        cmd = message.command[0]
        if len(message.command) < 2:
            return await message.reply("Usage:\n/delete Show\n/delete Show Season\n/delete Show /Episode\n/delete Show Season /Episode")

        args = message.text.split(" ", 1)[1].strip()

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

        cmd_category_map = {
            "delete": "Hindi Dubbed",
            "delete_regional": "Regional",
            "delete_jap": "Japanese Drama",
            "delete_c": "C Drama",
            "delete_arb": "Arabic Drama"
        }
        raw_category = cmd_category_map.get(cmd, "Hindi Dubbed")
        category = CATEGORY_ALIASES.get(raw_category, raw_category)
        data = load_data_cached()

        if show_name not in data.get(category, {}):
            return await message.reply(f"Show not found in *{category}*")

        if season is None and episode_index is None:
            del data[category][show_name]
            collection.delete_one({"category": category, "show_name": show_name})
            backup_database()
            save_data(data)
            return await message.reply(f"Deleted *{show_name}* from *{category}*")

        if season and episode_index is None:
            if season not in data[category][show_name]:
                return await message.reply("Season not found.")

            del data[category][show_name][season]
            collection.update_one(
                {"category": category, "show_name": show_name},
                {"$unset": {f"episodes.{season}": ""}}
            )
            backup_database()
            save_data(data)
            return await message.reply(f"Deleted *Season {season}* from *{show_name}*")

        key = season if season else "episodes"
        if key not in data[category][show_name]:
            return await message.reply("Season or episode list not found.")

        episodes = data[category][show_name][key]
        if not isinstance(episodes, list):
            return await message.reply("This season/section doesn't contain a list of episodes.")

        if episode_index < 0 or episode_index >= len(episodes):
            return await message.reply("Episode index out of range.")

        del episodes[episode_index]
        collection.update_one(
            {"category": category, "show_name": show_name},
            {"$set": {f"episodes.{key}": episodes}}
        )
        backup_database()
        save_data(data)
        return await message.reply(f"Deleted Episode {episode_index + 1} from *{show_name}* {f'Season {season}' if season else ''}")
    except Exception as e:
        print("Delete Error:", e)
        return await message.reply("Something went wrong while deleting.")

@app.on_callback_query(filters.regex("^episode_"))
async def send_episode(client, callback_query: CallbackQuery):
    try:
        payload = callback_query.data[len("episode_"):]
        parts = payload.split("_")

        if len(parts) < 4:
            return await callback_query.answer("Invalid episode data.")

        category = parts[0]
        index_str = parts[-1]
        season_or_key = parts[-2]
        show_name_parts = parts[1:-2]
        show_name = "_".join(show_name_parts)
        index = int(index_str) - 1
        data = load_data_cached()

        if show_name not in data.get(category, {}):
            return await callback_query.answer("Show not found.")

        if season_or_key not in data[category][show_name]:
            return await callback_query.answer("Season or episode list not found.")
        
        if season_or_key == "poster":
            return await callback_query.answer("Poster is not an episode.")

        episode_list = data[category][show_name][season_or_key]

        if index < 0 or index >= len(episode_list):
            return await callback_query.answer("Episode index out of range.")

        episode_data = episode_list[index]

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
                return await callback_query.answer("Split parts not uploaded yet.")
            return await callback_query.message.edit_text(
                f"🎬 {show_name.replace('_', ' ')} - Episode {index+1} (Split)",
                reply_markup=InlineKeyboardMarkup(buttons)
            )

        if isinstance(episode_data, str):
            episode_data = { "type": "video", "content": episode_data }

        if episode_data["type"] == "video":
            sent_msg = await client.send_video(
                chat_id=callback_query.from_user.id,
                video=episode_data["content"],
                caption=f"🎬 {show_name.replace('_', ' ')} - Episode {index + 1}"
            )
            asyncio.create_task(auto_delete_message(sent_msg, 180))
            await callback_query.answer("Sent video. It will auto-delete in 3 minutes.")


        elif episode_data["type"] == "link":
            await client.send_message(
                chat_id=callback_query.from_user.id,
                text=(
                    f"🎬 {show_name.replace('_', ' ')} - Episode {index + 1}\n"
                    f"Download Link"
                ),
                reply_markup=InlineKeyboardMarkup([[ 
                    InlineKeyboardButton("▶ Watch Episode", url=episode_data["content"])
                ]]),
                disable_web_page_preview=False
            )
            await callback_query.answer("Sent link.")
            return

    except Exception as e:
        print(f"[send_episode] ERROR: {e}")
        await callback_query.answer("Error while sending episode.")

@app.on_callback_query(filters.regex("^splitpart_"))
async def send_split_part(client, callback_query: CallbackQuery):
    try:
        payload = callback_query.data[len("splitpart_"):]
        parts = payload.split("_")
        if len(parts) < 5:
            return await callback_query.answer("Invalid split data.")

        category, *name_parts, season_or_key, ep_index_str, part_index_str = parts
        show_name = "_".join(name_parts)
        ep_index = int(ep_index_str)
        part_index = int(part_index_str)  # must be 0 or 1

        if part_index not in (0, 1):
            return await callback_query.answer("Invalid part index.")

        data = load_data_cached()
        if season_or_key not in data.get(category, {}).get(show_name, {}):
            return await callback_query.answer("Season not found.")

        episodes = data[category][show_name][season_or_key]
        if ep_index < 0 or ep_index >= len(episodes):
            return await callback_query.answer("Episode not found.")

        entry = episodes[ep_index]
        if not isinstance(entry, list) or len(entry) < 2:
            return await callback_query.answer("This is not a split episode.")

        file_id = entry[part_index]
        if not file_id:
            return await callback_query.answer("That part is not uploaded yet.")

        sent_msg = await client.send_video(
            chat_id=callback_query.from_user.id,
            video=file_id,
            caption=f"🎬 {show_name.replace('_',' ')} | S{season_or_key}E{ep_index+1} - Part {part_index+1}"
        )
        asyncio.create_task(auto_delete_message(sent_msg, 180))
        await callback_query.answer("Sent part. Auto-deletes in 3 minutes.")

    except Exception as e:
        logger.exception("send_split_part error: %s", e)
        await callback_query.answer("Could not send split part.")



@app.on_message(filters.command("test_forward") & filters.user(ADMIN_ID) & filters.private)
async def test_forward(client, message):
    try:
        await client.forward_messages(
            chat_id=STORAGE_CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_ids=message.message_id,
            disable_notification=True
        )
        await message.reply("Forwarding successful!")
    except Exception as e:
        await message.reply(f"Error forwarding: {e}")

@app.on_callback_query(filters.regex("^report$"))
async def handle_report(client, callback_query: CallbackQuery):
    await callback_query.message.edit("Please type the name of the missing show/episode.")

@app.on_message(filters.text & ~filters.command(["start", "help", "add", "upload", "delete"]) & ~filters.user(ADMIN_ID) & filters.private)
async def handle_user_report(client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()

    if not text:
        return await message.reply("Empty message, Please Enter your report.")

    try:
        report_msg = await client.send_message(
            ADMIN_ID,
            f"New Report from `{user_id}`:\n\n{message.text}",
            reply_markup=InlineKeyboardMarkup([[ 
                InlineKeyboardButton("Reply", callback_data=f"reply_to_{user_id}")
            ]])
        )
        
        REPORTS[str(report_msg.message_id)] = user_id
        await message.reply("Report sent! The admin will reply soon if needed.")
    
    except Exception as e:
        await message.reply("Failed to send report. Please try again later.")
        print("[handle_users_report] Error:", e)

@app.on_callback_query(filters.regex("^reply_to_"))
async def handle_reply_button(client, callback: CallbackQuery):
    try:
        user_id = int(callback.data.split("_")[2])
        reply_waiting[callback.from_user.id] = user_id
        await callback.message.reply("Send your reply now. It will be forwarded to the user.")
        await callback.answer()
     
    except Exception as e:
        print("[reply_to_] Error:", e)
        await callback.answer("Failed to initiate reply.")

@app.on_message(filters.text & filters.user(ADMIN_ID) & filters.private)
async def handle_admin_messages(client, message: Message):
    user_id = message.from_user.id
    
    # Check if admin is in reply mode
    target_id = reply_waiting.get(user_id)
    if target_id:
        try:
            await client.send_message(
                target_id,
                f"Admin replied to your report:\n\n{message.text}"
            )
            await message.reply("Your reply has been sent to the user.")
            del reply_waiting[user_id]
        except Exception as e:
            await message.reply(f"Failed to send reply: {e}")
        return
    
    # Check if admin is in upload mode and sending links
    if user_id in upload_state:
        url = message.text.strip()
        if is_valid_url(url):
            state = upload_state[user_id]
            show = state["show"]
            season = state["season"]
            category = state["category"]
            
            data = load_data_cached()
            
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
            await message.reply("Link episode saved successfully.")
            return

# Replace the end of your bot.py file with this:
# --- Add this before if __name__ == "__main__": ---
def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()


if __name__ == "__main__":
    keep_alive()   # start dummy server
    logger.info("Bot is running...")
    app.run()