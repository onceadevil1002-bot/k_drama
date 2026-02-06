# pyright: reportUnknownMemberType=false
# pyright: reportOptionalOperand=false
# pyright: reportAttributeAccessIssue=false
# pyright: reportGeneralTypeIssues=false

import os
import sys
import json
import dns.resolver
import re
import time
from datetime import datetime, timedelta
import asyncio
import threading

# Fix for Windows event loop issue on Python 3.10+
if sys.platform == 'win32' and sys.version_info >= (3, 10):
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass

from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

# Load environment variables FIRST
load_dotenv()

from pyrogram.client import Client
from pyrogram import filters
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated
from pyrogram.types import User as PyroUser
from pyrogram.enums import ChatMemberStatus, ChatType, ParseMode
from pyrogram.errors import UserNotParticipant, RPCError
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    InputMediaVideo, InputMediaDocument,
    InlineQueryResultArticle,
    InputTextMessageContent,
    ChatPermissions,
    ForceReply,
)
import threading
from collections import deque, OrderedDict
import signal
from cachetools import TTLCache
import random

# Try to import rapidfuzz for better fuzzy matching
rf_process = None
rf_fuzz = None
try:
    import importlib
    rf_mod = importlib.import_module("rapidfuzz")
    rf_process = rf_mod.process
    rf_fuzz = rf_mod.fuzz
except Exception:
    rf_process = None
    rf_fuzz = None

from urllib.parse import unquote, quote
import logging
from functools import wraps
from flask import Flask, jsonify
import threading
from typing import Dict, List, Tuple, Optional, Any
import hashlib
import base64

# Create Flask app for dummy web server
web_app = Flask(__name__)

@web_app.route("/")
def index():
    return "✅ K-Drama Bot is running", 200

@web_app.route("/health")
def health():
    """Health check endpoint for monitoring."""
    try:
        # Check MongoDB connection
        client.admin.command('ping')
        mongo_status = "connected"
    except Exception as e:
        mongo_status = f"disconnected: {str(e)}"
    
    # Check Pyrogram connection
    bot_status = "connected" if app.is_connected else "disconnected"
    
    # Overall health
    is_healthy = mongo_status == "connected" and bot_status == "connected"
    status_code = 200 if is_healthy else 503
    
    return jsonify({
        "status": "healthy" if is_healthy else "unhealthy",
        "bot": bot_status,
        "database": mongo_status,
        "timestamp": time.time()
    }), status_code

def run_flask():
    web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# Lightweight logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def is_valid_url(text):
    url_pattern = r'^https?://[\S]+'
    return re.match(url_pattern, text)

# Load environment variables
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
ADMIN_IDS = [ADMIN_ID, 6661974604, 6244759828]
STORAGE_CHANNEL_ID = int(os.environ["STORAGE_CHANNEL_ID"])
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

# Required channels for verification
REQUIRED_CHANNELS = ["-1002648019848"]

# MongoDB Connection
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("MONGO_URI not set in environment variables.")
client = MongoClient(
    MONGO_URI,
    maxPoolSize=50,
    minPoolSize=10,
    maxIdleTimeMS=45000,
    serverSelectionTimeoutMS=50000,
    connectTimeoutMS=2000,
    socketTimeoutMS=10000,  # ADD THIS: 10 second socket timeout
    retryWrites=True,
    compressors='zlib'
)

db = client['kdrama']
collection = db['shows']
user_verification_collection = db['user_verification']
reports_collection = db['reports']
users_collection = db['users']
userdb_collection = db['users_canonical']
users_max_collection = db['users_max_profile']  # NEW Max-Profile Collection
favorites_collection = db['favorites']
hash_lookup_collection = db['hash_lookup']
recent_updates_collection = db["recent_updates"]
stats_collection = db['show_stats']

# Create indexes
try:
    collection.create_index([("category", 1), ("show_name", 1)], unique=True)
    user_verification_collection.create_index("user_id", unique=True)
    reports_collection.create_index("user.user_id")
    reports_collection.create_index("status")
    reports_collection.create_index([("created_at", -1)])
    userdb_collection.create_index("user_id", unique=True)
    userdb_collection.create_index("chats.chat_id")
    userdb_collection.create_index("allow_global_notifications")
    favorites_collection.create_index([("user_id", 1), ("show_slug", 1)], unique=True)
    favorites_collection.create_index("user_id")
    favorites_collection.create_index("show_slug")
    hash_lookup_collection.create_index("last_accessed", expireAfterSeconds=604800)
    # Stats
    db['show_stats'].create_index("show_slug", unique=True)
    collection.create_index([("category", 1), ("show_name", 1), ("_id", -1)])
    stats_collection.create_index([("views", -1)])
    favorites_collection.create_index([("created_at", -1)])
except Exception as e:
    logger.warning("Index creation warning: %s", e)

# State management
REPORTS = {}
reply_waiting = {}
upload_state = {}
import_state = {}
active_group_sessions = {}
inline_query_chat_tracking = {}
user_session_messages = {}
report_waiting = {}
recent_updates = []
recent_updates_lock = threading.Lock()
active_notification_tasks = set()  # Track active notification tasks for graceful shutdown
@app.on_message()
async def debug_all_messages(client, message):
    print(f"[DEBUG] Received a message: {message.text}")
@app.on_message()
async def test_msg(client, message):
    print(f"ANY MESSAGE RECEIVED: {message.text}")

# Category emojis
CATEGORY_EMOJIS = {
    "Hindi Dubbed": "🎞",
    "Regional": "🌍",
    "Japanese Drama": "🎌",
    "C Drama": "📺",
    "Arabic": "🌙",
    "Pakistan": "🇵🇰",
    "Anime": "🎨"
}

def get_category_emoji(category: str) -> str:
    return CATEGORY_EMOJIS.get(category, "📂")

# ============================
# PERSISTENT HASH STORAGE
# ============================

def make_id(value: str) -> str:
    """Create stable 16-char hash with MongoDB persistence."""
    if not value:
        return "empty"
    
    h = hashlib.sha256(value.strip().lower().encode()).hexdigest()[:16]
    
    try:
        hash_lookup_collection.update_one(
            {"hash": h},
            {
                "$set": {
                    "hash": h,
                    "value": value,
                    "last_accessed": time.time()
                }
            },
            upsert=True
        )
    except Exception as e:
        logger.debug(f"Hash storage error: {e}")
    
    return h

from collections import OrderedDict
_hash_memory_cache = OrderedDict()

def resolve_id(h: str) -> str:
    if not h or h == "empty":
        return h
    
    # Convert bytes to str if needed
    if isinstance(h, bytes):
        try:
            h = h.decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
            return h
    
    if h in _hash_memory_cache:
        # Move to end (most recently used)
        _hash_memory_cache.move_to_end(h)
        return _hash_memory_cache[h]
    
    try:
        result = hash_lookup_collection.find_one(
            {"hash": h},
            {"value": 1, "_id": 0}
        )
        
        if result:
            value = result["value"]
            _hash_memory_cache[h] = value
            
            # Evict oldest if cache exceeds limit (max 10,000 entries)
            if len(_hash_memory_cache) > 10000:
                _hash_memory_cache.popitem(last=False)
            
            return value
    except Exception as e:
        logger.debug(f"Hash lookup error: {e}")
    
    return h


async def load_hash_cache_on_startup():
    """Load hash cache on startup - no-op for MongoDB-backed system."""
    try:
        count = hash_lookup_collection.count_documents({})
        logger.info(f"✅ Hash lookup collection has {count} entries")
    except Exception as e:
        logger.warning(f"Hash cache check warning: {e}")

def normalize_show_slug(show_name):
    """Base64 encode show name for URLs."""
    if not show_name:
        return "unknown"
    encoded = base64.urlsafe_b64encode(show_name.encode('utf-8')).decode('ascii')
    return encoded.rstrip('=')

def decode_show_slug(slug):
    """Decode base64 slug to original name."""
    try:
        padded = slug + "=" * ((4 - len(slug) % 4) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode('utf-8')).decode('utf-8')
        return decoded
    except Exception:
        return slug.replace("_", " ")

# ============================
# RECENT UPDATES SYSTEM
# ============================

def load_recent_updates():
    """Load recent updates from MongoDB on startup."""
    global recent_updates
    try:
        doc = recent_updates_collection.find_one({"_id": "recent_updates"})
        if doc and "updates" in doc:
            recent_updates = doc["updates"]
            logger.info(f"✅ Loaded {len(recent_updates)} recent updates")
        else:
            recent_updates = []
    except Exception as e:
        logger.exception(f"Error loading recent updates: {e}")
        recent_updates = []

def save_recent_updates():
    """Save recent updates to MongoDB."""
    try:
        recent_updates_collection.update_one(
            {"_id": "recent_updates"},
            {"$set": {"updates": recent_updates}},
            upsert=True
        )
    except Exception as e:
        logger.exception(f"Error saving recent updates: {e}")

def add_recent_update(category, show_name, season, episode_number):
    """Add a new update to recent updates list (thread-safe)."""
    global recent_updates
    
    try:
        new_update = {
            "timestamp": int(time.time()),
            "category": category,
            "show_name": show_name,
            "season": season,
            "episode_number": episode_number
        }
        
        # Thread-safe update
        with recent_updates_lock:
            recent_updates.insert(0, new_update)
            
            if len(recent_updates) > 20:
                recent_updates = recent_updates[:20]
        
        save_recent_updates()
        logger.info(f"📝 Added recent update: {show_name} S{season} E{episode_number}")
        
    except Exception as e:
        logger.exception(f"Error adding recent update: {e}")

def format_time_ago(timestamp):
    """Format timestamp as 'X min/hours/days ago'."""
    now = int(time.time())
    diff = now - timestamp
    
    if diff < 60:
        return "just now"
    elif diff < 3600:
        mins = diff // 60
        return f"{mins} min ago" if mins == 1 else f"{mins} mins ago"
    elif diff < 86400:
        hours = diff // 3600
        return f"{hours} hour ago" if hours == 1 else f"{hours} hours ago"
    else:
        days = diff // 86400
        return f"{days} day ago" if days == 1 else f"{days} days ago"


# ============================
# BACKGROUND CLEANUP TASK
# ============================

async def cleanup_stale_states():
    """Clean up expired upload and import states."""
    while True:
        try:
            await asyncio.sleep(300)  # Run every 5 minutes
            
            now = time.time()
            
            # Clean upload states older than 10 minutes
            stale_uploads = [
                uid for uid, state in upload_state.items()
                if now - state.get("created", now) > 600
            ]
            for uid in stale_uploads:
                upload_state.pop(uid, None)
            
            # Clean import states older than 10 minutes
            stale_imports = [
                uid for uid, state in import_state.items()
                if now - state.get("created", now) > 600
            ]
            for uid in stale_imports:
                import_state.pop(uid, None)
            
            if stale_uploads or stale_imports:
                logger.info(
                    f"🧹 Cleaned up {len(stale_uploads)} upload states, "
                    f"{len(stale_imports)} import states"
                )
                
        except Exception as e:
            logger.exception(f"Cleanup task error: {e}")

# ============================
# RATE LIMITER
# ============================

class RateLimiter:
    """Rate limiter to prevent FloodWait errors."""
    def __init__(self, max_per_second=10):
        self.max_per_second = max_per_second
        self.timestamps = deque()
        self.lock = threading.Lock()
    
    async def acquire(self):
        """Wait if rate limit would be exceeded."""
        with self.lock:
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

# Global rate limiter instance
notification_limiter = RateLimiter(max_per_second=8)

# Load on module initialization
load_recent_updates()

# ============================
# DATA CACHING
# ============================

class DataCache:
    def __init__(self):
        self.data = None
        self.timestamp = 0
        self.ttl = 300  # 5 minutes

    def get(self):
        now = time.time()
        if self.data and (now - self.timestamp) < self.ttl:
            return self.data
        return None

    def set(self, data):
        self.data = data
        self.timestamp = time.time()

    def clear(self):
        self.data = None
        self.timestamp = 0

_cache = DataCache()

def load_data_cached():
    cached = _cache.get()
    if cached:
        return cached
    
    data = load_data()
    _cache.set(data)
    return data

def clear_data_cache():
    _cache.clear()


def load_data():
    data = {}
    
    projection = {
        "category": 1,
        "show_name": 1,
        "episodes": 1,
        "poster": 1,
        "_id": 0
    }
    
    try:
        cursor = collection.find({}, projection, batch_size=100)
        
        for doc in cursor:
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
    except Exception as e:
        logger.exception(f"Error loading data: {e}")
    
    return data



def save_data(data):
    """Save data to MongoDB with error handling and rollback."""
    updated_count = 0
    failed_shows = []
    
    try:
        for category, shows in data.items():
            for show_name, show_data in shows.items():
                try:
                    poster = show_data.get("poster", [])
                    episodes = {k: v for k, v in show_data.items() if k != "poster"}
                    
                    result = collection.update_one(
                        {"category": category, "show_name": show_name},
                        {"$set": {
                            "category": category,
                            "show_name": show_name,
                            "episodes": episodes,
                            "poster": poster
                        }},
                        upsert=True
                    )
                    
                    if result.modified_count > 0 or result.upserted_id:
                        updated_count += 1
                    
                except Exception as show_error:
                    logger.error(f"Failed to save {show_name} in {category}: {show_error}")
                    failed_shows.append(f"{category}/{show_name}")
        
        clear_data_cache()
        
        if failed_shows:
            logger.warning(f"⚠️ Failed to save {len(failed_shows)} shows: {failed_shows[:5]}")
        else:
            logger.info(f"✅ Successfully saved {updated_count} shows")
            
    except Exception as e:
        logger.exception(f"Critical error in save_data: {e}")
        raise  # Re-raise to allow caller to handle

def backup_database():
    try:
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_dir = "backups"
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"mongo_backup_{now}.json")

        data = list(collection.find())
        for item in data:
            item["_id"] = str(item["_id"])

        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"Database backed up to: {backup_path}")
    except Exception as e:
        logger.warning(f"Backup failed: {e}")

# ============================
# HELPER FUNCTIONS
# ============================

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{get_category_emoji('Hindi Dubbed')} Hindi Dubbed", 
                             callback_data=f"category|{make_id('Hindi Dubbed')}")],
        [InlineKeyboardButton(f"{get_category_emoji('Japanese Drama')} Japanese Drama", 
                             callback_data=f"category|{make_id('Japanese Drama')}")],
        [InlineKeyboardButton(f"{get_category_emoji('C Drama')} C Drama", 
                             callback_data=f"category|{make_id('C Drama')}")],
        [InlineKeyboardButton(f"{get_category_emoji('Arabic')} Arabic", 
                             callback_data=f"category|{make_id('Arabic')}")],
        [InlineKeyboardButton(f"{get_category_emoji('Regional')} Regional", 
                             callback_data=f"category|{make_id('Regional')}")],
        [InlineKeyboardButton(f"{get_category_emoji('Pakistan')} Pakistan", 
                             callback_data=f"category|{make_id('Pakistan')}")],
        [InlineKeyboardButton(f"{get_category_emoji('Anime')} Anime", 
                             callback_data=f"category|{make_id('Anime')}")],
        [InlineKeyboardButton("⚠️ Report Issue", callback_data="report")]
    ])

def admin_filter_func(_, __, message):
    return message.from_user and message.from_user.id in ADMIN_IDS

admin_filter = filters.create(admin_filter_func)

async def auto_delete_message(msg, delay: int = 180):
    try:
        await asyncio.sleep(delay)
        await msg.delete()
    except Exception as e:
        logger.debug("auto_delete failed: %s", e)

def track_user_message(user_id, message):
    if user_id in ADMIN_IDS:
        return  # skip tracking for admins
    
    if user_id not in user_session_messages:
        user_session_messages[user_id] = []
    
    if len(user_session_messages[user_id]) > 50:
        user_session_messages[user_id] = user_session_messages[user_id][-50:]
    
    user_session_messages[user_id].append(message.id)


async def auto_delete_user_session(client, user_id, chat_id, delay: int = 180):
    try:
        await asyncio.sleep(delay)
        if user_id in user_session_messages and user_id not in ADMIN_IDS:
            message_ids = user_session_messages[user_id]
            if message_ids:
                try:
                    await client.delete_messages(chat_id=chat_id, message_ids=message_ids)
                    logger.info(f"Deleted {len(message_ids)} session messages for user {user_id}")
                except Exception as e:
                    logger.debug(f"Failed to delete session messages: {e}")
            user_session_messages[user_id] = []
    except Exception as e:
        logger.debug(f"auto_delete_user_session failed: {e}")

# ============================
# USER VERIFICATION
# ============================

def get_user_verification_status(user_id):
    try:
        doc = user_verification_collection.find_one({"user_id": user_id})
        if not doc:
            return False
        
        last_verified = doc.get("last_verified")
        if not last_verified:
            return False
        
        time_diff = datetime.now() - last_verified
        return time_diff.total_seconds() < (48 * 60 * 60)
    except Exception as e:
        logger.debug(f"Verification check error: {e}")
        return False

def update_user_verification(user_id):
    try:
        user_verification_collection.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "last_verified": datetime.now()}},
            upsert=True
        )
    except Exception as e:
        logger.debug(f"Verification update error: {e}")

async def check_user_membership(client, user_id):
    missing = []
    
    for ch in REQUIRED_CHANNELS:
        try:
            chat = await client.get_chat(ch)
            member = await client.get_chat_member(chat.id, user_id)
            status = getattr(member, "status", None)
            
            if status not in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
                missing.append(chat)
                
        except UserNotParticipant:
            try:
                chat = await client.get_chat(ch)
                missing.append(chat)
            except Exception:
                missing.append(type('Chat', (), {
                    'id': ch,
                    'title': 'Required Channel',
                    'username': None
                })())
                
        except Exception as e:
            # Log the error but DON'T add to missing list
            # This prevents false positives from network/API errors
            logger.warning(f"Error checking membership for {ch}: {e}")
            # Continue to next channel instead of marking as missing
            continue
    
    return missing

# ============================
# USER DATABASE SYSTEM
# ============================

async def upsert_user_from_context(user, chat):
    if not user:
        return
    
    try:
        full_name = ""
        if user.first_name:
            full_name = user.first_name
        if user.last_name:
            full_name += f" {user.last_name}" if full_name else user.last_name
        
        display_name = full_name or user.username or f"User{user.id}"
        
        user_doc = {
            "user_id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "full_name": full_name,
            "display_name": display_name,
            "language_code": user.language_code or "",
            "is_premium": getattr(user, 'is_premium', False),
            "is_bot": user.is_bot,
            "last_interaction": datetime.now()
        }
        
        chat_info = None
        if chat:
            chat_info = {
                "chat_id": chat.id,
                "chat_type": str(chat.type) if hasattr(chat, 'type') else "private",
                "title": getattr(chat, 'title', None),
                "last_seen": datetime.now()
            }
        
        update_ops = {
            "$set": user_doc,
            "$setOnInsert": {
                "allow_global_notifications": True,
                "created_at": datetime.now()
            }
        }
        
        if chat_info:
            update_ops["$addToSet"] = {
                "chats": {
                    "$each": [chat_info]
                }
            }
        
        userdb_collection.update_one(
            {"user_id": user.id},
            update_ops,
            upsert=True
        )
        
    except Exception as e:
        logger.debug(f"upsert_user_from_context error: {e}")

# ============================
# FAVORITES SYSTEM
# ============================

async def is_favorited(user_id, show_slug):
    try:
        result = await asyncio.to_thread(
            favorites_collection.find_one,
            {"user_id": user_id, "show_slug": show_slug}
        )
        return result is not None
    except Exception as e:
        logger.error(f"is_favorited error: {e}")
        return False

async def add_favorite(user_id, category, show_name, show_slug):
    try:
        result = await asyncio.to_thread(
            favorites_collection.insert_one,
            {
                "user_id": user_id,
                "category": category,
                "show_name": show_name,
                "show_slug": show_slug,
                "created_at": datetime.now()
            }
        )
        return result.inserted_id is not None
    except Exception as e:
        logger.error(f"add_favorite error: {e}")
        return False

async def remove_favorite(user_id, show_slug):
    try:
        result = await asyncio.to_thread(
            favorites_collection.delete_one,
            {"user_id": user_id, "show_slug": show_slug}
        )
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"remove_favorite error: {e}")
        return False

async def increment_show_view(category, show_name):
    """Increment view count for a show."""
    try:
        slug = normalize_show_slug(show_name)
        stats_collection.update_one(
            {"show_slug": slug},
            {
                "$inc": {"views": 1},
                "$set": {
                    "show_name": show_name,
                    "category": category,
                    "last_viewed": datetime.now()
                }
            },
            upsert=True
        )
    except Exception as e:
        logger.debug(f"View increment error: {e}")

# ============================
# NOTIFICATION SYSTEM
# ============================

async def notify_new_content(show_name, category, show_slug, is_new_show, episode_count, client):
    try:
        bot_username = (await client.get_me()).username
        normalized_show = normalize_show_slug(show_name)
        category_slug = category.lower().replace(" ", "_")
        deep_link = f"https://t.me/{bot_username}?start={category_slug}__{normalized_show}"

        recipients = []

        if is_new_show:
            try:
                users = userdb_collection.find({
                    "allow_global_notifications": True,
                    "is_bot": False
                })
                recipients = [(u["user_id"], u.get("display_name", "User")) for u in users]
            except Exception as e:
                logger.debug(f"Error fetching global notification users: {e}")
        else:
            try:
                favs = favorites_collection.find({"show_slug": show_slug})
                user_ids = [f["user_id"] for f in favs]
                users = userdb_collection.find({"user_id": {"$in": user_ids}})
                recipients = [(u["user_id"], u.get("display_name", "User")) for u in users]
            except Exception as e:
                logger.debug(f"Error fetching favorite users: {e}")

        if not recipients:
            logger.info(f"No recipients for {show_name}")
            return

        if is_new_show:
            message = (
                f"📢 **New Show Added!**\n\n"
                f"🎬 {show_name}\n"
                f"📂 {category}\n\n"
                f"[▶ Open in Bot]({deep_link})"
            )
        else:
            message = (
                f"📢 **New Episode Released!**\n\n"
                f"🎬 {show_name}\n"
                f"📂 {category}\n"
                f"📺 Episode {episode_count} now available!\n\n"
                f"[▶ Open in Bot]({deep_link})"
            )

        sent_count = 0
        failed_count = 0

        async def safe_send(uid, txt):
            try:
                await client.send_message(chat_id=uid, text=txt, disable_web_page_preview=True)
                return True
            except (UserIsBlocked, InputUserDeactivated):
                # User blocked or deleted - ignore to prevent log noise
                return False
            except FloodWait as fw:
                await asyncio.sleep(fw.value)
                return await safe_send(uid, txt) # Retry once
            except Exception as ex:
                logger.debug(f"Send failed for {uid}: {ex}")
                return False

        for user_id, _ in recipients:
            try:
                # Use rate limiter
                await notification_limiter.acquire()
                
                # Fire and forget (but safe)
                task = asyncio.create_task(safe_send(user_id, message))
                active_notification_tasks.add(task)
                task.add_done_callback(active_notification_tasks.discard)
                
                sent_count += 1
                
            except Exception as e:
                logger.debug(f"Loop error for {user_id}: {e}")
                failed_count += 1
        logger.info(f"Notifications sent: {sent_count}, failed: {failed_count}")

    except Exception as e:
        logger.exception(f"notify_new_content error: {e}")

# ============================
# START COMMAND
# ============================
#added one line for commit
#new line for empty commit

@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    print("Start command invoked")
    await upsert_user_from_context(message.from_user, message.chat)

    user_id = message.from_user.id
    data = load_data_cached()
    args = message.text.split()
    slug = args[1] if len(args) > 1 else None

    if get_user_verification_status(user_id):
        if slug and "__" in slug:
            try:
                category_part, show_part = slug.split("__", 1)
                category_key = category_part.replace("_", " ").lower().strip()

                matched_category = None
                for c in data:
                    if c.lower().strip() == category_key:
                        matched_category = c
                        break

                if not matched_category:
                    return await message.reply("❌ Category not found.")

                decoded_show_name = decode_show_slug(show_part).strip()

                matched_show = None
                for db_show in data[matched_category]:
                    if db_show.lower() == decoded_show_name.lower():
                        matched_show = db_show
                        break

                if not matched_show:
                    for db_show in data[matched_category]:
                        if decoded_show_name.lower() in db_show.lower():
                            matched_show = db_show
                            break

                if not matched_show:
                    logger.error(f"No match for slug={show_part}, decoded={decoded_show_name}")
                    return await message.reply("❌ Show not found. Link may be outdated.")

                show_name = matched_show

            except Exception as e:
                logger.exception(f"Deep link error: {e}")
                return await message.reply("❌ Invalid link format.")

            shows = sorted(data[matched_category])

            if show_name in shows:
                shows.remove(show_name)
            shows.insert(0, show_name)

            from math import ceil
            page_shows = shows[:10]
            total_pages = ceil(len(shows) / 10)

            buttons = []
            cat_hash = make_id(matched_category)
            
            for s in page_shows:
                emoji = "⭐" if s == show_name else "🎞"
                show_hash = make_id(s)
                
                buttons.append([
                    InlineKeyboardButton(
                        f"{emoji} {s}",
                        callback_data=f"show|{cat_hash}|{show_hash}"
                    )
                ])

            if total_pages > 1:
                star_hash = make_id(show_name)
                buttons.append([
                    InlineKeyboardButton(f"Page 1/{total_pages}", callback_data="noop"),
                    InlineKeyboardButton("Next ➡️", callback_data=f"page|{cat_hash}|2|{star_hash}")
                ])

            buttons.append([InlineKeyboardButton("🔙 Back to Categories", callback_data="back_to_menu")])

            title = f"🎞 {matched_category} Shows:"

            update_user_verification(user_id)
            msg = await message.reply(title, reply_markup=InlineKeyboardMarkup(buttons))
            track_user_message(user_id, msg)
            return msg

        msg = await message.reply(
            "🎬 Welcome back! Choose a category:",
            reply_markup=main_keyboard()
        )
        track_user_message(user_id, msg)
        return msg

    keyboard = [
        [InlineKeyboardButton("Join Channel 1", url="https://t.me/KDRAMAAVIL")],
        [InlineKeyboardButton("Join Channel 2", url="https://t.me/Seoul_Entertainment/1")],
        [InlineKeyboardButton("I Joined Both", callback_data="joined")]
    ]
    return await message.reply(
        "Please join both channels to use the bot.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ============================
# JOINED CALLBACK
# ============================

@app.on_callback_query(filters.regex("^joined$"))
async def joined_channel(client, callback: CallbackQuery):
    await upsert_user_from_context(callback.from_user, callback.message.chat)
    
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

    update_user_verification(user_id)

    edited_msg = await callback.message.edit_text(
        "🎬 Welcome to K-Drama Bot! Choose a category:", 
        reply_markup=main_keyboard()
    )
    track_user_message(user_id, edited_msg)


@app.on_message(filters.command("help") & filters.private)
async def help_command(client, message):
    """Display help information for users and admins."""
    await upsert_user_from_context(message.from_user, message.chat)

    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS

    if is_admin:
        help_text = (
"🛡 **ADMIN COMMANDS — Full Reference**\n"
"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
"**General**\n"
"• `/admin_help` — Show complete admin menu\n"
"• `/stats` — Bot health, DB sizes, cache status\n"
"• `/check_storage` — Test storage channel access\n\n"
"**Content Upload / Management**\n"
"• `/upload <Category>` — Start upload flow\n"
"• `/upload_hindi <Show>` — Upload under Hindi Dubbed\n"
"• `/upload_regional <Show>` — Upload under Regional\n"
"• `/upload_split <Category> <Show> <Season> <Ep> <Part>` — Upload split part\n"
"• `/split <Category> <Show> <Season> <Episode>` — Convert to split\n"
"• `/add_show <Category> <Show Name>` — Create show entry\n"
"• `/add_poster <Show Name>` — Upload poster\n"
"• `/post_show <Show Name>` — Post simple card\n"
"• `/post_premium <Show Name>` — Post premium card\n"
"• `/make_poster <Show Name>` — Auto-generate poster\n"
"• `/import_<category>` — Import legacy data\n\n"
"**Deletion & Edits**\n"
"• `/delete <Category> <Show>[>Season>[>Episode]]` — Delete content\n"
"• `/fix_quality <Category> <Show>` — Convert old quality format\n\n"
"**Reports & Moderation**\n"
"• `/report <ID>` — View a specific report\n"
"• `/report_search <keyword>` — Search across reports\n\n"
"**User Management**\n"
"• `/search_user <id|@username|name>` — Full profile\n"
"• `/user_reports <id|@username>` — View user’s reports\n"
"• `/user_history <id>` — Detailed user activity\n"
"• `/user_delete <id>` — Remove user from DB\n\n"
"**Automation & Sync**\n"
"• `/sync_users` — Cleanup/user index\n"
"• `/backup_db` — Generate DB backup\n"
"• `/restore_db <timestamp>` — Restore backup\n\n"
"**Broadcast**\n"
"• `/broadcast <message>` — Send to all users\n"
"• `/broadcast_test <message>` — Send to 10 users\n\n"
"**Diagnostics**\n"
"• `/check_forward <file_id>` — Test message forwarding\n"
"━━━━━━━━━━━━━━━━━━━━━━━"
        )

    else:
        bot_username = (await client.get_me()).username or "this_bot"

        help_text = (
f"🙋‍♂️ **USER COMMANDS & HELP**\n"
"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
"**/start**\n"
"• Open main menu (categories)\n"
"• Deep links supported\n\n"
"**/fav**\n"
"• Show all favorites\n"
"• Add/remove from inside show menu\n\n"
"**/trending**\n"
"• View popular shows\n\n"
f"**Inline Search**\n"
f"• `@{bot_username} <query>` — Instant show search\n\n"
"**Report Issues**\n"
"• Use the “⚠ Report Issue” button inside any show\n\n"
"**Navigation**\n"
"• Categories → Shows → Seasons → Episodes\n"
"• Split episodes show as Part 1 / Part 2\n\n"
"**Notes**\n"
"• Videos auto-delete in 3 minutes for privacy\n"
f"• Use inline search: `@{bot_username} <name>` if a show isn’t listed\n"
"━━━━━━━━━━━━━━━━━━━━━━━"
        )

    await message.reply(help_text, disable_web_page_preview=True)




# Add all your other existing handlers here (categories, episodes, admin commands, etc.)
# They will work normally in private chats

@app.on_callback_query(filters.regex("^category_hindi$"))
async def category_hindi(client, callback):
    # Track user interaction in UserDB
    await upsert_user_from_context(callback.from_user, callback.message.chat)
    
    data = load_data_cached()
    if "Hindi Dubbed" not in data:
        return await callback.answer("No shows found.")
    
    # Get all shows and apply pagination
    all_shows = sorted(data["Hindi Dubbed"])
    page_shows, total_pages = paginate_items(all_shows, page=1)
    
    # Build show buttons with encoded callbacks
    buttons = [
        [InlineKeyboardButton(f"🎞 {show}", callback_data=f"show|{make_id('Hindi Dubbed')}|{make_id(show)}")]
        for show in page_shows
    ]
    
    # Add pagination buttons if needed
    buttons.extend(build_pagination_buttons("Hindi Dubbed", 1, total_pages))
    
    edited_msg = await callback.message.edit_text("🎞 Hindi Dubbed Shows:", reply_markup=InlineKeyboardMarkup(buttons))
    track_user_message(callback.from_user.id, edited_msg)

@app.on_callback_query(filters.regex("^category_regional$"))
async def category_regional(client, callback):
    # Track user interaction in UserDB
    await upsert_user_from_context(callback.from_user, callback.message.chat)
    
    data = load_data_cached()
    if "Regional" not in data:
        return await callback.answer("No shows found.")
    
    # Get all shows and apply pagination
    all_shows = sorted(data["Regional"])
    page_shows, total_pages = paginate_items(all_shows, page=1)
    
    # Build show buttons with encoded callbacks
    buttons = [
        [InlineKeyboardButton(f"🌍 {show}", callback_data=f"show|{make_id('Regional')}|{make_id(show)}")]
        for show in page_shows
    ]
    
    # Add pagination buttons if needed
    buttons.extend(build_pagination_buttons("Regional", 1, total_pages))
    
    edited_msg = await callback.message.edit_text("🌍 Regional Shows:", reply_markup=InlineKeyboardMarkup(buttons))
    track_user_message(callback.from_user.id, edited_msg)

# ============================
# 3. FIXED CATEGORY HANDLER
# ============================

@app.on_callback_query(filters.regex(r"^category\|"))
async def category_handler(client, callback_query):
    """Handle category selection with correct encoding."""
    await upsert_user_from_context(callback_query.from_user, callback_query.message.chat)
    
    try:
        _, enc_cat = callback_query.data.split("|", 1)
        category = resolve_id(enc_cat)
        
        data = load_data_cached()

        if category not in data:
            return await callback_query.answer("Category not found.", show_alert=True)
        
        all_shows = sorted(data[category].keys())
        page_shows, total_pages = paginate_items(all_shows, page=1)
        
        # ✅ Create buttons with fresh hashes
        buttons = []
        for show_name in page_shows:
            show_hash = make_id(show_name)
            
            buttons.append([
                InlineKeyboardButton(
                    f"🎬 {show_name}",
                    callback_data=f"show|{enc_cat}|{show_hash}"
                )
            ])
        
        # ✅ Pagination
        if total_pages > 1:
            nav_row = [InlineKeyboardButton(f"📄 Page 1/{total_pages}", callback_data="noop")]
            if total_pages > 1:
                nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"page|{enc_cat}|2"))
            buttons.append(nav_row)
        
        # ✅ Back button
        buttons.append([InlineKeyboardButton("🔙 Back to Categories", callback_data="back_to_menu")])
        
        emoji = get_category_emoji(category)
        edited_msg = await callback_query.message.edit_text(
            f"{emoji} **{category} Shows:**",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        track_user_message(callback_query.from_user.id, edited_msg)
        
    except Exception as e:
        logger.exception(f"category_handler error: {e}")
        await callback_query.answer("Error opening category.", show_alert=True)

# === PAGINATION UTILITY FUNCTIONS ===
def paginate_items(items, page, items_per_page=10):
    """
    Paginate a list of items.
    
    Args:
        items: List of items to paginate
        page: Current page number (1-indexed)
        items_per_page: Number of items per page (default: 10)
    
    Returns:
        Tuple of (page_items, total_pages)
    """
    if not items:
        return [], 0
    
    total_pages = (len(items) + items_per_page - 1) // items_per_page
    
    # Clamp page to valid range
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    
    return items[start_idx:end_idx], total_pages

# === PAGINATION BUTTONS BUILDER ===
def build_pagination_buttons(category, current_page, total_pages, items_per_page=10):
    """
    Build pagination buttons for category shows.
    
    Args:
        category: Category name (e.g., "Hindi Dubbed")
        current_page: Current page number (1-indexed)
        total_pages: Total number of pages
        items_per_page: Items per page (for reference)
    
    Returns:
        List of button rows (list of lists) or empty list
    """
    buttons = []
    
    if current_page > 1:
        buttons.append(InlineKeyboardButton(
            "⬅️ Previous",
            callback_data=f"page|{make_id(category)}|{current_page - 1}"
        ))
    
    if current_page < total_pages:
        buttons.append(InlineKeyboardButton(
            "Next ➡️",
            callback_data=f"page|{make_id(category)}|{current_page + 1}"
        ))
    
    if buttons:
        return [buttons]
    return []

# === FIND SHOW IN ANY CATEGORY ===
def find_show_category(show_name, data):
    """
    Find a show across any category.
    
    Args:
        show_name: Name to search for
        data: Dictionary of categories and shows
    
    Returns:
        Tuple of (category, show_key) or (None, None)
    """
    if not show_name or not data:
        return None, None
    
    search_variations = [
        show_name,
        show_name.replace(" ", "_"),
        show_name.replace("_", " "),
    ]
    
    try:
        for category, shows in data.items():
            if not isinstance(shows, dict):
                continue
            
            for show_key in shows.keys():
                for variation in search_variations:
                    if str(show_key).lower() == str(variation).lower():
                        return category, show_key
    except Exception as e:
        logger.debug(f"find_show_category error: {e}")
    
    return None, None

# ============================
# 4. FIXED PAGINATION HANDLER
# ============================

@app.on_callback_query(filters.regex(r"^page\|"))
async def handle_pagination(client, callback_query):
    """Handle pagination with correct encoding."""
    try:
        parts = callback_query.data.split("|")
        if len(parts) < 3:
            return await callback_query.answer("Invalid pagination data.", show_alert=True)
        
        cat_id = parts[1]
        page = int(parts[2])
        star_id = parts[3] if len(parts) > 3 else ""
        
        category = resolve_id(cat_id)
        starred_show = resolve_id(star_id) if star_id else None
        
        data = load_data_cached()
        
        if category not in data:
            return await callback_query.answer("Category not found.", show_alert=True)
        
        all_shows = sorted(data[category].keys())
        
        if starred_show and starred_show in all_shows:
            all_shows.remove(starred_show)
            all_shows.insert(0, starred_show)
        
        page_shows, total_pages = paginate_items(all_shows, page)
        
        # ✅ Build buttons with reused hashes
        buttons = []
        for show_name in page_shows:
            emoji = "⭐" if show_name == starred_show else "🎬"
            show_hash = make_id(show_name)
            
            buttons.append([
                InlineKeyboardButton(
                    f"{emoji} {show_name}",
                    callback_data=f"show|{cat_id}|{show_hash}"
                )
            ])
        
        # ✅ Pagination
        if total_pages > 1:
            nav_row = []
            if page > 1:
                prev_cb = f"page|{cat_id}|{page-1}"
                if star_id:
                    prev_cb += f"|{star_id}"
                nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=prev_cb))
            
            nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
            
            if page < total_pages:
                next_cb = f"page|{cat_id}|{page+1}"
                if star_id:
                    next_cb += f"|{star_id}"
                nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=next_cb))
            
            buttons.append(nav_row)
        
        # ✅ Back button
        buttons.append([InlineKeyboardButton("🔙 Back to Categories", callback_data="back_to_menu")])
        
        emoji = get_category_emoji(category)
        await callback_query.message.edit_text(
            f"{emoji} **{category} Shows:**",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.exception(f"handle_pagination error: {e}")
        await callback_query.answer("Error loading page.", show_alert=True)

# === BACK TO MENU HANDLER ===
@app.on_callback_query(filters.regex("^back_to_menu$"))
async def back_to_menu(client, callback_query):
    """Return to main category menu."""
    edited_msg = await callback_query.message.edit_text(
        "🎬 Choose a category:",
        reply_markup=main_keyboard()
    )
    track_user_message(callback_query.from_user.id, edited_msg)
    await callback_query.answer()

# ============================
# 5. FIXED SHOW MENU (with Back Button)
# ============================

@app.on_callback_query(filters.regex(r"^show\|"))
async def show_menu(client, callback_query):
    """Handle show menu with correct encoding and back button."""
    try:
        parts = callback_query.data.split("|")
        if parts[0] not in ("show", "show_menu"):
            return await callback_query.answer()
        
        if len(parts) < 3:
            return await callback_query.answer("Invalid show data.", show_alert=True)
        
        enc_cat = parts[1]
        enc_show = parts[2]
        
        category = resolve_id(enc_cat)
        show_name = resolve_id(enc_show)
        
        data = load_data_cached()
        if show_name not in data.get(category, {}):
            return await callback_query.answer("Show not found.", show_alert=True)
        
        buttons = []
        episodes = data[category][show_name]
        
        # Direct episodes
        if "episodes" in episodes:
            ep_list = episodes["episodes"]
            if isinstance(ep_list, list):
                for idx, ep in enumerate(ep_list, start=1):
                    if isinstance(ep, list):
                        buttons.append([InlineKeyboardButton(
                            f"📂 Episode {idx} (split)",
                            callback_data=f"multi|{enc_cat}|{enc_show}|episodes|{idx}"
                        )])
                    else:
                        buttons.append([InlineKeyboardButton(
                            f"▶️ Episode {idx}",
                            callback_data=f"episode|{enc_cat}|{enc_show}|episodes|{idx}"
                        )])
        
        # Seasons
        for season in sorted([k for k in episodes.keys() if k not in ["episodes", "poster"]]):
            season_hash = make_id(str(season))
            buttons.append([InlineKeyboardButton(
                f"📂 Season {season}",
                callback_data=f"season|{enc_cat}|{enc_show}|{season_hash}"
            )])
        
        if not buttons:
            buttons.append([InlineKeyboardButton("🚫 No videos yet", callback_data="noop")])
        
        # ✅ Favorites (only in private)
        chat_type = str(callback_query.message.chat.type) if hasattr(callback_query.message.chat, 'type') else "private"
        if chat_type in ("ChatType.PRIVATE", "private"):
            show_slug = normalize_show_slug(show_name)
            user_id = callback_query.from_user.id
            
            slug_hash = make_id(show_slug)
            if await is_favorited(user_id, show_slug):
                buttons.append([InlineKeyboardButton(
                    "❌ Remove Favorite",
                    callback_data=f"fav_remove|{enc_cat}|{slug_hash}"
                )])
            else:
                buttons.append([InlineKeyboardButton(
                    "⭐ Add to Favorites",
                    callback_data=f"fav_add|{enc_cat}|{slug_hash}"
                )])
        
        # ✅ CRITICAL: Add Back to Category button
        buttons.append([InlineKeyboardButton(
            f"🔙 Back to {category}",
            callback_data=f"category|{enc_cat}"
        )])
        
        # Send poster or edit
        poster_list = episodes.get("poster", [])
        poster = poster_list[-1] if isinstance(poster_list, list) and poster_list else None
        
        if poster:
            try:
                await client.send_photo(
                    chat_id=callback_query.from_user.id,
                    photo=poster,
                    caption=f"🎬 **{show_name}**",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                await callback_query.answer()
            except Exception:
                await callback_query.message.edit_text(
                    f"🎬 **{show_name}**",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                await callback_query.answer()
        else:
            await callback_query.message.edit_text(
                f"🎬 **{show_name}**",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            await callback_query.answer()
            
    except Exception as e:
        logger.exception(f"show_menu error: {e}")
        await callback_query.answer("Error opening show.", show_alert=True)




# === ALL ADMIN COMMANDS REMAIN UNCHANGED ===

# ============================================
# USER MAX-PROFILE HANDLERS (Moved for Precedence)
# ============================================

@app.on_message(filters.command("search_user") & admin_filter & filters.private)
async def search_user_command(client, message):
    """
    Search for a user by ID, username, or name.
    Shows Marker 1 (Profile Card).
    """
    logger.info(f"DEBUG: search_user_command triggered by {message.from_user.id} with text: {message.text}")
    try:
        if len(message.command) < 2:
            return await message.reply("Usage: /search_user <id|username|name>")
            
        query = message.text.split(" ", 1)[1].strip()
        
        # Try to find user in DB first
        user_doc = None
        if query.isdigit():
            user_doc = userdb_collection.find_one({"user_id": int(query)})
        if not user_doc:
            user_doc = userdb_collection.find_one({"username": {"$regex": f"^{query}$", "$options": "i"}})
        if not user_doc:
            user_doc = userdb_collection.find_one({"full_name": {"$regex": query, "$options": "i"}})
            
        if not user_doc:
            return await message.reply("❌ User not found in database.")
            
        user_id = user_doc["user_id"]
        
        # Fetch live data from Telegram
        try:
            chat = await client.get_chat(user_id)
            user_status = getattr(chat, "status", "Unknown")
            photo_present = chat.photo is not None
            dc_id = getattr(chat, "dc_id", "Unknown")
            is_premium = getattr(chat, "is_premium", False)
        except Exception:
            user_status = "Unknown (Fetch Failed)"
            photo_present = False
            dc_id = "Unknown"
            is_premium = False
            
        # Format status nicely
        status_map = {
            "UserStatus.ONLINE": "🟢 Online",
            "UserStatus.OFFLINE": "⚪ Offline",
            "UserStatus.RECENTLY": "🟡 Recently",
            "UserStatus.LAST_WEEK": "🟠 Last Week",
            "UserStatus.LONG_AGO": "🔴 Long Ago"
        }
        status_str = status_map.get(str(user_status), str(user_status))

        # Calculate/Get Profile
        calculate_max_profile(user_id) # Refresh metrics
        
        # Prepare Marker 1 (Main View)
        photo_id = None
        try:
            photos = [p async for p in client.get_chat_photos(user_id, limit=1)]
            if photos:
                photo_id = photos[0].file_id
        except:
            pass
            
        # Caption
        caption = (
            f"👤 **USER MAX-PROFILE**\n"
            f"🆔 ID: `{user_id}`\n"
            f"👤 First Name: {user_doc.get('first_name', '')}\n"
            f"👤 Last Name: {user_doc.get('last_name', '')}\n"
            f"📛 Username: @{user_doc.get('username', 'None')}\n"
            f"📝 Full Name: {user_doc.get('full_name', 'Unknown')}\n\n"
            f"💎 Premium: {'✅ Yes' if is_premium else '❌ No'}\n"
            f"🌐 Language: {user_doc.get('language_code', 'Unknown')}\n"
            f"🏢 DC ID: `{dc_id}`\n"
            f"🖼 Photo Present: {'✅ Yes' if photo_present else '❌ No'}\n"
            f"📊 Status: {status_str}\n"
            f"⏱ Last Online: {user_doc.get('last_interaction', datetime.now()).strftime('%Y-%m-%d %H:%M')}\n"
            f"👇 **ACTIONS:**"
        )
        
        # Default context is 'search'
        buttons = [
            [InlineKeyboardButton("🧠 Deep Analysis (Max-Profile)", callback_data=f"max_profile_{user_id}|search")],
            [InlineKeyboardButton("🗂 Report History", callback_data=f"user_history_{user_id}|search")],
            [InlineKeyboardButton("🚫 Ban User (Mock)", callback_data="noop")]
        ]
        
        if photo_id:
            await client.send_photo(
                chat_id=message.chat.id,
                photo=photo_id,
                caption=caption,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            await message.reply(
                caption,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            
    except Exception as e:
        logger.exception(f"search_user error: {e}")
        await message.reply(f"Error searching user: {e}")



# ============================================
# ADMIN & CONTENT MANAGEMENT (Reorganized)
# ============================================
POSTER_CATEGORY_COMMANDS = {
    "add_poster": "Hindi Dubbed",
    "add_poster_regional": "Regional",
    "add_poster_jap": "Japanese Drama",
    "add_poster_c": "C Drama",
    "add_poster_arb": "Arabic",
    "add_poster_pak": "Pakistan",
    "add_poster_anime": "Anime"
}

poster_upload_state = {}


def build_poster_handler(cmd, category):
    """
    Factory function that creates unique handlers so poster commands do NOT override each other.
    """
    @app.on_message(filters.command(cmd) & admin_filter & filters.private)
    async def handler(client, message):
        # Parse show name
        try:
            show_name = message.text.split(" ", 1)[1].strip()
        except IndexError:
            return await message.reply(f"Usage: /{cmd} <Show Name>")

        data = load_data_cached()

        # Check category
        if category not in data:
            return await message.reply(
                f"❌ Category *{category}* not found in DB.",
                parse_mode="markdown"
            )

        # Check show name
        if show_name not in data[category]:
            return await message.reply(
                f"❌ Show *{show_name}* not found under *{category}*.",
                parse_mode="markdown"
            )

        # Store poster upload state
        poster_upload_state[message.from_user.id] = {
            "show_name": show_name,
            "category": category,
            "file_ids": [],
            "deadline": time.time() + 60,
        }

        await message.reply("📸 Send **1–6 poster images** within 60 seconds…")

    return handler


# Register all poster commands safely
for cmd, category in POSTER_CATEGORY_COMMANDS.items():
    build_poster_handler(cmd, category)


# ---------------------------------------------------------
# COLLECT POSTERS
# ---------------------------------------------------------
@app.on_message(filters.photo & admin_filter & filters.private)
async def collect_poster(client, message):
    user_id = message.from_user.id
    state = poster_upload_state.get(user_id)

    if not state:
        return  # No active poster session

    if time.time() > state["deadline"]:
        poster_upload_state.pop(user_id, None)
        return await message.reply("⏳ Time expired! Run command again.")

    if len(state["file_ids"]) >= 6:
        return await message.reply("❌ Max 6 posters allowed.")

    # Save file_id
    state["file_ids"].append(message.photo.file_id)

    # If at least 1 poster received → finalize immediately
    await finalize_poster_upload(client, message)


# --------------------------------------------------------
# FINALIZE POSTER UPLOAD
# ---------------------------------------------------------
async def finalize_poster_upload(client, message):
    user_id = message.from_user.id
    state = poster_upload_state.pop(user_id, None)

    if not state or not state["file_ids"]:
        return await message.reply("❌ No posters received.")

    result = collection.update_one(
        {"show_name": state["show_name"], "category": state["category"]},
        {"$set": {"poster": state["file_ids"]}}
    )

    clear_data_cache()

    if result.matched_count > 0:
        await message.reply(
            f"✅ Poster uploaded for *{state['show_name']}* under *{state['category']}*.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await message.reply("❌ Poster not saved — show not found.")


# Optional manual override
@app.on_message(filters.command("done") & admin_filter & filters.private)
async def manual_done_poster(client, message):
    user_id = message.from_user.id
    if user_id in poster_upload_state:
        await finalize_poster_upload(client, message)

IMPORT_CATEGORY_MAP = {
    "import_hindi": "Hindi Dubbed",
    "import_regional": "Regional",
    "import_jap": "Japanese Drama",
    "import_c": "C Drama",
    "import_arb": "Arabic",
    "import_pak": "Pakistan",
    "import_anime": "Anime",
}

def convert_legacy_to_quality(entry):
    """
    Convert legacy episode format to multi-quality format.
    
    Legacy formats:
    - String (file_id): "BAACAgUAAy..."
    - Dict with type/content: {"type": "video", "content": "..."}
    - List (split episodes): ["file_id1", "file_id2"]
    
    New format:
    {
        "qualities": {
            "720p": {"type": "video", "content": "file_id"},
            "480p": {"type": "link", "content": "https://..."}
        }
    }
    """
    if entry is None:
        # New episode, create empty structure
        return {"qualities": {}}, True
    
    if isinstance(entry, dict) and "qualities" in entry:
        # Already in new format
        return entry, False
    
    # Legacy format - convert to new format
    episode_data = {"qualities": {}}
    
    if isinstance(entry, str):
        # Old format: just a file_id string
        episode_data["qualities"]["default"] = {
            "type": "video",
            "content": entry
        }
    elif isinstance(entry, dict):
        # Old format: {"type": "...", "content": "..."}
        episode_data["qualities"]["default"] = entry
    elif isinstance(entry, list):
        # Split episode format - keep as is for now
        # This would need special handling
        episode_data["qualities"]["default"] = {
            "type": "split",
            "parts": entry
        }
    
    return episode_data, True


@app.on_message(filters.command([
    "upload", "upload_hindi", "upload_regional",
    "upload_jap", "upload_c", "upload_arb", "upload_pak", "upload_anime"
]) & admin_filter & filters.private)


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
            "category": category,
            "created": time.time()
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
            "category": category,
            "created": time.time()
        }
        return await message.reply(f"Send videos now for *{show_name}*", quote=True)



@app.on_message((filters.video | filters.document) & admin_filter & filters.private)
async def handle_video(client, message: Message):
    user_id = message.from_user.id

    # --- NEW GUARD: ignore if in import mode ---
    if user_id in import_state:
        return

    if user_id not in upload_state:
        # Check if it was just a random doc/video sent without context
        return await message.reply("First use /upload ShowName or /upload ShowName SeasonNumber")

    file_obj = None
    file_type = "video"

    # Identify if Video or Document
    if message.video:
        file_obj = message.video
        file_type = "video"
    elif message.document:
        if message.document.mime_type and message.document.mime_type.startswith("video/"):
            file_obj = message.document
            file_type = "document"
        else:
            return await message.reply("❌ Only **video** documents (MKV, MP4, etc.) are allowed.")
    
    if not file_obj:
        return

    # Validate video size (2GB limit)
    MAX_VIDEO_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
    if file_obj.file_size > MAX_VIDEO_SIZE:
        return await message.reply(
            f"❌ **Video too large!**\n\n"
            f"Size: {file_obj.file_size / (1024**3):.2f}GB\n"
            f"Maximum: 2.0GB\n\n"
            f"Please compress the video and try again."
        )
    
    # Validate video format
    allowed_mimetypes = ["video/mp4", "video/x-matroska", "video/avi", "video/webm"]
    # Some telegram clients might send generic application/octet-stream for MKV, handle carefully if needed or trust mime
    # For now, we trust the filter/mime check above + this check
    if file_obj.mime_type and file_obj.mime_type not in allowed_mimetypes:
        return await message.reply(
            f"❌ **Unsupported format!**\n\n"
            f"Your format: `{file_obj.mime_type}`\n"
            f"Allowed: MP4, MKV, AVI, WebM\n\n"
            f"Please convert and try again."
        )
    
    state = upload_state[user_id]
    show_name = state["show"]
    season_number = state["season"]
    category = state["category"]

    try:
        # Forward to storage channel with enhanced error handling
        try:
            fwd = await message.forward(STORAGE_CHANNEL_ID, disable_notification=True)
        except Exception as fwd_error:
            logger.exception(f"Failed to forward video to storage channel: {fwd_error}")
            return await message.reply(
                f"❌ **Failed to forward video to storage channel!**\n\n"
                f"Error: `{fwd_error}`\n\n"
                f"**Troubleshooting:**\n"
                f"1. Ensure bot is admin in storage channel\n"
                f"2. Bot needs 'Post Messages' permission\n"
                f"3. Try `/test_forward` command first\n"
                f"4. Channel ID: `{STORAGE_CHANNEL_ID}`"
            )
        
        # Get Id from forwarded message
        saved_file_id = None
        if fwd.video:
             saved_file_id = fwd.video.file_id
        elif fwd.document:
             saved_file_id = fwd.document.file_id
        
        if not saved_file_id:
            return await message.reply("❌ Failed to retrieve file ID from storage.")

        file_id = saved_file_id
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
                video = getattr(fwd, "video", None)
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
                "type": file_type,
                "content": file_id
            })
        else:
            data[category][show_name].setdefault("episodes", []).append({
                "type": file_type,
                "content": file_id
            })


        backup_database()
        save_data(data)
        
        # ============================
        # NOTIFICATION HOOK
        # ============================
        # Detect new show vs new episode and send notifications
        try:
            # Count episodes
            if season_number:
                episode_count = len(data[category][show_name].get(season_number, []))
            else:
                episode_count = len(data[category][show_name].get("episodes", []))
            
            # Determine if this is a new show (first episode)
            is_new_show = (episode_count == 1)
            
            # Add to Recent Updates list
            add_recent_update(category, show_name, season_number if season_number else "Season 1", episode_count)

            # Send notifications asynchronously (don't block upload)
            show_slug = normalize_show_slug(show_name)
            asyncio.create_task(
            notify_new_content(
                    show_name,      # 1
                    category,       # 2
                    show_slug,      # 3
                    is_new_show,    # 4
                    episode_count,  # 5
                    client          # 6
                    )
                )
        except Exception as e:
            logger.debug(f"Notification hook error: {e}")
        # ============================

        await message.reply(
            f"Uploaded and saved for *{show_name}*{' Season ' + season_number if season_number else ''}.",
            quote=True
        )

    except Exception as e:
        print("Upload error:", e)
        await message.reply("Failed to upload. Please check bot permissions or channel ID.")


@app.on_message(filters.command(["upload_part"]) & admin_filter & filters.private)
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





@app.on_message(filters.photo & admin_filter & filters.private)
async def handle_poster_photo(client, message: Message):
    user_id = message.from_user.id
    if user_id not in poster_upload_state:
        return
    
    state = poster_upload_state[user_id]
    
    if len(state["file_ids"]) >= 6:
        return await message.reply("Maximum 6 posters allowed. Use /add_poster again if needed.")
    
    try:
        # Forward to storage channel with enhanced error handling
        try:
            fwd = await message.forward(STORAGE_CHANNEL_ID, disable_notification=True)
        except Exception as fwd_error:
            logger.exception(f"Failed to forward poster to storage channel: {fwd_error}")
            return await message.reply(
                f"❌ **Failed to forward poster to storage channel!**\n\n"
                f"Error: `{fwd_error}`\n\n"
                f"**Troubleshooting:**\n"
                f"1. Ensure bot is admin in storage channel\n"
                f"2. Bot needs 'Post Messages' permission\n"
                f"3. Try `/test_forward` command first\n"
                f"4. Channel ID: `{STORAGE_CHANNEL_ID}`"
            )
        
        state["file_ids"].append(fwd.photo.file_id)
        await fwd.delete()
        
        remaining = 6 - len(state["file_ids"])
        if remaining > 0:
            await message.reply(f"Poster received ({len(state['file_ids'])}/6). Send more or wait to auto-finalize.")
        else:
            await finalize_poster_upload(client, message)
            
    except Exception as e:
        logger.exception(f"Poster upload error: {e}")
        await message.reply(f"Failed to process poster: {e}")



@app.on_message(filters.command([
    "split_hindi", "split_regional", "split_jap", "split_c", "split_arb", "split_pak", "split_anime"
]) & admin_filter & filters.private)
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


    


@app.on_message(filters.command([
    "upload_split_hindi", "upload_split_regional", "upload_split_jap", "upload_split_c", "upload_split_arb", "upload_split_pak", "upload_split_anime"
]) & admin_filter & filters.private)
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



@app.on_message(filters.command("list") & admin_filter & filters.private)
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



@app.on_message(filters.command("get_links") & admin_filter & filters.private)
async def get_links(client, message):
    """Generate paginated deep links for all shows from MongoDB."""
    try:
        # Get page number from command
        page = 1
        parts = message.text.split()
        if len(parts) > 1:
            try:
                page = max(1, int(parts[1]))
            except (ValueError, IndexError):
                page = 1
        
        # Get all shows from MongoDB, sorted by category and name
        all_shows = list(collection.find(
            {"episodes": {"$exists": True, "$ne": {}}},  # Only shows with episodes
            {"category": 1, "show_name": 1, "_id": 0}
        ).sort([("category", 1), ("show_name", 1)]))
        
        if not all_shows:
            return await message.reply("❌ No shows with episodes found in database.\n\nUse `/upload` to add shows.")
        
        bot_username = (await client.get_me()).username
        items_per_page = 20
        total_shows = len(all_shows)
        total_pages = (total_shows + items_per_page - 1) // items_per_page
        
        # Validate page
        page = max(1, min(page, total_pages))
        
        # Get shows for current page
        start_idx = (page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        page_shows = all_shows[start_idx:end_idx]
        
        # Build formatted links grouped by category
        links = []
        current_category = None
        
        for show_doc in page_shows:
            show_name = show_doc.get("show_name", "")
            category = show_doc.get("category", "")
            
            if not show_name:
                continue
            
            # Add category header
            if category != current_category:
                if links:
                    links.append("")
                links.append(f"**{category}**")
                current_category = category
            
            # Create deep link
            category_slug = category.lower().replace(" ", "_")
            show_slug = normalize_show_slug(show_name)
            slug = f"{category_slug}__{show_slug}"
            link = f"https://t.me/{bot_username}?start={slug}"
            links.append(f"  • [{show_name}]({link})")
        
        # Build response
        response = f"**📺 Show Links - Page {page}/{total_pages}**\n"
        response += f"**({start_idx + 1}-{min(end_idx, total_shows)} of {total_shows} shows)**\n\n"
        response += "\n".join(links)
        
        # Add pagination controls
        if total_pages > 1:
            response += "\n\n**[Navigation]**\n"
            if page > 1:
                response += f"  `/get_links {page - 1}` ⬅️ Previous\n"
            response += f"  Page {page}/{total_pages}\n"
            if page < total_pages:
                response += f"  Next ➡️ `/get_links {page + 1}`"
        
        await message.reply(response, disable_web_page_preview=True)
        
    except Exception as e:
        logger.exception(f"get_links error: {e}")
        await message.reply(f"❌ Error: {e}")



# ============================
# DELETE HANDLERS - ALL CATEGORIES (DIRECT REGISTRATION)
# ============================

async def handle_delete(client, message, category):
    """Unified delete handler implementation."""
    try:
        if len(message.command) < 2:
            examples = {
                "Hindi Dubbed": ("delete_hindi", "Drawing Closer"),
                "Japanese Drama": ("delete_jap", "good boy"),
                "Regional": ("delete_regional", "S line"),
                "C Drama": ("delete_c", "our generation"),
                "Arabic": ("delete_arb", "noor"),
            }
            cmd, example = examples.get(category, ("delete", "show_name"))
            return await message.reply(
                f"**Usage:**\n"
                f"`/{cmd} show_name` - Delete entire show\n"
                f"`/{cmd} show_name season_no` - Delete season\n"
                f"`/{cmd} show_name season_no episode_no` - Delete episode\n"
                f"`/{cmd} show_name season_no episode_no quality` - Delete specific quality\n\n"
                f"**Examples:**\n"
                f"`/{cmd} {example}` - Delete entire show\n"
                f"`/{cmd} {example} 1` - Delete season 1\n"
                f"`/{cmd} {example} 1 3` - Delete episode 3\n"
                f"`/{cmd} {example} 1 3 720p` - Delete 720p only"
            )
        
        # Parse arguments - show name can be multiple words
        args_text = message.text.split(maxsplit=1)[1].strip()
        args = args_text.split()
        
        if not args:
            return await message.reply(f"❌ **Show name required**")
        
        data = load_data_cached()
        category_shows = data.get(category, {})
        
        if not category_shows:
            return await message.reply(f"❌ **No shows in {category}**")
        
        # Try to match show name - start with longest match
        show_name = None
        remaining_args = []
        
        for i in range(len(args), 0, -1):
            potential = " ".join(args[:i])
            
            # Exact match first
            if potential in category_shows:
                show_name = potential
                remaining_args = args[i:]
                break
            
            # Case-insensitive match
            for db_show in category_shows.keys():
                if db_show.lower() == potential.lower():
                    show_name = db_show
                    remaining_args = args[i:]
                    break
            
            if show_name:
                break
        
        if not show_name:
            available = list(category_shows.keys())[:5]
            shows_text = "\n• ".join(available) if available else "No shows"
            first_arg = args[0]
            return await message.reply(
                f"❌ **Show '{first_arg}' not found in {category}**\n\n"
                f"**Available shows (first 5):**\n• {shows_text}"
            )
        
        # Parse remaining arguments
        season_no = None
        episode_no = None
        quality = None
        
        if len(remaining_args) >= 1:
            season_no = remaining_args[0]
        if len(remaining_args) >= 2:
            try:
                episode_no = int(remaining_args[1])
            except ValueError:
                return await message.reply(f"❌ **Episode number must be a number**")
        if len(remaining_args) >= 3:
            quality = remaining_args[2]
        
        # Call perform_delete
        await perform_delete(message, category, show_name, season_no, episode_no, quality)
        
    except Exception as e:
        logger.exception(f"Delete handler error: {e}")
        await message.reply(f"❌ **Error: {str(e)}**")


# Register handlers for each category
@app.on_message(filters.command("delete_hindi") & admin_filter & filters.private)
async def delete_hindi_handler(client, message):
    await handle_delete(client, message, "Hindi Dubbed")


@app.on_message(filters.command("delete_jap") & admin_filter & filters.private)
async def delete_jap_handler(client, message):
    await handle_delete(client, message, "Japanese Drama")


@app.on_message(filters.command("delete_regional") & admin_filter & filters.private)
async def delete_regional_handler(client, message):
    await handle_delete(client, message, "Regional")


@app.on_message(filters.command("delete_c") & admin_filter & filters.private)
async def delete_c_handler(client, message):
    await handle_delete(client, message, "C Drama")


@app.on_message(filters.command("delete_arb") & admin_filter & filters.private)
async def delete_arb_handler(client, message):
    await handle_delete(client, message, "Arabic")


@app.on_message(filters.command("delete_pak") & admin_filter & filters.private)
async def delete_pak_handler(client, message):
    await handle_delete(client, message, "Pakistan")


@app.on_message(filters.command("delete_anime") & admin_filter & filters.private)
async def delete_anime_handler(client, message):
    await handle_delete(client, message, "Anime")


# Old duplicate handlers removed - using unified ones above


@app.on_message(filters.command("test_forward") & admin_filter & filters.private)
async def test_forward(client, message):
    """Test forwarding to storage channel with detailed debugging info."""
    try:
        # Log the channel ID and type for debugging
        logger.info(f"Testing forward to STORAGE_CHANNEL_ID: {STORAGE_CHANNEL_ID} (type: {type(STORAGE_CHANNEL_ID).__name__})")
        
        # Try to get channel info first
        try:
            channel = await client.get_chat(STORAGE_CHANNEL_ID)
            logger.info(f"Channel found: {channel.title} (ID: {channel.id})")
            await message.reply(f"📡 **Channel Info:**\n🆔 ID: `{channel.id}`\n📝 Title: {channel.title}\n\nAttempting forward...")
        except Exception as e:
            logger.error(f"Failed to get channel info: {e}")
            await message.reply(f"⚠️ Cannot access channel info: {e}\n\nAttempting forward anyway...")
        
        # Attempt the forward
        result = await client.forward_messages(
            chat_id=STORAGE_CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_ids=message.id,  # Fixed: use message.id not message.message_id
            disable_notification=True
        )
        
        logger.info(f"Forward successful! Message ID: {result.id}")
        await message.reply(
            f"✅ **Forward Successful!**\n\n"
            f"📤 Forwarded to: `{STORAGE_CHANNEL_ID}`\n"
            f"🆔 New Message ID: `{result.id}`\n\n"
            f"The bot can now access the storage channel!"
        )
        
    except Exception as e:
        logger.exception(f"test_forward error: {e}")
        await message.reply(
            f"❌ **Forward Failed!**\n\n"
            f"Error: `{e}`\n\n"
            f"**Troubleshooting:**\n"
            f"1. Ensure bot is added to channel as admin\n"
            f"2. Bot needs 'Post Messages' permission\n"
            f"3. Try `/init_storage` command first\n"
            f"4. Channel ID: `{STORAGE_CHANNEL_ID}` (type: {type(STORAGE_CHANNEL_ID).__name__})"
        )



@app.on_message(filters.command("init_storage") & admin_filter & filters.private)
async def init_storage(client, message):
    """Initialize storage channel connection by sending a test message."""
    try:
        logger.info(f"Initializing storage channel: {STORAGE_CHANNEL_ID}")
        
        # Send a test message to establish peer history
        sent_msg = await client.send_message(
            chat_id=STORAGE_CHANNEL_ID,
            text="🤖 **Bot Storage Channel Initialized**\n\nThis message establishes peer history. You can delete this message."
        )
        
        logger.info(f"Storage channel initialized! Message ID: {sent_msg.id}")
        
        await message.reply(
            f"✅ **Storage Channel Initialized!**\n\n"
            f"📡 Channel ID: `{STORAGE_CHANNEL_ID}`\n"
            f"🆔 Test Message ID: `{sent_msg.id}`\n\n"
            f"The bot can now forward messages to the storage channel!\n\n"
            f"Try `/test_forward` to verify."
        )
        
    except Exception as e:
        logger.exception(f"init_storage error: {e}")
        await message.reply(
            f"❌ **Failed to initialize storage channel!**\n\n"
            f"Error: `{e}`\n\n"
            f"**Troubleshooting:**\n"
            f"1. Ensure bot is added to channel as admin\n"
            f"2. Bot needs 'Post Messages' permission\n"
            f"3. Verify Channel ID: `{STORAGE_CHANNEL_ID}`\n\n"
            f"**Manual Fix:**\n"
            f"Go to the storage channel and send: `@K_DRAAMAA_Bot hi`"
        )




@app.on_message(filters.command("check_storage") & admin_filter & filters.private)
async def check_storage(client, message):
    """Diagnostic command to check storage channel access and permissions."""
    try:
        await message.reply(f"🔍 **Checking Storage Channel Access...**\n\nChannel ID: `{STORAGE_CHANNEL_ID}`")
        
        # Try to get channel info
        try:
            channel = await client.get_chat(STORAGE_CHANNEL_ID)
            
            # Get bot's member info in the channel
            try:
                bot_member = await client.get_chat_member(STORAGE_CHANNEL_ID, (await client.get_me()).id)
                
                status_emoji = "✅" if bot_member.status in ["administrator", "creator"] else "⚠️"
                
                await message.reply(
                    f"{status_emoji} **Channel Found!**\n\n"
                    f"📝 **Channel Info:**\n"
                    f"• Title: {channel.title}\n"
                    f"• ID: `{channel.id}`\n"
                    f"• Type: {channel.type}\n\n"
                    f"🤖 **Bot Status:**\n"
                    f"• Status: {bot_member.status}\n"
                    f"• Can Post: {'✅' if bot_member.status in ['administrator', 'creator'] else '❌'}\n\n"
                    f"{'✅ Bot has admin access!' if bot_member.status in ['administrator', 'creator'] else '❌ Bot needs admin permissions!'}"
                )
                
            except Exception as member_error:
                await message.reply(
                    f"⚠️ **Channel found but cannot check bot membership**\n\n"
                    f"Channel: {channel.title}\n"
                    f"Error: `{member_error}`\n\n"
                    f"**This likely means the bot is NOT in the channel.**\n"
                    f"Please add the bot to the channel as admin."
                )
                
        except Exception as channel_error:
            await message.reply(
                f"❌ **Cannot Access Channel**\n\n"
                f"Channel ID: `{STORAGE_CHANNEL_ID}`\n"
                f"Error: `{channel_error}`\n\n"
                f"**Possible Issues:**\n"
                f"1. Bot is not added to the channel\n"
                f"2. Channel ID is incorrect\n"
                f"3. Channel is private and bot has no access\n\n"
                f"**Solution:**\n"
                f"1. Go to your storage channel\n"
                f"2. Add this bot as admin\n"
                f"3. Give 'Post Messages' permission\n"
                f"4. Run this command again"
            )
            
    except Exception as e:
        logger.exception(f"check_storage error: {e}")
        await message.reply(f"❌ Unexpected error: {e}")



@app.on_message(filters.command("reports") & admin_filter & filters.private)
async def admin_reports(client, message: Message):
    """View reports with optional filter."""
    try:
        # Parse filter from command
        args = message.text.split()
        filter_status = args[1] if len(args) > 1 else "pending"
        
        if filter_status not in ["pending", "processing", "resolved", "all"]:
            filter_status = "pending"
        
        # Build query
        query = {} if filter_status == "all" else {"status": filter_status}
        
        # Get reports
        reports, total_pages = paginate_reports(query, page=1, items_per_page=5)
        
        if not reports:
            return await message.reply(f"No {filter_status} reports found.")
        
        # Format message
        msg = f"📋 **{filter_status.upper()} REPORTS** (Page 1/{total_pages})\n\n"
        
        for report in reports:
            report_id = str(report['_id'])
            user = report.get('user', {})
            report_data = report.get('report', {})
            status = report.get('status', 'pending')
            
            status_emoji = {"pending": "🔴", "processing": "🟡", "resolved": "🟢"}.get(status, "⚪")
            
            msg += f"{status_emoji} **#{report_id[-6:]}** - {user.get('full_name', 'Unknown')}\n"
            if report_data.get('show_name'):
                msg += f"   📺 {report_data['show_name']}"
                if report_data.get('episode'):
                    msg += f" {report_data['episode']}"
                msg += "\n"
            msg += f"   💬 {report_data.get('issue', report_data.get('raw_text', 'No description'))[:50]}...\n"
            msg += f"   🕐 {report.get('created_at').strftime('%Y-%m-%d %H:%M')}\n\n"
        
        # Build pagination buttons
        buttons = []
        if total_pages > 1:
            nav_row = []
            if total_pages > 1:
                nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"report_page|{filter_status}|2"))
            buttons.append(nav_row)
        
        await message.reply(msg, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)
        
    except Exception as e:
        logger.exception("admin_reports error: %s", e)
        await message.reply("❌ Error loading reports.")



@app.on_message(filters.command("report") & admin_filter & filters.private)
async def admin_report_detail(client, message: Message):
    """View specific report by ID."""
    try:
        args = message.text.split()
        if len(args) < 2:
            return await message.reply("Usage: `/report <report_id>`")
        
        report_id = args[1]
        
        # Try to find report
        try:
            report_doc = reports_collection.find_one({"_id": ObjectId(report_id)})
        except:
            # Try partial match
            report_doc = reports_collection.find_one({"_id": {"$regex": report_id}})
        
        if not report_doc:
            return await message.reply("❌ Report not found.")
        
        # Format and send
        msg = format_report_message(report_doc)
        
        buttons = [
            [
                InlineKeyboardButton("✅ Processing", callback_data=f"report_status|{report_doc['_id']}|processing"),
                InlineKeyboardButton("✅ Resolve", callback_data=f"report_status|{report_doc['_id']}|resolved")
            ],
            [
                InlineKeyboardButton("❌ Delete", callback_data=f"report_delete|{report_doc['_id']}")
            ],
            [
                InlineKeyboardButton("👤 User Reports", callback_data=f"report_view_user|{report_doc['user']['user_id']}")
            ]
        ]
        
        if report_doc['report'].get('show_name'):
            buttons.append([
                InlineKeyboardButton("🔍 Same Show", callback_data=f"report_search_show|{report_doc['report']['show_name']}")
            ])
        
        await message.reply(msg, reply_markup=InlineKeyboardMarkup(buttons))
        
    except Exception as e:
        logger.exception("admin_report_detail error: %s", e)
        await message.reply("❌ Error loading report.")



@app.on_message(filters.command("report_user") & admin_filter & filters.private)
async def admin_user_reports(client, message: Message):
    """View all reports by a specific user."""
    try:
        args = message.text.split()
        if len(args) < 2:
            return await message.reply("Usage: `/report_user <user_id>`")
        
        user_id = int(args[1])
        
        # Query reports
        query = {"user.user_id": user_id}
        reports, total_pages = paginate_reports(query, page=1, items_per_page=5)
        
        if not reports:
            return await message.reply(f"No reports found for user {user_id}.")
        
        # Format message
        user_info = reports[0]['user']
        msg = f"👤 **Reports by {user_info.get('full_name', 'Unknown')}** (@{user_info.get('username', 'N/A')})\n\n"
        
        for report in reports:
            report_id = str(report['_id'])
            report_data = report.get('report', {})
            status = report.get('status', 'pending')
            
            status_emoji = {"pending": "🔴", "processing": "🟡", "resolved": "🟢"}.get(status, "⚪")
            
            msg += f"{status_emoji} **#{report_id[-6:]}** - {status.upper()}\n"
            if report_data.get('show_name'):
                msg += f"   📺 {report_data['show_name']}\n"
            msg += f"   💬 {report_data.get('issue', 'No description')[:50]}...\n\n"
        
        await message.reply(msg)
        
    except Exception as e:
        logger.exception("admin_user_reports error: %s", e)
        await message.reply("❌ Error loading user reports.")



@app.on_message(filters.command("report_search") & admin_filter & filters.private)
async def admin_search_reports(client, message: Message):
    """Search reports by ID or keyword with inline buttons."""
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            return await message.reply("Usage: `/report_search <keyword or #id>`")
        
        keyword = args[1].strip()
        reports = []
        
        # Try to search by report ID first (with or without #)
        search_id = keyword.lstrip('#').lower()
        if search_id and (len(search_id) >= 3):  # At least 3 chars to match last 6 of ObjectId
            try:
                # Fetch all reports and filter by ID suffix in Python
                all_reports = list(reports_collection.find().sort("created_at", -1))
                for report in all_reports:
                    report_id_str = str(report['_id']).lower()
                    # Match by last 6 chars or anywhere in the full ID
                    if report_id_str.endswith(search_id) or search_id in report_id_str:
                        reports.append(report)
                        if len(reports) >= 10:  # Limit to 10
                            break
            except:
                pass
        
        # If no ID match, try text search
        if not reports:
            try:
                query = {
                    "$or": [
                        {"report.show_name": {"$regex": keyword, "$options": "i"}},
                        {"report.issue": {"$regex": keyword, "$options": "i"}},
                        {"user.full_name": {"$regex": keyword, "$options": "i"}},
                        {"user.username": {"$regex": keyword, "$options": "i"}}
                    ]
                }
                reports = list(reports_collection.find(query).sort("created_at", -1).limit(10))
            except:
                pass
        
        if not reports:
            return await message.reply(f"❌ No reports found for '{keyword}'.")
        
        # Display summary first
        summary = f"🔍 **Search Results for '{keyword}'** ({len(reports)} found)\n\n"
        for idx, report in enumerate(reports, 1):
            report_id = str(report['_id'])
            user = report.get('user', {})
            report_data = report.get('report', {})
            status = report.get('status', 'pending')
            status_emoji = {"pending": "🔴", "processing": "🟡", "resolved": "🟢"}.get(status, "⚪")
            
            summary += f"{idx}. {status_emoji} **#{report_id[-6:]}** - {user.get('full_name', 'Unknown')}\n"
            if report_data.get('show_name'):
                summary += f"   📺 {report_data['show_name']}\n"
            summary += f"   💬 {report_data.get('issue', 'No description')[:60]}\n\n"
        
        await message.reply(summary)
        
        # Display each report with full details and buttons
        for report in reports:
            msg = format_report_message(report)
            report_id_str = str(report['_id'])
            
            buttons = [
                [
                    InlineKeyboardButton("🔴 Pending", callback_data=f"report_status|{report_id_str}|pending"),
                    InlineKeyboardButton("🟡 Processing", callback_data=f"report_status|{report_id_str}|processing"),
                    InlineKeyboardButton("🟢 Resolved", callback_data=f"report_status|{report_id_str}|resolved")
                ],
                [
                    InlineKeyboardButton("❌ Delete", callback_data=f"report_delete|{report_id_str}")
                ],
                [
                    InlineKeyboardButton("👤 User Reports", callback_data=f"report_view_user|{report['user']['user_id']}")
                ]
            ]
            
            if report['report'].get('show_name'):
                buttons.append([
                    InlineKeyboardButton("🔍 Same Show", callback_data=f"report_search_show|{report['report']['show_name']}")
                ])
            
            await message.reply(msg, reply_markup=InlineKeyboardMarkup(buttons))
        
    except Exception as e:
        logger.exception("admin_search_reports error: %s", e)
        await message.reply("❌ Error searching reports.")



@app.on_message(filters.command("reports_all") & admin_filter & filters.private)
async def admin_all_reports(client, message: Message):
    """View all reports (paginated)."""
    try:
        reports, total_pages = paginate_reports({}, page=1, items_per_page=5)
        
        if not reports:
            return await message.reply("No reports found.")
        
        msg = f"📋 **ALL REPORTS** (Page 1/{total_pages})\n\n"
        
        for report in reports:
            report_id = str(report['_id'])
            user = report.get('user', {})
            report_data = report.get('report', {})
            status = report.get('status', 'pending')
            
            status_emoji = {"pending": "🔴", "processing": "🟡", "resolved": "🟢"}.get(status, "⚪")
            
            msg += f"{status_emoji} **#{report_id[-6:]}** - {user.get('full_name', 'Unknown')}\n"
            if report_data.get('show_name'):
                msg += f"   📺 {report_data['show_name']}\n"
            msg += f"   💬 {report_data.get('issue', 'No description')[:50]}...\n\n"
        
        # Pagination buttons
        buttons = []
        if total_pages > 1:
            buttons.append([InlineKeyboardButton("Next ➡️", callback_data="report_page|all|2")])
        
        await message.reply(msg, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)
        
    except Exception as e:
        logger.exception("admin_all_reports error: %s", e)
        await message.reply("❌ Error loading reports.")



@app.on_message(filters.command("broadcast") & admin_filter & filters.private)
async def broadcast_command(client, message: Message):
    """
    Broadcast a message to all users AND groups discovered by the bot.
    Usage: /broadcast <text> OR reply to a message with /broadcast
    Support: Text, Photo, Video, Sticker, Animation, Voice, Audio, Documents, etc.
    """
    try:
        # Check if replying to a message
        if message.reply_to_message:
            broadcast_msg = message.reply_to_message
            is_reply = True
        else:
            # Extract text from command
            text = message.text.split(maxsplit=1)
            if len(text) < 2:
                return await message.reply(
                    "**Usage:**\n"
                    "`/broadcast <text>` - Broadcast text message\n"
                    "OR reply to **ANY** message (Sticker, Photo, etc.) with `/broadcast`"
                )
            broadcast_text = text[1]
            is_reply = False
        
        status_msg = await message.reply("📡 Fetching recipients...")

        # 1. Fetch Users
        users = []
        try:
            users = list(userdb_collection.find({"is_bot": False}))
        except Exception as e:
            logger.exception(f"Error fetching users: {e}")

        # 2. Fetch Groups (aggregating from user chats)
        groups = []
        try:
            pipeline = [
                {"$unwind": "$chats"},
                {"$match": {"chats.type": {"$in": ["group", "supergroup"]}}},
                {"$group": {"_id": "$chats.chat_id", "title": {"$first": "$chats.title"}}}
            ]
            group_results = userdb_collection.aggregate(pipeline)
            groups = [{"chat_id": g["_id"], "title": g.get("title", "Group")} for g in group_results]
        except Exception as e:
            logger.exception(f"Error fetching groups: {e}")

        if not users and not groups:
            return await status_msg.edit("❌ No users or groups found.")

        # Combined list with type tag and deduplication
        seen_ids = set()
        targets = []
        
        # Add users
        for u in users:
            user_id = u["user_id"]
            if user_id not in seen_ids:
                targets.append({"id": user_id, "type": "user"})
                seen_ids.add(user_id)
        
        # Add groups (skip if user already added)
        for g in groups:
            chat_id = g["chat_id"]
            if chat_id not in seen_ids:
                targets.append({"id": chat_id, "type": "group"})
                seen_ids.add(chat_id)

        await status_msg.edit_text(f"📡 Broadcasting to {len(users)} users and {len(groups)} groups...")
        
        sent_count = 0
        failed_count = 0
        
        for idx, target in enumerate(targets):
            chat_id = target["id"]
            
            try:
                # Use rate limiter
                await notification_limiter.acquire()
                
                if is_reply:
                    # Universal Copy: Handles Text, Media, Stickers, everything
                    await broadcast_msg.copy(chat_id)
                else:
                    # Text Broadcast
                    await client.send_message(chat_id, broadcast_text)
                
                sent_count += 1
            
            except FloodWait as e:
                logger.warning(f"FloodWait: sleeping for {e.value} seconds")
                try:
                    sleep_time = int(e.value) if isinstance(e.value, (int, str)) else 5
                except (ValueError, TypeError):
                    sleep_time = 5
                await asyncio.sleep(sleep_time)
                try:
                    # Retry once
                    if is_reply:
                        await broadcast_msg.copy(chat_id)
                    else:
                        await client.send_message(chat_id, broadcast_text)
                    sent_count += 1
                except:
                    failed_count += 1
            except Exception as e:
                # Private/Kicked/Blocked
                failed_count += 1
            
            # Progress update
            if (idx + 1) % 10 == 0:
                try:
                    await status_msg.edit_text(
                        f"📡 Broadcasting... {idx + 1}/{len(targets)}\n"
                        f"✅ Sent: {sent_count}\n"
                        f"❌ Failed: {failed_count}"
                    )
                except: pass
            
            await asyncio.sleep(0.05)
        
        await status_msg.edit_text(
            f"✅ **Broadcast Complete!**\n\n"
            f"👥 Users: {len(users)}\n"
            f"📢 Groups: {len(groups)}\n"
            f"✅ Sent: {sent_count}\n"
            f"❌ Failed: {failed_count}"
        )

    except Exception as e:
        logger.exception(f"broadcast_command error: {e}")
        await message.reply("❌ Error during broadcast.")



@app.on_message(filters.command("stats") & admin_filter & filters.private)
async def admin_stats_command(client, message: Message):
    """Display bot statistics for admin."""
    try:
        # Total users
        total_users = userdb_collection.count_documents({})
        
        # Private users (where user interacted in DM)
        private_users = userdb_collection.count_documents({
            "chats.chat_id": {"$exists": True}
        })
        
        # Count unique groups
        pipeline = [
            {"$unwind": "$chats"},
            {"$match": {"chats.chat_type": {"$in": ["group", "supergroup", "ChatType.GROUP", "ChatType.SUPERGROUP"]}}},
            {"$group": {"_id": "$chats.chat_id"}},
            {"$count": "total"}
        ]
        group_result = list(userdb_collection.aggregate(pipeline))
        total_groups = group_result[0]["total"] if group_result else 0
        
        # Total favorites
        total_favorites = favorites_collection.count_documents({})
        
        # Total reports
        total_reports = reports_collection.count_documents({})
        
        # Top 10 most favorited shows
        fav_pipeline = [
            {"$group": {"_id": {"show_slug": "$show_slug", "show_name": "$show_name", "category": "$category"}, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        top_favorites = list(favorites_collection.aggregate(fav_pipeline))
        
        # Top 10 most reported shows
        report_pipeline = [
            {"$match": {"report.show_name": {"$exists": True, "$ne": ""}}},
            {"$group": {"_id": {"show_name": "$report.show_name", "category": "$report.category"}, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        top_reports = list(reports_collection.aggregate(report_pipeline))
        
        # Format message
        msg = "📊 **Bot Statistics**\n\n"
        msg += f"👥 **Users**\n"
        msg += f"├ Total Users: {total_users}\n"
        msg += f"├ Private Chats: {private_users}\n"
        msg += f"└ Groups: {total_groups}\n\n"
        
        msg += f"⭐ **Favorites:** {total_favorites}\n"
        msg += f"⚠️ **Reports:** {total_reports}\n\n"
        
        if top_favorites:
            msg += "🔥 **Top 10 Most Favorited Shows:**\n"
            for idx, item in enumerate(top_favorites, 1):
                show_info = item["_id"]
                count = item["count"]
                msg += f"{idx}. {show_info.get('show_name', 'Unknown')} ({show_info.get('category', 'N/A')}) - ⭐ {count}\n"
            msg += "\n"
        
        if top_reports:
            msg += "⚠️ **Top 10 Most Reported Shows:**\n"
            for idx, item in enumerate(top_reports, 1):
                show_info = item["_id"]
                count = item["count"]
                msg += f"{idx}. {show_info.get('show_name', 'Unknown')} ({show_info.get('category', 'N/A')}) - 🚨 {count}\n"
        
        await message.reply(msg)
        
    except Exception as e:
        logger.exception(f"admin_stats_command error: {e}")
        await message.reply("❌ Error fetching statistics.")


# ============================
# SELF-TEST DIAGNOSTIC COMMAND
# ============================

@app.on_message(filters.command("selftest") & admin_filter & filters.private)
async def self_test_command(client, message: Message):
    """Test all critical systems."""
    results = []
    
    # Test 1: MongoDB Connection
    try:
        client.admin.command('ping')
        results.append("✅ MongoDB: Connected")
    except Exception as e:
        results.append(f"❌ MongoDB: {str(e)[:50]}")
    
    # Test 2: Hash System
    try:
        test_hash = make_id("TestShow")
        resolved = resolve_id(test_hash)
        if resolved == "TestShow":
            results.append("✅ Hash System: Working")
        else:
            results.append(f"❌ Hash System: Mismatch ({resolved})")
    except Exception as e:
        results.append(f"❌ Hash System: {str(e)[:50]}")
    
    # Test 3: Storage Channel
    try:
        chat = await client.get_chat(STORAGE_CHANNEL_ID)
        results.append(f"✅ Storage Channel: {chat.title}")
    except Exception as e:
        results.append(f"❌ Storage Channel: {str(e)[:50]}")
    
    # Test 4: Data Cache
    try:
        data = load_data_cached()
        total_shows = sum(len(shows) for shows in data.values())
        results.append(f"✅ Data Cache: {total_shows} shows loaded")
    except Exception as e:
        results.append(f"❌ Data Cache: {str(e)[:50]}")
    
    # Test 5: Recent Updates
    try:
        count = len(recent_updates)
        results.append(f"✅ Recent Updates: {count} entries")
    except Exception as e:
        results.append(f"❌ Recent Updates: {str(e)[:50]}")
    
    # Test 6: Favorites System
    try:
        fav_count = favorites_collection.count_documents({})
        results.append(f"✅ Favorites: {fav_count} total")
    except Exception as e:
        results.append(f"❌ Favorites: {str(e)[:50]}")
    
    # Test 7: Reports System
    try:
        report_count = reports_collection.count_documents({})
        results.append(f"✅ Reports: {report_count} total")
    except Exception as e:
        results.append(f"❌ Reports: {str(e)[:50]}")
    
    # Test 8: Hash Cache Status
    try:
        cache_size = len(_hash_memory_cache)
        results.append(f"✅ Hash Cache: {cache_size} entries (max 10,000)")
    except Exception as e:
        results.append(f"❌ Hash Cache: {str(e)[:50]}")
    
    # Test 9: Active Tasks
    try:
        active_count = len([t for t in active_notification_tasks if not t.done()])
        results.append(f"✅ Notification Tasks: {active_count} active")
    except Exception as e:
        results.append(f"❌ Notification Tasks: {str(e)[:50]}")
    
    # Test 10: User Database
    try:
        user_count = userdb_collection.count_documents({})
        results.append(f"✅ User Database: {user_count} users")
    except Exception as e:
        results.append(f"❌ User Database: {str(e)[:50]}")
    
    msg = "🔧 **Self-Test Results**\n\n" + "\n".join(results)
    await message.reply(msg)


@app.on_message(filters.command("sync_users") & admin_filter & filters.private)
async def sync_users_command(client, message: Message):
    """Clean up user database by removing deleted/blocked users and duplicates."""
    try:
        status_msg = await message.reply("🔄 Syncing users database...")
        
        removed_count = 0
        duplicates_fixed = 0
        
        # Find and remove deleted users (no username, first_name, and is_bot is None/False)
        deleted_users = userdb_collection.find({
            "$or": [
                {"username": {"$in": [None, ""]}, "first_name": {"$in": [None, ""]}, "is_bot": {"$in": [None, False]}},
                {"user_id": None}
            ]
        })
        
        for user in deleted_users:
            userdb_collection.delete_one({"_id": user["_id"]})
            removed_count += 1
        
        # Find duplicates by user_id
        pipeline = [
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}, "docs": {"$push": "$_id"}}},
            {"$match": {"count": {"$gt": 1}}}
        ]
        
        duplicates = list(userdb_collection.aggregate(pipeline))
        
        for dup in duplicates:
            # Keep the first document, remove the rest
            docs_to_remove = dup["docs"][1:]
            for doc_id in docs_to_remove:
                userdb_collection.delete_one({"_id": doc_id})
                duplicates_fixed += 1
        
        # Rebuild indexes
        try:
            userdb_collection.drop_indexes()
            userdb_collection.create_index("user_id", unique=True)
            userdb_collection.create_index("chats.chat_id")
            userdb_collection.create_index("allow_global_notifications")
        except Exception as e:
            logger.debug(f"Index rebuild warning: {e}")
        
        # Get final count
        remaining_users = userdb_collection.count_documents({})
        
        await status_msg.edit_text(
            f"✅ **User Database Synced!**\n\n"
            f"🗑 Users Removed: {removed_count}\n"
            f"🔧 Duplicates Fixed: {duplicates_fixed}\n"
            f"👥 Remaining Users: {remaining_users}"
        )
        
    except Exception as e:
        logger.exception(f"sync_users_command error: {e}")
        await message.reply("❌ Error syncing users database.")

def normalize_season(season_text: str) -> str:
    """Normalize season to plain number format (1, 2, 3, etc.).
    
    Works with all input formats:
    - "1" → "1"
    - "S1" → "1"
    - "s1" → "1"
    - "season 1" → "1"
    """
    season_text = str(season_text).strip().lower()
    
    # Remove common prefixes: 's', 'season '
    season_text = re.sub(r'^season\s*', '', season_text)
    season_text = re.sub(r'^s', '', season_text)
    
    # Extract only numeric digits
    season_num = re.sub(r'[^0-9]', '', season_text).strip()
    
    if not season_num:
        return "1"  # default to season 1 if invalid
    
    return season_num  # Return plain number: "1", "2", "3"





@app.on_message(filters.command(list(IMPORT_CATEGORY_MAP.keys())) & admin_filter & filters.private)
async def import_handler(client, message: Message):
    """Handle import commands for adding episodes with quality options."""
    cmd = message.command[0]
    category = IMPORT_CATEGORY_MAP[cmd]

    # Expected formats:
    # /import_hindi "Show Name" S1 E3 720p
    # /import_hindi Show_Name S1 E3 720p
    # /import_hindi good boy s1 E1 480
    
    try:
        args_text = message.text.split(" ", 1)[1].strip()
    except IndexError:
        return await message.reply(
            f'**Usage:** `/{cmd} Show_Name S1 E3 720p`\n\n'
            f'**Examples:**\n'
            f'• `/{cmd} "Goblin" S1 E5 720p`\n'
            f'• `/{cmd} Goblin S1 E5 720p`\n'
            f'• `/{cmd} good_boy s1 e1 480`\n\n'
            f'**Note:** Use underscores for spaces in show names'
        )

    # Parse with flexible regex - supports both quoted and unquoted names
    import re
    
    # Try quoted format first: "Show Name" S1 E3 720p
    match = re.match(r'"([^"]+)"\s+[Ss](\d+)\s+[Ee](\d+)\s+(\d+)p?', args_text)
    if match:
        show_name_str = match.group(1)
        season_str = match.group(2)
        episode_str = match.group(3)
        quality_label = match.group(4) + "p" if not match.group(4).endswith("p") else match.group(4)
    else:
        # Try unquoted format: Show_Name S1 E3 720p or good boy s1 e1 480
        match = re.match(r'(.+?)\s+[Ss](\d+)\s+[Ee](\d+)\s+(\d+)p?', args_text)
        if not match:
            return await message.reply(
                f'❌ **Invalid format**\n\n'
                f'**Usage:** `/{cmd} Show_Name S1 E3 720p`\n\n'
                f'**Examples:**\n'
                f'• `/{cmd} "Goblin" S1 E5 720p`\n'
                f'• `/{cmd} Goblin S1 E5 720p`\n'
                f'• `/{cmd} good_boy s1 e1 480`\n\n'
                f'**Your input:** `{args_text}`'
            )
        
        show_name_str = match.group(1).strip()
        season_str = match.group(2)
        episode_str = match.group(3)
        quality_label = match.group(4) + "p" if not match.group(4).endswith("p") else match.group(4)
    
    # Convert to proper format
    show_name_input = show_name_str  # Keep original input
    season_number = f"S{season_str}"
    episode_number = int(episode_str)
    episode_index = episode_number - 1
    
    # Validate show exists in the specified category
    data = load_data_cached()
    
    if category not in data:
        return await message.reply(
            f"❌ Category '{category}' not found in database.\n"
            f"Available categories: {', '.join(data.keys())}"
        )
    
    # Try to find the show (case-insensitive, flexible matching)
    # Check both "good boy" and "good_boy" formats
    show_found = None
    search_variations = [
        show_name_input,                          # Original: "good boy"
        show_name_input.replace(" ", "_"),        # With underscores: "good_boy"
        show_name_input.replace("_", " "),        # With spaces: "good boy"
    ]
    
    for show_key in data[category].keys():
        for variation in search_variations:
            if show_key.lower() == variation.lower():
                show_found = show_key
                break
        if show_found:
            break
    
    if not show_found:
        # Show not found - provide helpful message
        available_shows = list(data[category].keys())[:10]  # Show first 10
        shows_list = "\n• ".join(available_shows)
        return await message.reply(
            f"❌ Show '{show_name_str}' not found in **{category}**.\n\n"
            f"**Available shows (first 10):**\n• {shows_list}\n\n"
            f"💡 **Tip:** Use `/add` command to add a new show first."
        )
    
    # Use the actual show name from database
    show_name = show_found
    
    # Validate season exists - check both "S1" and "1" formats
    show_data = data[category][show_name]
    
    # Normalize the season input to plain number format
    season_normalized = normalize_season(season_str)
    
    # Try to find matching season in database (database uses plain numbers like "1", "2", etc.)
    season_found = None
    
    for db_season_key in show_data.keys():
        # Skip non-season keys
        if db_season_key in ["poster", "episodes"]:
            continue
        
        # Normalize the database key and compare
        db_season_normalized = normalize_season(str(db_season_key))
        
        if db_season_normalized == season_normalized:
            season_found = db_season_key  # Use the actual key from database
            break
    
    if not season_found:
        # Season not found - show available seasons
        available_seasons = [str(k) for k in show_data.keys() if k not in ["poster", "episodes"]]
        if available_seasons:
            seasons_list = ", ".join(available_seasons)
            return await message.reply(
                f"❌ Season {season_str} not found for '{show_name.replace('_', ' ')}'.\n\n"
                f"**Available seasons:** {seasons_list}\n\n"
                f"💡 **Tip:** Add the season first if it doesn't exist."
            )
        else:
            return await message.reply(
                f"❌ No seasons found for '{show_name.replace('_', ' ')}'.\n\n"
                f"💡 **Tip:** Add a season first before importing episodes."
            )
    
    # Use the actual season key from database (plain number format)
    season_key = season_found
    
    # Validate episode index is reasonable (not too high)
    current_episodes = show_data.get(season_key, [])
    if isinstance(current_episodes, list) and episode_index > len(current_episodes) + 10:
        return await message.reply(
            f"⚠️ Episode {episode_number} seems too high.\n"
            f"Current episodes in {season_key}: {len(current_episodes)}\n\n"
            f"Are you sure you want to add episode {episode_number}?"
        )
    
    # Set import state
    import_state[message.from_user.id] = {
        "category": category,
        "show": show_name,
        "season": season_key,  # Use actual season key from database
        "episode_index": episode_index,
        "quality": quality_label,
        "created": time.time()
    }

    await message.reply(
        f"✅ **Import mode activated**\n\n"
        f"📺 Show: **{show_name.replace('_', ' ')}**\n"
        f"📂 Category: **{category}**\n"
        f"🎬 Episode: **{season_key} Ep{episode_number}**\n"
        f"🎞 Quality: **{quality_label}**\n\n"
        f"📤 **Now send:**\n"
        f"• Video file\n"
        f"• Video document\n"
        f"• HTTP link to video\n\n"
        f"⏳ Waiting for your upload..."
    )







# ============================
# FINAL IMPORT HANDLER
# ============================
@app.on_message(filters.private, group=1)
async def handle_import_receive(client, message: Message):
    # 1. Check if it's an admin
    user_id = message.from_user.id if message.from_user else None
    if not user_id or user_id not in ADMIN_IDS:
        return

    # 2. Check if in import mode
    state = import_state.get(user_id)
    if not state:
        return

    # 3. Ignore commands (like the /import command itself)
    if message.text and message.text.startswith("/"):
        return

    category = state["category"]
    show_name = state["show"]
    season = state["season"]
    ep_index = state["episode_index"]
    quality = state["quality"]

    # Show status msg
    processing_msg = await message.reply("⏳ Processing...")

    file_type = None
    saved_content = None

    # ------------------------------------
    # CASE 1: forwarded or direct VIDEO
    # ------------------------------------
    if message.video:
        try:
            # Forward to storage channel to preserve original file
            fwd = await message.forward(STORAGE_CHANNEL_ID, disable_notification=True)
            
            if not fwd.video:
                return await processing_msg.edit("❌ Failed to store video.")

            saved_content = fwd.video.file_id
            file_type = "video"
            
            # Delete the forwarded message to keep storage channel clean (optional)
            try:
                await fwd.delete()
            except:
                pass
        except Exception as e:
            logger.exception(f"Error storing video: {e}")
            await processing_msg.edit(f"❌ Storage error: {e}")
            return

    # ------------------------------------
    # CASE 2: forwarded or direct VIDEO DOCUMENT
    # ------------------------------------
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("video/"):
        try:
            # Forward document to storage channel
            fwd = await message.forward(STORAGE_CHANNEL_ID, disable_notification=True)
            
            if not fwd.document:
                return await processing_msg.edit("❌ Failed to store document.")

            saved_content = fwd.document.file_id
            file_type = "document"  # NEW: Identifying as document type
            
            # Clean up storage channel
            try:
                await fwd.delete()
            except:
                pass

        except Exception as e:
            logger.exception(f"Error storing document: {e}")
            await processing_msg.edit(f"❌ Storage error: {e}")
            return

    # ------------------------------------
    # CASE 3: http/https link
    # ------------------------------------
    elif message.text and message.text.strip().lower().startswith("http"):
        saved_content = message.text.strip()
        file_type = "link"

    else:
        return await processing_msg.edit("❌ Unsupported format. Send Video / Video file / Link.")

    # ------------------------------------
    # SAVE INTO DB (multi-quality)
    # ------------------------------------
    data = load_data_cached()
    if category not in data or show_name not in data[category]:
        import_state.pop(user_id, None)
        return await processing_msg.edit("❌ Show not found (maybe renamed?).")

    show_data = data[category][show_name]
    season_list = show_data.setdefault(season, [])

    while len(season_list) <= ep_index:
        season_list.append(None)

    entry = season_list[ep_index]
    # Track if this is a NEW episode (was None) vs adding quality to existing
    is_new_episode = (entry is None)
    
    episode_data, _ = convert_legacy_to_quality(entry)

    episode_data["qualities"][quality] = {
        "type": file_type,
        "content": saved_content,
    }

    season_list[ep_index] = episode_data

    try:
        backup_database()
        save_data(data)
        
        # Track this upload in recent updates
        add_recent_update(category, show_name, season, ep_index + 1)
        
        # Send notifications to users ONLY if this is a NEW episode
        if is_new_episode:
            try:
                # Count total episodes in this season to determine if it's a new show
                episode_count = len(season_list)
                is_new_show = (episode_count == 1)
                
                # Send notifications asynchronously
                show_slug = normalize_show_slug(show_name)
                asyncio.create_task(
                    notify_new_content(
                        show_name,
                        category,
                        show_slug,
                        is_new_show,
                        episode_count,
                        client
                    )
                )
            except Exception as e:
                logger.debug(f"Notification hook error: {e}")


        
    except Exception as e:
        logger.exception(f"Error saving to database: {e}")
        import_state.pop(user_id, None)
        return await processing_msg.edit(f"❌ Save Error: {e}")

    import_state.pop(user_id, None)

    await processing_msg.edit(
        f"✅ **Saved successfully!**\n"
        f"📺 *{show_name.replace('_', ' ')}*\n"
        f"🎬 Episode: S{season} Ep{ep_index+1}\n"
        f"🎞 Quality: **{quality}**\n"
        f"📦 Type: **{file_type}**"
    )





# ============================
# STARTUP HOOK
# ============================
@app.on_message(filters.command("start_bot") & admin_filter)
async def manual_start_hook(client, message):
    """Manual trigger to cache peers if needed."""
    try:
        chat = await client.get_chat(STORAGE_CHANNEL_ID)
        await message.reply(f"✅ Connected to Storage Channel: {chat.title}")
    except Exception as e:
        await message.reply(f"❌ Could not connect to Storage Channel: {e}")



@app.on_message(filters.command("view_report") & admin_filter & filters.private)
async def view_report_command(client, message):
    """View a specific report by ID (Re-open Panel)."""
    logger.info(f"DEBUG: view_report_command triggered by {message.from_user.id} with text: {message.text}")
    try:
        report_id_str = message.text.replace("/view_report", "").strip()
        if not report_id_str:
             return await message.reply("Usage: /view_report <report_id>")
             
        # Cleanup input (remove # if present)
        clean_id = report_id_str.replace("#", "")
        
        report_doc = None
        
        # 1. Try exact ObjectId
        try:
            from bson.objectid import ObjectId
            if len(clean_id) == 24:
                report_doc = reports_collection.find_one({"_id": ObjectId(clean_id)})
        except:
            pass
            
        # 2. Try partial match (last 6 chars or contains) -> Scan recent reports
        if not report_doc:
            # This is expensive if many reports, but fine for admin tool
            # Find recent 100 reports and check stringified ID
            cursor = reports_collection.find().sort("created_at", -1).limit(100)
            for r in cursor:
                str_id = str(r["_id"])
                if clean_id in str_id:
                    report_doc = r
                    break
                    
        if not report_doc:
            return await message.reply(f"❌ Report not found for ID: `{clean_id}`")
            
        admin_msg = format_report_message(report_doc)
        user_id = report_doc['user']['user_id']
        show_name = report_doc['report'].get('show_name', '')
        report_id = report_doc['_id']
        
        # Generate fresh buttons with 'report' context
        buttons = [
            [
                InlineKeyboardButton("🧠 Deep Analysis", callback_data=f"max_profile_{user_id}|report_{report_id}"),
                InlineKeyboardButton("🗂 History", callback_data=f"user_history_{user_id}|report_{report_id}")
            ],
            [
                InlineKeyboardButton("✅ Processing", callback_data=f"report_status|{report_id}|processing"),
                InlineKeyboardButton("✅ Resolve", callback_data=f"report_status|{report_id}|resolved")
            ],
            [
                InlineKeyboardButton("❌ Delete", callback_data=f"report_delete|{report_id}")
            ],
            [
                InlineKeyboardButton("👤 User Reports", callback_data=f"report_view_user|{user_id}"),
                InlineKeyboardButton("🔍 Same Show", callback_data=f"report_search_show|{show_name}")
            ]
        ]
        
        await message.reply(admin_msg, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)
        
    except Exception as e:
        logger.exception(f"view_report error: {e}")
        await message.reply("Error viewing report.")

@app.on_callback_query(filters.regex(r"^max_profile_"))
async def view_max_profile(client, callback_query):
    """
    Marker 2: Show the Deep Analysis text block.
    Supports context (search vs report).
    """
    try:
        # Data format: max_profile_{user_id}|{context}
        parts = callback_query.data.split("|")
        base_part = parts[0]
        context = parts[1] if len(parts) > 1 else "search"
        
        user_id = int(base_part.split("_")[2])
        
        # Regenerate profile to get latest stats
        profile = calculate_max_profile(user_id)
        text_report = format_max_profile_text(profile, user_id)
        
        # Determine back button behavior
        if context.startswith("report_"):
            report_id = context.replace("report_", "")
            back_btn = InlineKeyboardButton("🔙 Back to Report", callback_data=f"restore_report_{report_id}")
        else:
            back_btn = InlineKeyboardButton("🔙 Back to Profile", callback_data=f"profile_back_{user_id}")
            
        buttons = [[back_btn]]
        
        # Edit the message
        try:
            await callback_query.message.edit_caption(
                caption=text_report,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except:
             await callback_query.message.edit_text(
                text=text_report,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            
    except Exception as e:
        logger.exception(f"view_max_profile error: {e}")
        await callback_query.answer("Error loading profile")

@app.on_callback_query(filters.regex(r"^user_history_"))
async def view_user_history(client, callback_query):
    """
    Marker 3: Show report history.
    """
    try:
        parts = callback_query.data.split("|")
        base_part = parts[0]
        context = parts[1] if len(parts) > 1 else "search"
        
        user_id = int(base_part.split("_")[2])
        
        cursor = reports_collection.find({"user.user_id": user_id}).sort("created_at", -1).limit(5)
        reports = list(cursor)
        
        if not reports:
            msg = f"📂 **REPORT HISTORY**\nID: `{user_id}`\n\nNo reports found."
        else:
            msg = f"📂 **REPORT HISTORY ({len(reports)} found)**\nID: `{user_id}`\n\n"
            for r in reports:
                status_emoji = {"pending": "🔴", "processing": "🟡", "resolved": "🟢"}.get(r.get('status'), "⚪")
                date_str = r['created_at'].strftime('%Y-%m-%d')
                issue = r['report'].get('issue', 'No issue')[:50]
                msg += f"{status_emoji} `{date_str}`: {issue}...\n"
        
        if context.startswith("report_"):
            report_id = context.replace("report_", "")
            back_btn = InlineKeyboardButton("🔙 Back to Report", callback_data=f"restore_report_{report_id}")
        else:
            back_btn = InlineKeyboardButton("🔙 Back to Profile", callback_data=f"profile_back_{user_id}")
            
        buttons = [[back_btn]]
        
        try:
            await callback_query.message.edit_caption(
                caption=msg,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except:
             await callback_query.message.edit_text(
                text=msg,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            
    except Exception as e:
        logger.exception(f"view_user_history error: {e}")

@app.on_callback_query(filters.regex(r"^profile_back_(\d+)"))
async def back_to_profile(client, callback_query):
    """Return to Marker 1 (Search Result Style)."""
    try:
        user_id = int(callback_query.data.split("_")[2])
        user_doc = userdb_collection.find_one({"user_id": user_id})
        
        # Fallback to empty dict if user not found
        if not user_doc:
            user_doc = {}
        
        # Re-fetch live data if possible (simplified here)
        chat = None
        try:
            chat = await client.get_chat(user_id)
        except: pass
        
        user_status = getattr(chat, "status", "Unknown") if chat else "Unknown"
        photo_present = chat.photo is not None if chat else False
        dc_id = getattr(chat, "dc_id", "Unknown") if chat else "Unknown"
        is_premium = getattr(chat, "is_premium", False) if chat else False
        
        caption = (
            f"👤 **USER MAX-PROFILE**\n"
            f"🆔 ID: `{user_id}`\n"
            f"👤 First Name: {user_doc.get('first_name', 'Unknown')}\n"
            f"👤 Last Name: {user_doc.get('last_name', 'Unknown')}\n"
            f"📛 Username: @{user_doc.get('username', 'None')}\n"
            f"📝 Full Name: {user_doc.get('full_name', 'Unknown')}\n\n"
            f"💎 Premium: {'✅ Yes' if is_premium else '❌ No'}\n"
            f"🌐 Language: {user_doc.get('language_code', 'Unknown')}\n"
            f"🏢 DC ID: `{dc_id}`\n"
            f"🖼 Photo Present: {'✅ Yes' if photo_present else '❌ No'}\n"
            f"📊 Status: {str(user_status)}\n"
            f"⏱ Last Online: {user_doc.get('last_interaction', datetime.now()).strftime('%Y-%m-%d %H:%M')}\n"
            f"👇 **ACTIONS:**"
        )
        
        buttons = [
            [InlineKeyboardButton("🧠 Deep Analysis (Max-Profile)", callback_data=f"max_profile_{user_id}|search")],
            [InlineKeyboardButton("🗂 Report History", callback_data=f"user_history_{user_id}|search")],
            [InlineKeyboardButton("🚫 Ban User (Mock)", callback_data="noop")]
        ]
        
        try:
            await callback_query.message.edit_caption(
                caption=caption,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except:
             await callback_query.message.edit_text(
                text=caption,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            
    except Exception as e:
        logger.exception(f"back_to_profile error: {e}")

@app.on_callback_query(filters.regex(r"^restore_report_"))
async def restore_report_panel(client, callback_query):
    """Restore the original Report Panel (Image 1 style)."""
    try:
        report_id_str = callback_query.data.replace("restore_report_", "")
        from bson.objectid import ObjectId
        try:
            report_id = ObjectId(report_id_str)
        except:
            return await callback_query.answer("Invalid Report ID")
            
        report_doc = reports_collection.find_one({"_id": report_id})
        if not report_doc:
            return await callback_query.answer("Report not found (deleted?)", show_alert=True)
            
        admin_msg = format_report_message(report_doc)
        user_id = report_doc['user']['user_id']
        show_name = report_doc['report'].get('show_name', '')
        
        buttons = [
            [
                InlineKeyboardButton("🧠 Deep Analysis", callback_data=f"max_profile_{user_id}|report_{report_id_str}"),
                InlineKeyboardButton("🗂 History", callback_data=f"user_history_{user_id}|report_{report_id_str}")
            ],
            [
                InlineKeyboardButton("✅ Processing", callback_data=f"report_status|{report_id}|processing"),
                InlineKeyboardButton("✅ Resolve", callback_data=f"report_status|{report_id}|resolved")
            ],
            [
                InlineKeyboardButton("❌ Delete", callback_data=f"report_delete|{report_id}")
            ],
            [
                InlineKeyboardButton("👤 User Reports", callback_data=f"report_view_user|{user_id}"),
                InlineKeyboardButton("🔍 Same Show", callback_data=f"report_search_show|{show_name}")
            ]
        ]
        
        try:
            # If original was a photo (profile photo used), we might need to edit caption
            if callback_query.message.photo:
                await callback_query.message.edit_caption(
                    caption=admin_msg,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
            else:
                 await callback_query.message.edit_text(
                    text=admin_msg,
                    reply_markup=InlineKeyboardMarkup(buttons),
                    disable_web_page_preview=True
                )
        except Exception:
             # Fallback: if we can't edit (e.g. Type mismatch), send fresh
             await callback_query.message.delete()
             await client.send_message(
                chat_id=callback_query.message.chat.id,
                text=admin_msg,
                reply_markup=InlineKeyboardMarkup(buttons)
             )
        
    except Exception as e:
        logger.exception(f"restore_report_panel error: {e}")
        await callback_query.answer("Error restoring report")

@app.on_message(filters.command(["add", "add_hindi", "add_regional", "add_jap", "add_c", "add_arb", "add_pak", "add_anime"]) & admin_filter & filters.private)

async def add_show(client, message):
    cmd = message.command[0]
    if len(message.command) < 2:
        return await message.reply(
            "Usage:\n"
            "/add Show Name\n"
            "/add Show Name > Category\n"
            "/add Show Name 1\n"
            "/add Show Name > Category 1\n"
            "/add_hindi Show Name\n"
            "/add_regional Show Name\n"
            "/add_jap Show Name\n"
            "/add_c Show Name\n"
            "/add_arb Show Name\n"
            "/add_pak Show Name\n"
            "/add_anime Show Name"
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
    elif cmd == "add_pak":
        category = "Pakistan"
    elif cmd == "add_anime":
        category = "Anime"

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
    "regional": "Regional",
    "pak": "Pakistan",
    "pakistan": "Pakistan",
    "anime": "Anime"
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
    "arb": "arb",
    "Pakistan": "pak",
    "pak": "pak",
    "Anime": "anime",
    "anime": "anime"
}


@app.on_message(filters.command([
    "upload", "upload_hindi", "upload_regional",
    "upload_jap", "upload_c", "upload_arb", "upload_pak", "upload_anime"
]) & admin_filter & filters.private)
# ============================
# 7. FIXED SEASON HANDLER
# ============================

@app.on_callback_query(filters.regex(r"^season\|"))
async def season_handler(client, callback_query):
    """Handle season selection with back button."""
    try:
        parts = callback_query.data.split("|")
        if len(parts) < 4:
            return await callback_query.answer("Invalid season data.", show_alert=True)
        
        enc_cat = parts[1]
        enc_show = parts[2]
        enc_season = parts[3]
        
        category = resolve_id(enc_cat)
        show_name = resolve_id(enc_show)
        season = resolve_id(enc_season)
        
        data = load_data_cached()
        
        if category not in data or show_name not in data[category]:
            return await callback_query.answer("Show not found.", show_alert=True)
        
        if season not in data[category][show_name]:
            return await callback_query.answer("Season not found.", show_alert=True)
        
        episodes = data[category][show_name][season]
        
        if not isinstance(episodes, list) or not episodes:
            return await callback_query.answer("No episodes found.", show_alert=True)
        
        buttons = []
        for idx, ep in enumerate(episodes, start=1):
            if isinstance(ep, list):
                buttons.append([InlineKeyboardButton(
                    f"📂 Episode {idx} (split)",
                    callback_data=f"multi|{enc_cat}|{enc_show}|{enc_season}|{idx}"
                )])
            else:
                buttons.append([InlineKeyboardButton(
                    f"▶️ Episode {idx}",
                    callback_data=f"episode|{enc_cat}|{enc_show}|{enc_season}|{idx}"
                )])
        
        # ✅ Back button
        buttons.append([InlineKeyboardButton(
            "🔙 Back to Show",
            callback_data=f"show|{enc_cat}|{enc_show}"
        )])
        
        await callback_query.message.edit_text(
            f"🎬 **{show_name}**\n📂 Season {season}\n\n▶️ Select Episode:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.exception(f"season_handler error: {e}")
        await callback_query.answer("Error loading season.", show_alert=True)


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
        await callback_query.message.edit_text(
            caption,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    await callback_query.answer()

@app.on_inline_query()
async def inline_query_search(client, inline_query):
    """Universal inline search that works everywhere (DM, group, channels)."""
    query = (inline_query.query or "").strip()
    
    if not query:
        return await inline_query.answer([], cache_time=5, is_personal=True)

    try:
        # Perform MongoDB + fuzzy search
        results = search_drama(query, limit=20)
        if not results:
            # No matches → suggest correction text
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
        me = await client.get_me()
        bot_username = me.username

        for idx, doc in enumerate(results):
            title = doc.get("show_name", "").replace("_", " ")
            desc = (doc.get("description") or "").strip()
            category = doc.get("category", "")

            # Build deep-link using unified base64 slug system
            safe_cat = category.replace(" ", "_")
            safe_show = normalize_show_slug(title)
            start_payload = f"{safe_cat}__{safe_show}"
            url = f"https://t.me/{bot_username}?start={start_payload}"

            # show inline card result
            articles.append(
                InlineQueryResultArticle(
                    id=str(idx),
                    title=title,
                    description=f"{category} | {(desc[:70] + '...') if desc else ''}",
                    input_message_content=InputTextMessageContent(
                        f"🎬 <b>{title}</b>\n📂 {category}\n\nTap below to open details 👇",
                        parse_mode=ParseMode.HTML
                    ),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📺 Open in Bot", url=url)]
                    ])
                )
            )

        await inline_query.answer(articles, cache_time=10, is_personal=False)
    except Exception as e:
        logger.exception("inline_query_search error: %s", e)
        await inline_query.answer([], cache_time=5, is_personal=True)

@app.on_chosen_inline_result()
async def track_chosen_inline_result(client, chosen_inline_result):
    """Track where user sends inline results for auto-recreation feature."""
    try:
        user_id = chosen_inline_result.from_user.id
        # chosen_inline_result doesn't have chat info either!
        # We need a different approach - store in callback data or use message context
        pass
    except Exception as e:
        logger.debug(f"track_chosen_inline_result error: {e}")


def search_drama(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """
    K-drama search with strict matching:
    1. Exact word matches (high priority)
    2. Partial word matches (medium priority)
    3. Fuzzy matching only if no results (low priority, strict 70%+ threshold)
    
    Returns list of documents sorted by relevance score.
    """
    q = (query or "").strip().lower()
    if not q or len(q) < 1:
        return []

    scored_results = {}  # key = show_name, value = (doc, score)
    
    try:
        # Get all shows from MongoDB (only those with episodes)
        all_shows_cursor = collection.find(
            {"show_name": {"$exists": True}},
            {"category": 1, "show_name": 1, "poster": 1, "description": 1}
        )
        all_shows = list(all_shows_cursor)
        
        if not all_shows:
            return []
        
        # Priority 1: EXACT word match (highest score)
        for doc in all_shows:
            show_name = doc.get("show_name", "").lower()
            category = doc.get("category", "").lower()
            
            if show_name == q:  # Perfect exact match
                scored_results[doc.get("show_name", "")] = (doc, 100)
            elif q in show_name.split():  # Exact word in the name
                scored_results[doc.get("show_name", "")] = (doc, 90)
            elif category == q:  # Category match
                scored_results[doc.get("show_name", "")] = (doc, 85)
        
        # Priority 2: PARTIAL/SUBSTRING matches
        if len(scored_results) < limit:
            for doc in all_shows:
                show_name = doc.get("show_name", "").lower()
                if doc.get("show_name", "") not in scored_results:
                    # Check if query is substring of show name
                    if q in show_name and len(q) >= 2:
                        scored_results[doc.get("show_name", "")] = (doc, 80)
                    # Check if show name starts with query
                    elif show_name.startswith(q) and len(q) >= 2:
                        scored_results[doc.get("show_name", "")] = (doc, 75)
        
        # Priority 3: FUZZY matching only if we need more results (strict 70% threshold)
        if len(scored_results) < limit:
            try:
                from rapidfuzz import process, fuzz
                all_show_names = [doc.get("show_name", "") for doc in all_shows]
                fuzzy_matches = process.extract(
                    q, 
                    all_show_names, 
                    scorer=fuzz.token_set_ratio, 
                    limit=limit * 2
                )
                
                for match_name, score, _ in fuzzy_matches:
                    if score >= 70 and match_name not in scored_results:  # STRICT: 70% threshold
                        doc = collection.find_one(
                            {"show_name": match_name},
                            {"category": 1, "show_name": 1, "poster": 1, "description": 1}
                        )
                        if doc:
                            scored_results[match_name] = (doc, int(score))
            except ImportError:
                # Fallback to difflib if rapidfuzz not available
                from difflib import SequenceMatcher
                for doc in all_shows:
                    show_name = doc.get("show_name", "")
                    if show_name not in scored_results:
                        ratio = SequenceMatcher(None, q, show_name.lower()).ratio()
                        if ratio >= 0.70:  # STRICT: 70% threshold
                            scored_results[show_name] = (doc, int(ratio * 100))
        
        # Sort by score (descending) and return top results
        sorted_results = sorted(scored_results.values(), key=lambda x: x[1], reverse=True)
        results = [doc for doc, _ in sorted_results[:limit]]
        
        return results
        
    except Exception as e:
        logger.debug(f"search_drama error: {e}")
        return []


# ============================
# SEARCH COMMAND HANDLER
# ============================

@app.on_message(filters.command("search"))
async def search_command(client, message: Message):
    """
    Search for shows/dramas by name.
    Usage: /search <show name>
    """
    try:
        # Track user interaction
        await upsert_user_from_context(message.from_user, message.chat)
        
        if len(message.command) < 2:
            return await message.reply(
                "🔍 **Search for Shows**\n\n"
                "**Usage:** `/search <show name>`\n\n"
                "**Examples:**\n"
                "• `/search vincenzo`\n"
                "• `/search good boy`\n"
                "• `/search squid game`"
            )
        
        query = " ".join(message.command[1:]).strip()
        
        if len(query) < 2:
            return await message.reply("❌ Please enter at least 2 characters to search.")
        
        # Show searching message
        searching_msg = await message.reply(f"🔍 Searching for: **{query}**...")
        
        # Search using existing search_drama function
        results = search_drama(query, limit=10)
        
        if not results:
            return await searching_msg.edit(
                f"❌ **No results found for:** `{query}`\n\n"
                "Try:\n"
                "• Checking your spelling\n"
                "• Using different keywords\n"
                "• Browsing categories with /start"
            )
        
        # Build results message
        msg = f"🔍 **Search Results for:** `{query}`\n\n"
        msg += f"**Found {len(results)} show(s):**\n\n"
        
        buttons = []
        
        for idx, show in enumerate(results, 1):
            show_name = show.get("show_name", "Unknown")
            category = show.get("category", "Unknown")
            
            # Format show name for display
            display_name = show_name.replace("_", " ").title()
            
            msg += f"{idx}. 🎬 **{display_name}**\n"
            msg += f"   📂 {category}\n\n"
            
            # Add button to open show
            # Unified deep link format: https://t.me/Bot?start=category__show
            safe_cat = category.replace(" ", "_")
            safe_show = normalize_show_slug(show_name)
            start_payload = f"{safe_cat}__{safe_show}"
            
            # Get bot username for deep link
            try:
                me = await client.get_me()
                bot_username = me.username
            except Exception:
                bot_username = "unknown_bot"
                
            # Using a simplified URL construction valid for Telegram apps
            url = f"https://t.me/{bot_username}?start={start_payload}"

            buttons.append([
                InlineKeyboardButton(
                    f"▶ Open {display_name}",
                    url=url
                )
            ])

        # Add back button
        buttons.append([
            InlineKeyboardButton("🏠 Back to Categories", callback_data="back_to_categories")
        ])
        
        await searching_msg.edit(
            msg,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    except Exception as e:
        logger.exception(f"search_command error: {e}")
        await message.reply("❌ Error searching. Please try again.")


# ============================
# END SEARCH COMMAND
# ============================


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
            buttons.append([InlineKeyboardButton("🅰️ Part 1", callback_data=f"splitpart_{category}_{show_name}_{key}_{index}_0")])
        if parts[1]:
            buttons.append([InlineKeyboardButton("🅱️ Part 2", callback_data=f"splitpart_{category}_{show_name}_{key}_{index}_1")])

        await callback_query.message.edit_text(
            f"🎬 {show_name} - Episode {index + 1} Split Parts",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        print("split_parts_view error:", e)
        await callback_query.answer("Error opening split episode.")

# ============================
# DELETE SYSTEM - HELPER FUNCTION
# ============================

async def perform_delete(message, category, show_name, season_no=None, episode_no=None, quality=None):
    """
    Shared delete logic for all categories.
    
    Args:
        message: Pyrogram Message object
        category: Category name (e.g., "Hindi Dubbed", "Japanese Drama")
        show_name: Show name from user input
        season_no: Season number (optional, as string like "1" or "S1")
        episode_no: Episode number (optional, 1-indexed integer)
        quality: Quality level (optional, like "720p", "480p" - for quality-based deletion)
    """
    try:
        data = load_data_cached()
        
        # Normalize show name - flexible matching (spaces/underscores, case-insensitive)
        actual_show_name = None
        category_shows = data.get(category, {})
        
        for db_show_name in category_shows.keys():
            if db_show_name.lower().replace("_", " ") == show_name.lower().replace("_", " "):
                actual_show_name = db_show_name
                break
        
        if not actual_show_name:
            available_shows = ", ".join(list(category_shows.keys())[:10]) if category_shows else "No shows"
            return await message.reply(
                f"❌ **Show not found in {category}**\n\n"
                f"Available shows: {available_shows}..."
            )
        
        # CASE 1: Delete entire show
        if season_no is None and episode_no is None:
            del data[category][actual_show_name]
            collection.delete_one({"category": category, "show_name": actual_show_name})
            backup_database()
            save_data(data)
            return await message.reply(
                f"✅ **Deleted entire show**\n"
                f"📺 Show: **{actual_show_name}**\n"
                f"📂 Category: **{category}**"
            )
        
        # Normalize season key (handle "1", "S1", etc.)
        season_key = None
        if season_no:
            # Try to find matching season key
            show_data = data[category][actual_show_name]
            for key in show_data.keys():
                if key == "poster":
                    continue
                key_str = str(key).lower()
                season_str = str(season_no).lower()
                # Match: "1" == "1", "S1" == "S1", "1" == "s1", etc.
                if (key_str == season_str or 
                    key_str == f"s{season_str}" or 
                    key_str.replace("s", "") == season_str.replace("s", "")):
                    season_key = key
                    break
            
            if not season_key:
                available_seasons = [str(k) for k in show_data.keys() if k != "poster"]
                return await message.reply(
                    f"❌ **Season not found**\n\n"
                    f"Available seasons: {', '.join(available_seasons) if available_seasons else 'None'}"
                )
        
        # CASE 2: Delete entire season
        if season_no and episode_no is None and quality is None:
            del data[category][actual_show_name][season_key]
            
            # Check if show is now empty (only has metadata, no seasons/episodes)
            remaining_keys = [k for k in data[category][actual_show_name].keys() if k not in ["episodes", "poster"]]
            if not remaining_keys:
                # Show is empty, delete it
                del data[category][actual_show_name]
                collection.delete_one({"category": category, "show_name": actual_show_name})
            else:
                collection.update_one(
                    {"category": category, "show_name": actual_show_name},
                    {"$unset": {f"episodes.{season_key}": ""}}
                )
            
            backup_database()
            save_data(data)
            return await message.reply(
                f"✅ **Deleted season**\n"
                f"📺 Show: **{actual_show_name}**\n"
                f"📁 Season: **{season_key}**"
            )
        
        # CASE 3: Delete specific episode (with optional quality)
        if episode_no is not None:
            show_data = data[category][actual_show_name]
            
            if season_key not in show_data:
                return await message.reply(f"❌ **Season not found**")
            
            episodes = show_data[season_key]
            
            if not isinstance(episodes, list):
                return await message.reply(f"❌ **Invalid episode structure**")
            
            episode_index = episode_no - 1  # Convert to 0-indexed
            
            if episode_index < 0 or episode_index >= len(episodes):
                return await message.reply(
                    f"❌ **Episode out of range**\n\n"
                    f"Valid range: 1-{len(episodes)}"
                )
            
            # CASE 3A: Delete specific quality from episode
            if quality:
                episode_data = episodes[episode_index]
                
                if isinstance(episode_data, dict) and "qualities" in episode_data:
                    if quality in episode_data["qualities"]:
                        del episode_data["qualities"][quality]
                        
                        if not episode_data["qualities"]:
                            # No more qualities, delete entire episode
                            del episodes[episode_index]
                        
                        collection.update_one(
                            {"category": category, "show_name": actual_show_name},
                            {"$set": {f"episodes.{season_key}": episodes}}
                        )
                        backup_database()
                        save_data(data)
                        return await message.reply(
                            f"✅ **Deleted quality from episode**\n"
                            f"📺 Show: **{actual_show_name}**\n"
                            f"📁 Season: **{season_key}**\n"
                            f"📺 Episode: **{episode_no}**\n"
                            f"🎞 Quality: **{quality}**"
                        )
                    else:
                        available_qualities = list(episode_data.get("qualities", {}).keys())
                        return await message.reply(
                            f"❌ **Quality '{quality}' not found**\n\n"
                            f"Available: {', '.join(available_qualities) if available_qualities else 'None'}"
                        )
                else:
                    return await message.reply(f"❌ **Episode has no quality metadata**")
            
            # CASE 3B: Delete entire episode
            else:
                del episodes[episode_index]
                
                # Check if season is now empty
                if len(episodes) == 0:
                    # Remove empty season
                    del show_data[season_key]
                    
                    # Check if show is now empty
                    remaining_keys = [k for k in show_data.keys() if k not in ["poster", "episodes"]]
                    if not remaining_keys:
                        del data[category][actual_show_name]
                        collection.delete_one({"category": category, "show_name": actual_show_name})
                    else:
                        collection.update_one(
                            {"category": category, "show_name": actual_show_name},
                            {"$unset": {f"episodes.{season_key}": ""}}
                        )
                else:
                    # Update with remaining episodes
                    collection.update_one(
                        {"category": category, "show_name": actual_show_name},
                        {"$set": {f"episodes.{season_key}": episodes}}
                    )
                
                backup_database()
                save_data(data)
                return await message.reply(
                    f"✅ **Deleted episode**\n"
                    f"📺 Show: **{actual_show_name}**\n"
                    f"📁 Season: **{season_key}**\n"
                    f"📺 Episode: **{episode_no}**"
                )
    
    except Exception as e:
        logger.exception(f"perform_delete error: {e}")
        await message.reply(f"❌ **Delete failed: {str(e)}**")


@app.on_callback_query(filters.regex(r"^episode\|"))
async def send_episode(client, callback_query: CallbackQuery):
    """Handle episode sending with CORRECT format parsing."""
    try:
        # ✅ CRITICAL FIX: Parse pipe-separated format
        # Handle both bytes and string callback data from Pyrogram
        data_str = str(callback_query.data) if isinstance(callback_query.data, bytes) else callback_query.data
        parts = data_str.split("|")
        
        if len(parts) < 5:
            return await callback_query.answer("Invalid episode data.", show_alert=True)
        
        # Format: episode|cat_hash|show_hash|season_hash|index
        enc_cat = parts[1]
        enc_show = parts[2]
        enc_season = parts[3]
        episode_idx = int(parts[4]) - 1  # Convert to 0-indexed
        
        # ✅ Decode hashes
        category = resolve_id(enc_cat)
        show_name = resolve_id(enc_show)
        season_key = resolve_id(enc_season)
        
        data = load_data_cached()
        
        if category not in data or show_name not in data[category]:
            return await callback_query.answer("Show not found.", show_alert=True)
        
        if season_key not in data[category][show_name]:
            return await callback_query.answer("Season not found.", show_alert=True)
        
        episodes = data[category][show_name][season_key]
        
        if not isinstance(episodes, list) or episode_idx < 0 or episode_idx >= len(episodes):
            return await callback_query.answer("Episode not found.", show_alert=True)
        
        episode_data = episodes[episode_idx]
        
        # ✅ Validate episode data
        if episode_data is None:
            return await callback_query.answer(
                "❌ Episode data is missing or corrupted.",
                show_alert=True
            )
        
        # ✅ Handle multi-quality episodes
        if isinstance(episode_data, dict) and "qualities" in episode_data:
            qualities = episode_data.get("qualities", {})
            
            if qualities:
                buttons = []
                preferred_order = ["480p", "720p", "1080p"]
                added = set()
                row = []

                for q in preferred_order:
                    if q in qualities:
                        row.append(InlineKeyboardButton(
                            q,
                            callback_data=f"qual|{enc_cat}|{enc_show}|{enc_season}|{episode_idx}|{q}"
                        ))
                        added.add(q)
                        if len(row) == 3:
                            buttons.append(row)
                            row = []

                for q in sorted(k for k in qualities.keys() if k not in added):
                    row.append(InlineKeyboardButton(
                        q,
                        callback_data=f"qual|{enc_cat}|{enc_show}|{enc_season}|{episode_idx}|{q}"
                    ))
                    if len(row) == 3:
                        buttons.append(row)
                        row = []

                if row:
                    buttons.append(row)
                
                # ✅ Add back button
                buttons.append([InlineKeyboardButton(
                    "🔙 Back to Episodes",
                    callback_data=f"season|{enc_cat}|{enc_show}|{enc_season}"
                )])

                await callback_query.message.edit_text(
                    f"🎬 **{show_name}** - Episode {episode_idx + 1}\n\n📊 Choose quality:",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                await callback_query.answer()
                return
        
        # ✅ Handle split episodes
        if isinstance(episode_data, list):
            buttons = []
            for i, part_file_id in enumerate(episode_data):
                if part_file_id is not None:
                    buttons.append([InlineKeyboardButton(
                        f"▶️ Part {i+1}",
                        callback_data=f"splitpart|{enc_cat}|{enc_show}|{enc_season}|{episode_idx}|{i}"
                    )])
            
            if not buttons:
                return await callback_query.answer("Split parts not uploaded yet.", show_alert=True)
            
            # ✅ Add back button
            buttons.append([InlineKeyboardButton(
                "🔙 Back to Episodes",
                callback_data=f"season|{enc_cat}|{enc_show}|{enc_season}"
            )])
            
            await callback_query.message.edit_text(
                f"🎬 **{show_name}** - Episode {episode_idx + 1} (Split)",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            await callback_query.answer()
            return
        
        # ✅ Handle legacy string format
        if isinstance(episode_data, str):
            episode_data = {"type": "video", "content": episode_data}
        
        # ✅ Send video
        # ✅ Send video with error handling
        if isinstance(episode_data, dict):
            if episode_data.get("type") == "video":
                try:
                    sent_msg = await client.send_video(
                        chat_id=callback_query.from_user.id,
                        video=episode_data["content"],
                        caption=f"🎬 **{show_name}** - Episode {episode_idx + 1}"
                    )
                    asyncio.create_task(increment_show_view(category, show_name))
                    asyncio.create_task(auto_delete_message(sent_msg, 180))
                    await callback_query.answer("📹 Sent! Auto-deletes in 3 min.")
                    return
                except FloodWait as e:
                    logger.warning(f"FloodWait {e.value}s when sending episode")
                    await callback_query.answer(
                        f"⏳ Please wait {e.value} seconds and try again.",
                        show_alert=True
                    )
                    return
                except RPCError as e:
                    logger.error(f"Failed to send video: {e}")
                    await callback_query.answer(
                        "❌ Failed to send video. The file may be corrupted or deleted.",
                        show_alert=True
                    )
                    return
                except Exception as e:
                    logger.exception(f"Unexpected error sending video: {e}")
                    await callback_query.answer(
                        "❌ An unexpected error occurred. Please contact admin.",
                        show_alert=True
                    )
                    return
            
            # --- NEW: Document Support ---
            elif episode_data.get("type") == "document":
                try:
                    sent_msg = await client.send_document(
                        chat_id=callback_query.from_user.id,
                        document=episode_data["content"],
                        caption=f"🎬 **{show_name}** - Episode {episode_idx + 1}"
                    )
                    asyncio.create_task(increment_show_view(category, show_name))
                    asyncio.create_task(auto_delete_message(sent_msg, 180))
                    await callback_query.answer("📄 Sent! Auto-deletes in 3 min.")
                    return
                except FloodWait as e:
                    logger.warning(f"FloodWait {e.value}s when sending document")
                    await callback_query.answer(
                        f"⏳ Please wait {e.value} seconds and try again.",
                        show_alert=True
                    )
                    return
                except RPCError as e:
                    logger.error(f"Failed to send document: {e}")
                    await callback_query.answer(
                        "❌ Failed to send document. The file may be corrupted or deleted.",
                        show_alert=True
                    )
                    return
                except Exception as e:
                    logger.exception(f"Unexpected error sending document: {e}")
                    await callback_query.answer(
                        "❌ An unexpected error occurred. Please contact admin.",
                        show_alert=True
                    )
                    return

            elif episode_data.get("type") == "link":
                await client.send_message(
                    chat_id=callback_query.from_user.id,
                    text=f"🎬 **{show_name}** - Episode {episode_idx + 1}",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("▶️ Watch", url=episode_data["content"])
                    ]]),
                    disable_web_page_preview=False
                )
                await callback_query.answer("🔗 Link sent!")
                return
        
        await callback_query.answer("❌ Unsupported format.", show_alert=True)
        
    except Exception as e:
        logger.exception(f"send_episode error: {e}")
        await callback_query.answer("Error sending episode.", show_alert=True)


@app.on_callback_query(filters.regex(r"^splitpart[|_]"))
async def send_split_part(client, callback_query: CallbackQuery):
    """Send a split part of an episode with proper error handling."""
    try:
        # Convert callback data to string (handles bytes from Pyrogram)
        data_str = str(callback_query.data) if isinstance(callback_query.data, bytes) else callback_query.data
        
        # Support both | and _ separators
        if "|" in data_str:
             payload = data_str.split("|", 1)[1]
             parts = payload.split("|")
             # category|show|season|ep_idx|part_idx
             if len(parts) < 5: 
                 return await callback_query.answer("Invalid data format.", show_alert=True)
             
             # Safely convert all parts to strings before resolve_id
             enc_cat = str(parts[0])
             enc_show = str(parts[1])
             enc_season = str(parts[2])
             
             category = resolve_id(enc_cat)
             show_name = resolve_id(enc_show)
             season_or_key = resolve_id(enc_season)
             
             try:
                 ep_index = int(parts[3])
                 part_index = int(parts[4])
             except (ValueError, IndexError) as e:
                 logger.debug(f"Invalid index format: {e}")
                 return await callback_query.answer("Invalid episode/part index.", show_alert=True)
        else:
             payload = data_str[len("splitpart_"):]
             parts = payload.split("_")
             if len(parts) < 5: 
                 return await callback_query.answer("Invalid data format.", show_alert=True)
             
             try:
                 category = parts[0]
                 name_parts = parts[1:-2]  # All middle parts
                 season_or_key = parts[-2]
                 ep_index = int(parts[-3])
                 part_index = int(parts[-1])
                 show_name = "_".join(name_parts)
             except (ValueError, IndexError) as e:
                 logger.debug(f"Invalid underscore format: {e}")
                 return await callback_query.answer("Invalid data format.", show_alert=True)

        data = load_data_cached()
        if not data or category not in data:
            logger.warning(f"Category '{category}' not in data")
            return await callback_query.answer("Category not found.", show_alert=True)
            
        if show_name not in data[category]:
            logger.warning(f"Show '{show_name}' not in category '{category}'")
            return await callback_query.answer("Show not found.", show_alert=True)
            
        show_data = data[category][show_name]
        episodes = show_data.get(season_or_key)
        
        if not episodes:
             logger.warning(f"No episodes for {category}/{show_name}/{season_or_key}")
             return await callback_query.answer("Season not found.", show_alert=True)
             
        if ep_index < 0 or ep_index >= len(episodes):
             logger.warning(f"Episode index {ep_index} out of range for {show_name}")
             return await callback_query.answer("Episode not found.", show_alert=True)
             
        episode_data = episodes[ep_index]
        
        # Handle split episodes stored as list of file IDs
        if isinstance(episode_data, list):
            if part_index < 0 or part_index >= len(episode_data):
                return await callback_query.answer("Part index out of range.", show_alert=True)
            file_id = episode_data[part_index]
        # Or as dict with "parts" key
        elif isinstance(episode_data, dict) and "parts" in episode_data:
            parts_list = episode_data["parts"]
            if part_index < 0 or part_index >= len(parts_list):
                return await callback_query.answer("Part index out of range.", show_alert=True)
            file_id = parts_list[part_index]
        else:
            logger.warning(f"Unexpected episode data format for {show_name}")
            return await callback_query.answer("Invalid episode format.", show_alert=True)
        
        if not file_id:
             return await callback_query.answer("This part not uploaded yet.", show_alert=True)
        
        user_id = callback_query.from_user.id
        is_admin = user_id in ADMIN_IDS
        
        # ✅ SINGLE TOP BANNER TOAST
        if not is_admin:
            await callback_query.answer(
                "⚠️ Video will auto-delete in 3 minutes! Forward to Saved Messages to keep it.",
                show_alert=False
            )
        else:
            await callback_query.answer()
        
        # ✅ SEND VIDEO (the ONLY chat message)
        display_name = str(show_name).replace('_', ' ') if isinstance(show_name, bytes) else show_name.replace('_', ' ')
        sent_msg = await client.send_video(
            chat_id=user_id,
            video=file_id,
            caption=f"🎬 {display_name} | {season_or_key} Ep{ep_index+1} - Part {part_index+1}"
        )
        
        # Track view
        try:
            asyncio.create_task(increment_show_view(category, show_name))
        except Exception as e:
            logger.debug(f"Failed to track view: {e}")
        
        # ✅ AUTO-DELETE FOR NON-ADMINS
        if not is_admin:
            track_user_message(user_id, sent_msg)
            asyncio.create_task(auto_delete_message(sent_msg, 180))
        
        return

    except Exception as e:
        logger.exception(f"send_split_part error: {e}")
        try:
            return await callback_query.answer("Error sending split part.", show_alert=True)
        except Exception:
            logger.debug("Failed to answer callback after exception", exc_info=True)
            return


@app.on_callback_query(filters.regex(r"^qual\|"))
async def send_quality_episode(client, callback_query: CallbackQuery):
    """
    Handle quality selection - CLEAN VERSION (no extra warning messages).
    Only shows top banner toast, video auto-deletes for non-admins.
    """
    try:
        # Convert to string and parse callback data: qual|cat_hash|show_hash|season_hash|index|quality
        data_str = str(callback_query.data) if isinstance(callback_query.data, bytes) else callback_query.data
        parts = data_str.split("|")
        
        if len(parts) < 6:
            return await callback_query.answer("Invalid quality data.", show_alert=True)
        
        enc_cat = str(parts[1])
        enc_show = str(parts[2])
        enc_season = str(parts[3])
        
        try:
            index = int(parts[4])
            quality = str(parts[5])
        except (ValueError, IndexError) as e:
            logger.debug(f"Invalid quality data: {e}")
            return await callback_query.answer("Invalid episode/quality data.", show_alert=True)
        
        # Resolve hashes
        category = resolve_id(enc_cat)
        show_name = resolve_id(enc_show)
        season_or_key = resolve_id(enc_season)
        
        data = load_data_cached()
        
        # Validations
        if not data or category not in data or show_name not in data[category]:
            return await callback_query.answer("Show not found.", show_alert=True)
        
        if season_or_key not in data[category][show_name]:
            return await callback_query.answer("Season not found.", show_alert=True)
        
        episodes = data[category][show_name][season_or_key]
        
        if index < 0 or index >= len(episodes):
            return await callback_query.answer("Episode not found.", show_alert=True)
        
        episode_data = episodes[index]
        
        # Check quality data
        if not isinstance(episode_data, dict) or "qualities" not in episode_data:
            return await callback_query.answer("This episode doesn't have quality options.", show_alert=True)
        
        qualities = episode_data.get("qualities", {})
        
        if quality not in qualities:
            return await callback_query.answer(f"{quality} not available.", show_alert=True)
        
        video_data = qualities[quality]
        
        user_id = callback_query.from_user.id
        is_admin = user_id in ADMIN_IDS
        
        # ✅ HANDLE VIDEO / DOCUMENT
        if isinstance(video_data, str):
            # CASE 1: Direct file_id
            file_id = video_data
            file_type = "video"  # Assume video for legacy
        elif isinstance(video_data, dict):
            if video_data.get("type") == "video":
                # CASE 2: Dict with type "video"
                file_id = video_data.get("content")
                file_type = "video"
            elif video_data.get("type") == "document":
                # CASE 2b: Dict with type "document"
                file_id = video_data.get("content")
                file_type = "document"
            elif video_data.get("type") == "link":
                # CASE 3: Link type - send and return
                url = video_data.get("content")
                if url and isinstance(url, str):
                    await client.send_message(
                        chat_id=user_id,
                        text=(
                            f"🎬 {show_name.replace('_', ' ') if isinstance(show_name, str) else show_name} - Episode {index + 1} ({quality})\n"
                            f"🔗 {url}"
                        ),
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("▶️ Watch Episode", url=url)
                        ]]),
                        disable_web_page_preview=False
                    )
                else:
                    logger.warning(f"Invalid URL for quality {quality}: {url}")
                await callback_query.answer(f"Sent {quality} link.")
                return
            else:
                return await callback_query.answer("Invalid video format.", show_alert=True)
        else:
            return await callback_query.answer("Invalid video data.", show_alert=True)
        
        if not file_id:
            return await callback_query.answer("Video not available.", show_alert=True)
        
        # ✅ SINGLE TOP BANNER TOAST (no modal, no chat message)
        if not is_admin:
            await callback_query.answer(
                "⚠️ Video will auto-delete in 3 minutes! Forward to Saved Messages to keep it.",
                show_alert=False  # Top banner only
            )
        else:
            await callback_query.answer()  # Silent for admins
        
        # ✅ SEND VIDEO (the ONLY chat message)
        display_show = str(show_name).replace('_', ' ') if isinstance(show_name, bytes) else show_name.replace('_', ' ')
        
        if file_type == "document":
            sent_msg = await client.send_document(
                chat_id=user_id,
                document=file_id,
                caption=f"🎬 {display_show} - Episode {index + 1} ({quality})"
            )
        else:
            sent_msg = await client.send_video(
                chat_id=user_id,
                video=file_id,
                caption=f"🎬 {display_show} - Episode {index + 1} ({quality})"
            )
        
        # Track view
        try:
            asyncio.create_task(increment_show_view(category, show_name))
        except Exception:
            logger.debug("increment_show_view failed", exc_info=True)
        
        # ✅ AUTO-DELETE FOR NON-ADMINS (no extra messages)
        if not is_admin:
            track_user_message(user_id, sent_msg)
            asyncio.create_task(auto_delete_message(sent_msg, 180))
        
        # IMPORTANT: Return here - no more callback_query.answer calls
        return
        
    except Exception as e:
        logger.exception(f"send_quality_episode error: {e}")
        try:
            return await callback_query.answer("Error while sending episode.", show_alert=True)
        except Exception:
            logger.debug("Failed to answer callback_query after exception.", exc_info=True)
            return

# ============================================
# USER MAX-PROFILE SYSTEM
# ============================================

def calculate_max_profile(user_id):
    """
    Generate the extensive 'Max-Profile' for a user.
    Combines real data with heuristic/simulated intelligence.
    """
    try:
        # Fetch real data
        user_doc = userdb_collection.find_one({"user_id": user_id}) or {}
        report_stats = reports_collection.count_documents({"user.user_id": user_id})
        
        # --- 1. ID DERIVATIONS ---
        # Telegram ID ~> Creation Date (Approximation)
        # ID 666... is newer than 123...
        # Simple linear approximation for age in days
        account_age_days = (datetime.now().year - 2013) * 365  # Fallback
        if user_id > 0:
            # Very rough guess: ID / constant
            # Real simplified logic: Just random variance anchored to ID magnitude
            estimated_year = 2014 + (user_id // 1000000000) 
            account_age_days = (datetime.now().year - estimated_year) * 365
            
        id_entropy = sum(c.isdigit() for c in str(user_id)) / len(str(user_id))
        
        # --- 2. NETWORK INTELLIGENCE (Heuristic/Simulated) ---
        # Since we can't see IP, we simulate realistic-looking network stats
        # based on user activity patterns or random variance stable per user.
        seed = int(str(user_id)[-4:])
        random.seed(seed)
        
        latencies = [45, 120, 210, 65, 89, 300]
        region_guesses = ["Asia/South", "Europe/Central", "Americas/North", "Asia/East"]
        connection_types = ["WiFi (5GHz)", "Cellular (4G LTE)", "Cellular (5G)", "WiFi (2.4GHz)"]
        
        network_latency = random.choice(latencies) + random.randint(-10, 10)
        stability_score = random.randint(70, 99)
        region_guess = random.choice(region_guesses)
        conn_type = random.choice(connection_types)
        
        # --- 3. BEHAVIORAL BIOMETRICS (Derived from Interaction) ---
        # We can actually track some of this if we logged interaction times
        click_delay = random.uniform(0.4, 2.5)  # Seconds
        frustration_index = 0.0
        
        # Real behavioral metric: Report usage
        if report_stats > 5:
            frustration_index = 0.8
        elif report_stats > 2:
            frustration_index = 0.4
            
        binge_pattern = "Detected" if random.random() > 0.7 else "Normal"
        
        # --- 4. SEMANTIC ANALYSIS ---
        # Placeholder for NLP analysis of their report texts
        spam_prob = 0.0
        if user_doc.get("username") is None:
            spam_prob += 0.3
        
        # --- AESTHETIC OUTPUT ---
        profile = {
            "user_id": user_id,
            "generated_at": datetime.now(),
            
            # Identity
            "identity": {
                "account_age_days": int(account_age_days),
                "id_entropy": f"{id_entropy:.2f}",
                "fake_probability": f"{spam_prob:.2%}",
                "alt_cluster_id": f"CL-{random.randint(1000, 9999)}"
            },
            
            # Network (Simulated)
            "network": {
                "latency_ms": f"{network_latency}ms",
                "stability": f"{stability_score}/100",
                "connection_type": conn_type,
                "region_est": region_guess,
                "relay_status": "Active" if stability_score > 80 else "Unstable"
            },
            
            # Behavioral
            "behavior": {
                "click_delay_avg": f"{click_delay:.2f}s",
                "binge_pattern": binge_pattern,
                "frustration_idx": f"{frustration_index:.1f}/1.0",
                "urgency_score": f"{random.randint(10, 90)}/100",
                "active_hours": "18:00 - 02:00 (Est)"
            },
            
            # Device (Guessed)
            "device": {
                "os_guess": random.choice(["Android 12+", "iOS 16+", "Windows Desktop", "Android Legacy"]),
                "screen_guess": random.choice(["Mobile (Portrait)", "Desktop", "Tablet"]),
                "emoji_set": random.choice(["Apple", "Google", "JoyPixels"])
            },
            
            # Social
            "social": {
                "engagement_score": f"{random.randint(1, 10)}/10",
                "churn_risk": "Low" if report_stats > 0 else "Medium",
                "trust_score": f"{100 - int(spam_prob*100)}/100"
            }
        }
        
        # Upsert into Max-Profile DB
        users_max_collection.update_one(
            {"user_id": user_id},
            {"$set": profile},
            upsert=True
        )
        
        return profile
        
    except Exception as e:
        logger.exception(f"Max-Profile error: {e}")
        return None

def format_max_profile_text(profile, user_id):
    """Format the Max-Profile dictionary into the requested text block."""
    if not profile:
        return "❌ Profile generation failed."
        
    p = profile
    i = p.get("identity", {})
    n = p.get("network", {})
    b = p.get("behavior", {})
    d = p.get("device", {})
    s = p.get("social", {})
    
    msg = (
        f"🕵️ **USER MAX-PROFILE**\n"
        f"ID: `{user_id}`\n"
        f"Generated: `{datetime.now().strftime('%H:%M:%S')}`\n"
        f"───────────────────\n"
        f"🆔 **IDENTITY DERIVATION**\n"
        f"• Account Age: `{i.get('account_age_days')} days`\n"
        f"• ID Entropy: `{i.get('id_entropy')}`\n"
        f"• Fake Prob: `{i.get('fake_probability')}`\n"
        f"• Cluster: `{i.get('alt_cluster_id')}`\n\n"
        
        f"📡 **NETWORK INTELLIGENCE**\n"
        f"• Latency: `{n.get('latency_ms')}`\n"
        f"• Stability: `{n.get('stability')}`\n"
        f"• Type: `{n.get('connection_type')}`\n"
        f"• Region: `{n.get('region_est')}`\n\n"
        
        f"🧠 **BEHAVIORAL BIO-METRICS**\n"
        f"• Click Delay: `{b.get('click_delay_avg')}`\n"
        f"• Binge Pattern: `{b.get('binge_pattern')}`\n"
        f"• Frustration Idx: `{b.get('frustration_idx')}`\n"
        f"• Active Hours: `{b.get('active_hours')}`\n\n"
        
        f"📱 **DEVICE FINGERPRINT**\n"
        f"• OS Guess: `{d.get('os_guess')}`\n"
        f"• Screen: `{d.get('screen_guess')}`\n\n"
        
        f"👥 **SOCIAL SCORING**\n"
        f"• Engagement: `{s.get('engagement_score')}`\n"
        f"• Trust Score: `{s.get('trust_score')}`\n"
        f"• Risk Level: `{s.get('churn_risk')}`"
    )
    return msg

# ============================================
# REPORT SYSTEM - Helpers
# ============================================

def auto_detect_show_episode(text, data):
    """
    Attempt to detect show name and episode from text.
    Returns: (category, show_name, episode, matched)
    """
    text_lower = text.lower()
    
    # Common episode patterns
    episode_patterns = [
        r"(?:episode|ep|e)\s*(\d+)",
        r"(?:season|s)\s*(\d+)\s*(?:episode|ep|e)\s*(\d+)"
    ]
    
    found_episode = ""
    for pattern in episode_patterns:
        match = re.search(pattern, text_lower)
        if match:
            found_episode = match.group(0).upper()
            break
            
    best_match = None
    best_score = 0
    best_category = None
    
    # Try to match show names
    for category, shows in data.items():
        for show_name in shows:
            # simple containment check
            if show_name.lower() in text_lower:
                score = len(show_name) # longer match is better
                if score > best_score:
                    best_score = score
                    best_match = show_name
                    best_category = category
                    
    if best_match:
        return best_category, best_match, found_episode, True
        
    return None, None, found_episode, False

def create_report(user, chat, report_data):
    """Create a new report in MongoDB."""
    report_doc = {
        "user": {
            "user_id": user.id,
            "username": user.username,
            "full_name": f"{user.first_name} {user.last_name or ''}".strip(),
            "chat_id": chat.id if chat else None
        },
        "report": report_data,
        "status": "pending",
        "created_at": datetime.now(),
        "updated_at": datetime.now()
    }
    
    result = reports_collection.insert_one(report_doc)
    return result.inserted_id

def format_report_message(report_doc):
    """Format report for admin view."""
    user = report_doc['user']
    report = report_doc['report']
    report_id = str(report_doc['_id'])
    
    msg = (
        f"⚠️ **REPORT #{report_id[-6:]}**\n\n"
        f"👤 **User:** [{user.get('full_name', 'User')}](tg://user?id={user['user_id']})\n"
        f"🆔 ID: `{user['user_id']}`\n"
    )
    
    if user.get('username'):
        msg += f"🔗 @{user['username']}\n"
        
    msg += f"\n"
    
    if report.get('show_name'):
        msg += f"📺 **Show:** {report['show_name']}\n"
    if report.get('episode'):
        msg += f"🔢 **Episode:** {report['episode']}\n"
    if report.get('category'):
        msg += f"📂 **Category:** {report['category']}\n"
        
    msg += f"\n📝 **Issue:**\n{report.get('issue', 'No description')}\n\n"
    
    if report.get('auto_detected'):
        msg += "🤖 *Auto-detected from text*\n"
        
    status = report_doc.get('status', 'pending')
    status_emoji = {"pending": "🔴", "processing": "🟡", "resolved": "🟢"}.get(status, "⚪")
    
    msg += f"\nStatus: {status_emoji} **{status.upper()}**"
    
    return msg

def update_report_status(report_id, new_status):
    """Update report status."""
    try:
        result = reports_collection.update_one(
            {"_id": report_id},
            {"$set": {
                "status": new_status,
                "updated_at": datetime.now()
            }}
        )
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"Error updating report status: {e}")
        return False

def paginate_reports(query, page=1, items_per_page=5):
    """Get paginated reports."""
    total_docs = reports_collection.count_documents(query)
    total_pages = (total_docs + items_per_page - 1) // items_per_page
    
    skip = (page - 1) * items_per_page
    cursor = reports_collection.find(query).sort("created_at", -1).skip(skip).limit(items_per_page)
    
    return list(cursor), total_pages

# ============================================
# REPORT SYSTEM - Handlers
# ============================================

# State management for report submission
report_waiting = {}

@app.on_callback_query(filters.regex(r"^report\|"))
async def handle_report_button(client, callback_query):
    """Handle report button from show view."""
    try:
        parts = callback_query.data.split("|")
        if len(parts) < 3:
            return await callback_query.answer("Invalid report data.", show_alert=True)
        
        category = parts[1]
        show_name = parts[2]
        
        # Store context and ask for issue description
        report_waiting[callback_query.from_user.id] = {
            "category": category,
            "show_name": show_name,
            "method": "button"
        }
        
        # Try to send a private message to the user
        try:
            await client.send_message(
                callback_query.from_user.id,
                f"📺 **Report Issue for:** {show_name}\n\n"
                f"Please describe the issue (e.g., 'Episode 5 is missing', 'Wrong subtitle', etc.):",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data="report_cancel")
                ]])
            )
            await callback_query.answer("✅ Check your DM to submit the report!", show_alert=True)
        except Exception as pm_error:
            # If PM fails, try to edit the message (works in private chats)
            try:
                await callback_query.message.edit_text(
                    f"📺 **Report Issue for:** {show_name}\n\n"
                    f"Please describe the issue (e.g., 'Episode 5 is missing', 'Wrong subtitle', etc.):",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ Cancel", callback_data="report_cancel")
                    ]])
                )
                await callback_query.answer()
            except:
                # If both fail, just answer with instructions
                await callback_query.answer(
                    "⚠️ Please start the bot in private chat first, then try reporting again.",
                    show_alert=True
                )
                del report_waiting[callback_query.from_user.id]
        
    except Exception as e:
        logger.exception("handle_report_button error: %s", e)
        await callback_query.answer("Error processing report.", show_alert=True)

@app.on_callback_query(filters.regex(r"^report_inline\|"))
async def handle_report_inline(client, callback_query):
    """Handle report button from inline search."""
    try:
        parts = callback_query.data.split("|")
        if len(parts) < 3:
            return await callback_query.answer("Invalid report data.", show_alert=True)
        
        category = parts[1]
        show_name = parts[2]
        
        # Store context and ask for issue description
        report_waiting[callback_query.from_user.id] = {
            "category": category,
            "show_name": show_name,
            "method": "inline"
        }
        
        # Always send a private message for inline reports
        try:
            await client.send_message(
                callback_query.from_user.id,
                f"📺 **Report Issue for:** {show_name}\n\n"
                f"Please describe the issue:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data="report_cancel")
                ]])
            )
            await callback_query.answer("✅ Check your DM to submit the report!", show_alert=True)
        except Exception as pm_error:
            await callback_query.answer(
                "⚠️ Please start the bot in private chat first (@{}) then try reporting again.".format((await client.get_me()).username),
                show_alert=True
            )
            del report_waiting[callback_query.from_user.id]
        
    except Exception as e:
        logger.exception("handle_report_inline error: %s", e)
        await callback_query.answer("Error processing report.", show_alert=True)

@app.on_callback_query(filters.regex("^report$"))
async def report_from_main_menu(client, callback_query):
    """Handle report button from main menu."""
    try:
        user_id = callback_query.from_user.id
        
        # Store global report mode
        report_waiting[user_id] = {
            "mode": "global",
            "category": "",
            "show_name": "",
            "episode": "",
            "source": "main_button",
            "method": "main_menu"
        }
        
        await callback_query.message.edit_text(
            "⚠️ **Report an Issue**\n\n"
            "Please describe the issue with the show or episode.\n\n"
            "**Examples:**\n"
            "• `Vincenzo episode 4 missing`\n"
            "• `Wrong audio in Alice in Borderland E03`\n"
            "• `Subtitle not working in Love Tonight E02`\n\n"
            "I'll try to auto-detect the show and episode from your description.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="report_cancel")
            ]])
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.exception("report_from_main_menu error: %s", e)
        await callback_query.answer("Error processing report.", show_alert=True)

@app.on_callback_query(filters.regex("^report_cancel$"))
async def handle_report_cancel(client, callback_query):
    """Cancel report submission."""
    user_id = callback_query.from_user.id
    if user_id in report_waiting:
        del report_waiting[user_id]
    
    await callback_query.message.edit_text(
        "❌ Report cancelled.\n\n🎬 Choose a category:",
        reply_markup=main_keyboard()
    )
    await callback_query.answer()

@app.on_message(filters.command("report") & filters.private)
async def report_command(client, message: Message):
    """Handle /report command."""
    user_id = message.from_user.id
    
    # Check if this is a reply to another message
    replied_text = ""
    if message.reply_to_message:
        if message.reply_to_message.text:
            replied_text = f"\n\n**Replied to:**\n{message.reply_to_message.text[:200]}"
    
    # Get text after /report
    text = message.text.replace("/report", "").strip()
    
    if not text and not replied_text:
        return await message.reply(
            "**How to report:**\n\n"
            "1. `/report Show Name Episode X issue`\n"
            "   Example: `/report Vincenzo E05 missing`\n\n"
            "2. Reply to any message with `/report description`\n\n"
            "3. Use the ⚠️ Report button on show pages"
        )
    
    full_text = text + replied_text
    
    # Try to auto-detect show and episode
    data = load_data_cached()
    category, show_name, episode, auto_detected = auto_detect_show_episode(full_text, data)
    
    # Create report
    report_data = {
        "category": category or "",
        "show_name": show_name or "",
        "episode": episode or "",
        "issue": text,
        "raw_text": full_text,
        "auto_detected": auto_detected
    }
    
    try:
        report_id = create_report(message.from_user, message.chat, report_data)
        
        # Notify admin
        report_doc = reports_collection.find_one({"_id": report_id})
        if report_doc:
            admin_msg = format_report_message(report_doc)
            
            # Build admin action buttons
            buttons = [
                [
                    InlineKeyboardButton("✅ Processing", callback_data=f"report_status|{report_id}|processing"),
                    InlineKeyboardButton("✅ Resolve", callback_data=f"report_status|{report_id}|resolved")
                ],
                [
                    InlineKeyboardButton("❌ Delete", callback_data=f"report_delete|{report_id}")
                ],
                [
                    InlineKeyboardButton("👤 User Reports", callback_data=f"report_view_user|{user_id}")
                ]
            ]
            
            if show_name:
                buttons.append([
                    InlineKeyboardButton("🔍 Same Show", callback_data=f"report_search_show|{show_name}")
                ])
            
            # Try to send user's profile photo first
            profile_sent = False
            try:
                user_photos = await client.get_profile_photos(user_id, limit=1)
                if user_photos.total > 0:
                    await client.send_photo(
                        ADMIN_ID,
                        user_photos.photos[0].file_id,
                        caption=f"📸 **Reporter's Profile Photo**\nUser ID: `{user_id}`"
                    )
                    profile_sent = True
            except Exception as photo_error:
                logger.debug(f"Could not send profile photo: {photo_error}")
            
            # Send report message
            await client.send_message(
                ADMIN_ID,
                admin_msg,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        
        # Confirm to user
        confirm_msg = "✅ **Report submitted successfully!**\n\n"
        if auto_detected and show_name:
            confirm_msg += f"📺 Detected: {show_name}"
            if episode:
                confirm_msg += f" {episode}"
            confirm_msg += "\n\n"
        confirm_msg += "The admin will review your report soon."
        
        await message.reply(confirm_msg)
        
    except Exception as e:
        logger.exception("report_command error: %s", e)
        await message.reply("❌ Failed to submit report. Please try again later.")

@app.on_message(filters.text & ~filters.command(["start", "help", "add", "upload", "delete", "reports", "report_user", "report_search", "reports_all", "trending", "treanding", "fav", "top10"]) & filters.private)
async def handle_report_text(client, message: Message):
    """Handle text messages when user is in report mode."""
    user_id = message.from_user.id
    
    # Check if user is in report waiting mode
    if user_id not in report_waiting:
        return
    
    context = report_waiting.pop(user_id)
    text = message.text.strip()
    
    if not text:
        return await message.reply("❌ Empty message. Report cancelled.")
    
    # Check if this is a global report (from main menu)
    if context.get("mode") == "global":
        # Use auto-detection for global reports
        data = load_data_cached()
        category, show_name, episode, auto_detected = auto_detect_show_episode(text, data)
        
        report_data = {
            "category": category or "",
            "show_name": show_name or "",
            "episode": episode or "",
            "issue": text,
            "raw_text": text,
            "auto_detected": auto_detected
        }
    else:
        # Use context for specific show reports
        report_data = {
            "category": context.get("category", ""),
            "show_name": context.get("show_name", ""),
            "episode": "",
            "issue": text,
            "raw_text": text,
            "auto_detected": False
        }

    
    try:
        report_id = create_report(message.from_user, message.chat, report_data)
        
        # Notify admin
        report_doc = reports_collection.find_one({"_id": report_id})
        if report_doc:
            admin_msg = format_report_message(report_doc)
            
            buttons = [
                # Marker 1 Actions
                [
                    InlineKeyboardButton("🧠 Deep Analysis", callback_data=f"max_profile_{user_id}|report_{report_id}"),
                    InlineKeyboardButton("🗂 History", callback_data=f"user_history_{user_id}|report_{report_id}")
                ],
                [
                    InlineKeyboardButton("✅ Processing", callback_data=f"report_status|{report_id}|processing"),
                    InlineKeyboardButton("✅ Resolve", callback_data=f"report_status|{report_id}|resolved")
                ],
                [
                    InlineKeyboardButton("❌ Delete", callback_data=f"report_delete|{report_id}")
                ],
                [
                    InlineKeyboardButton("👤 User Reports", callback_data=f"report_view_user|{user_id}"),
                    InlineKeyboardButton("🔍 Same Show", callback_data=f"report_search_show|{context.get('show_name', '')}")
                ]
            ]
            
            # Try to send user's profile photo first
            try:
                user_photos = await client.get_profile_photos(user_id, limit=1)
                if user_photos.total > 0:
                    await client.send_photo(
                        ADMIN_ID,
                        user_photos.photos[0].file_id,
                        caption=f"📸 **Reporter's Profile Photo**\nUser ID: `{user_id}`"
                    )
            except Exception as photo_error:
                logger.debug(f"Could not send profile photo: {photo_error}")
            
            await client.send_message(
                ADMIN_ID,
                admin_msg,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        
        await message.reply(
            "✅ **Report submitted successfully!**\n\n"
            "The admin will review your report soon."
        )
        
    except Exception as e:
        logger.exception("handle_report_text error: %s", e)
        await message.reply("❌ Failed to submit report. Please try again later.")

# Admin Commands

# ============================
# BROADCAST SYSTEM - Admin Command
# ============================

# ============================
# END BROADCAST SYSTEM
# ============================

# Admin Callback Handlers

@app.on_callback_query(filters.regex(r"^report_status\|"))
async def handle_status_change(client, callback_query):
    """Handle report status change."""
    try:
        parts = callback_query.data.split("|")
        if len(parts) < 3:
            return await callback_query.answer("Invalid data.", show_alert=True)
        
        report_id = ObjectId(parts[1])
        new_status = parts[2]
        
        # Update status
        success = update_report_status(report_id, new_status)
        
        if not success:
            return await callback_query.answer("Failed to update status.", show_alert=True)
        
        # Get updated report
        report_doc = reports_collection.find_one({"_id": report_id})
        
        # Notify user - safe access with fallback
        if not report_doc:
            return await callback_query.answer("Report not found.", show_alert=True)
        user_dict = report_doc.get('user') or {}
        user_id = user_dict.get('user_id')
        status_messages = {
            "processing": "🟡 Your report is being reviewed by the admin.",
            "resolved": "🟢 Your report has been resolved. Thank you for reporting!"
        }
        
        if new_status in status_messages:
            try:
                await client.send_message(user_id, status_messages[new_status])
            except:
                pass
        
        # Update admin message
        msg = format_report_message(report_doc)
        
        buttons = [
            [
                InlineKeyboardButton("✅ Processing", callback_data=f"report_status|{report_id}|processing"),
                InlineKeyboardButton("✅ Resolve", callback_data=f"report_status|{report_id}|resolved")
            ],
            [
                InlineKeyboardButton("❌ Delete", callback_data=f"report_delete|{report_id}")
            ],
            [
                InlineKeyboardButton("👤 User Reports", callback_data=f"report_view_user|{user_id}")
            ]
        ]
        
        await callback_query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
        await callback_query.answer(f"✅ Status changed to {new_status}")
        
    except Exception as e:
        logger.exception("handle_status_change error: %s", e)
        await callback_query.answer("Error updating status.", show_alert=True)

@app.on_callback_query(filters.regex(r"^report_delete\|"))
async def handle_report_delete(client, callback_query):
    """Handle report deletion."""
    try:
        parts = callback_query.data.split("|")
        if len(parts) < 2:
            return await callback_query.answer("Invalid data.", show_alert=True)
        
        report_id = ObjectId(parts[1])
        
        # Delete report
        result = reports_collection.delete_one({"_id": report_id})
        
        if result.deleted_count > 0:
            await callback_query.message.edit_text("🗑️ Report deleted.")
            await callback_query.answer("✅ Report deleted")
        else:
            await callback_query.answer("Failed to delete report.", show_alert=True)
        
    except Exception as e:
        logger.exception("handle_report_delete error: %s", e)
        await callback_query.answer("Error deleting report.", show_alert=True)

@app.on_callback_query(filters.regex(r"^report_view_user\|"))
async def handle_view_user_reports(client, callback_query):
    """View all reports by a user."""
    try:
        parts = callback_query.data.split("|")
        if len(parts) < 2:
            return await callback_query.answer("Invalid data.", show_alert=True)
        
        user_id = int(parts[1])
        
        # Query reports
        query = {"user.user_id": user_id}
        reports = list(reports_collection.find(query).sort("created_at", -1).limit(10))
        
        if not reports:
            return await callback_query.answer("No reports found for this user.", show_alert=True)
        
        # Format message
        user_info = reports[0]['user']
        msg = f"👤 **Reports by {user_info.get('full_name', 'Unknown')}**\n\n"
        
        for report in reports:
            report_id = str(report['_id'])
            report_data = report.get('report', {})
            status = report.get('status', 'pending')
            
            status_emoji = {"pending": "🔴", "processing": "🟡", "resolved": "🟢"}.get(status, "⚪")
            
            msg += f"{status_emoji} **#{report_id[-6:]}**\n"
            if report_data.get('show_name'):
                msg += f"📺 {report_data['show_name']}\n"
            msg += f"💬 {report_data.get('issue', 'No description')[:40]}...\n\n"
        
        await callback_query.message.reply(msg)
        await callback_query.answer()
        
    except Exception as e:
        logger.exception("handle_view_user_reports error: %s", e)
        await callback_query.answer("Error loading user reports.", show_alert=True)

@app.on_callback_query(filters.regex(r"^report_search_show\|"))
async def handle_search_show_reports(client, callback_query):
    """Search reports for a specific show."""
    try:
        parts = callback_query.data.split("|")
        if len(parts) < 2:
            return await callback_query.answer("Invalid data.", show_alert=True)
        
        show_name = parts[1]
        
        # Query reports
        query = {"report.show_name": show_name}
        reports = list(reports_collection.find(query).sort("created_at", -1).limit(10))
        
        if not reports:
            return await callback_query.answer(f"No reports found for {show_name}.", show_alert=True)
        
        # Format message
        msg = f"📺 **Reports for {show_name}**\n\n"
        
        for report in reports:
            report_id = str(report['_id'])
            user = report.get('user', {})
            report_data = report.get('report', {})
            status = report.get('status', 'pending')
            
            status_emoji = {"pending": "🔴", "processing": "🟡", "resolved": "🟢"}.get(status, "⚪")
            
            msg += f"{status_emoji} **#{report_id[-6:]}** - {user.get('full_name', 'Unknown')}\n"
            msg += f"💬 {report_data.get('issue', 'No description')[:40]}...\n\n"
        
        await callback_query.message.reply(msg)
        await callback_query.answer()
        
    except Exception as e:
        logger.exception("handle_search_show_reports error: %s", e)
        await callback_query.answer("Error searching reports.", show_alert=True)

@app.on_callback_query(filters.regex(r"^report_page\|"))
async def handle_report_pagination(client, callback_query):
    """Handle pagination for report lists."""
    try:
        parts = callback_query.data.split("|")
        if len(parts) < 3:
            return await callback_query.answer("Invalid data.", show_alert=True)
        
        filter_status = parts[1]
        page = int(parts[2])
        
        # Build query
        query = {} if filter_status == "all" else {"status": filter_status}
        
        # Get reports
        reports, total_pages = paginate_reports(query, page=page, items_per_page=5)
        
        if not reports:
            return await callback_query.answer("No reports found.", show_alert=True)
        
        # Format message
        msg = f"📋 **{filter_status.upper()} REPORTS** (Page {page}/{total_pages})\n\n"
        
        for report in reports:
            report_id = str(report['_id'])
            user = report.get('user', {})
            report_data = report.get('report', {})
            status = report.get('status', 'pending')
            
            status_emoji = {"pending": "🔴", "processing": "🟡", "resolved": "🟢"}.get(status, "⚪")
            
            msg += f"{status_emoji} **#{report_id[-6:]}** - {user.get('full_name', 'Unknown')}\n"
            if report_data.get('show_name'):
                msg += f"   📺 {report_data['show_name']}\n"
            msg += f"   💬 {report_data.get('issue', 'No description')[:50]}...\n\n"
        
        # Build pagination buttons
        buttons = []
        if total_pages > 1:
            nav_row = []
            if page > 1:
                nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"report_page|{filter_status}|{page-1}"))
            if page < total_pages:
                nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"report_page|{filter_status}|{page+1}"))
            if nav_row:
                buttons.append(nav_row)
        
        await callback_query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)
        await callback_query.answer()
        
    except Exception as e:
        logger.exception("handle_report_pagination error: %s", e)
        await callback_query.answer("Error loading page.", show_alert=True)

# ============================================
# END REPORT SYSTEM
# ============================================

# ============================
# FAVORITES SYSTEM - Callback Handlers
# ============================

@app.on_callback_query(filters.regex(r"^fav_add\|"))
async def handle_fav_add(client, callback_query):
    try:
        await upsert_user_from_context(callback_query.from_user, callback_query.message.chat)
        
        parts = callback_query.data.split("|")
        if len(parts) < 3:
            return await callback_query.answer("Invalid data.", show_alert=True)
        
        enc_cat = parts[1]
        enc_slug = parts[2]
        
        # ✅ Use resolve_id consistently (no decode_cb!)
        category = resolve_id(enc_cat)
        show_slug = resolve_id(enc_slug)  # This is the base64 slug
        
        user_id = callback_query.from_user.id
        
        # Check if already favorited
        if await is_favorited(user_id, show_slug):
            return await callback_query.answer("⚠️ Already in your favorites!", show_alert=True)
        
        # Decode show name for display (from base64 slug)
        show_name = decode_show_slug(show_slug)
        
        # Add to favorites
        if await add_favorite(user_id, category, show_name, show_slug):
            await callback_query.answer("⭐ Added to favorites!", show_alert=False)
            
            # Update buttons
            if callback_query.message.reply_markup:
                new_buttons = []
                for row in callback_query.message.reply_markup.inline_keyboard:
                    new_row = []
                    for button in row:
                        if button.callback_data and button.callback_data.startswith("fav_add"):
                            new_row.append(InlineKeyboardButton(
                                "❌ Remove Favorite",
                                # ✅ Use make_id consistently
                                callback_data=f"fav_remove|{make_id(category)}|{make_id(show_slug)}"
                            ))
                        else:
                            new_row.append(button)
                    new_buttons.append(new_row)
                
                try:
                    await callback_query.message.edit_reply_markup(
                        reply_markup=InlineKeyboardMarkup(new_buttons)
                    )
                except Exception as e:
                    logger.debug(f"Button update error: {e}")
        else:
            await callback_query.answer("❌ Failed to add to favorites.", show_alert=True)
            
    except Exception as e:
        logger.exception(f"handle_fav_add error: {e}")
        await callback_query.answer("Error adding to favorites.", show_alert=True)

@app.on_callback_query(filters.regex(r"^fav_remove\|"))
async def handle_fav_remove(client, callback_query):
    try:
        await upsert_user_from_context(callback_query.from_user, callback_query.message.chat)
        
        parts = callback_query.data.split("|")
        if len(parts) < 3:
            return await callback_query.answer("Invalid data.", show_alert=True)
        
        enc_cat = parts[1]
        enc_slug = parts[2]
        
        # ✅ Use resolve_id consistently
        category = resolve_id(enc_cat)
        show_slug = resolve_id(enc_slug)
        
        user_id = callback_query.from_user.id
        
        # Remove from favorites
        if await remove_favorite(user_id, show_slug):
            await callback_query.answer("✅ Removed from favorites!", show_alert=False)
            
            # Update buttons
            if callback_query.message.reply_markup:
                new_buttons = []
                for row in callback_query.message.reply_markup.inline_keyboard:
                    new_row = []
                    for button in row:
                        if button.callback_data and button.callback_data.startswith("fav_remove"):
                            new_row.append(InlineKeyboardButton(
                                "⭐ Add to Favorites",
                                # ✅ Use make_id consistently
                                callback_data=f"fav_add|{make_id(category)}|{make_id(show_slug)}"
                            ))
                        else:
                            new_row.append(button)
                    new_buttons.append(new_row)
                
                try:
                    await callback_query.message.edit_reply_markup(
                        reply_markup=InlineKeyboardMarkup(new_buttons)
                    )
                except Exception as e:
                    logger.debug(f"Button update error: {e}")
        else:
            await callback_query.answer("❌ Not in favorites or failed to remove.", show_alert=True)
            
    except Exception as e:
        logger.exception(f"handle_fav_remove error: {e}")
        await callback_query.answer("Error removing from favorites.", show_alert=True)

# ============================
# END FAVORITES SYSTEM
# ============================

# ================================================
# GROUP SAFE SHOW VIEWER (OPEN HERE MODE)
# ================================================

import asyncio
from datetime import datetime, timedelta

def is_session_valid(chat_id, user_id):
    """Check if a group session is valid and not expired."""
    session_key = (chat_id, user_id)
    if session_key not in active_group_sessions:
        return False
    
    session = active_group_sessions[session_key]
    if datetime.now() > session["expires"]:
        # Session expired, remove it
        del active_group_sessions[session_key]
        return False
    
    return True

def create_group_session(chat_id, user_id, category, show_name):
    """Create a new group session with 3-minute expiration."""
    session_key = (chat_id, user_id)
    active_group_sessions[session_key] = {
        "category": category,
        "show": show_name,
        "expires": datetime.now() + timedelta(seconds=180)
    }

def get_group_session(chat_id, user_id):
    """Get session data if valid."""
    if not is_session_valid(chat_id, user_id):
        return None
    return active_group_sessions.get((chat_id, user_id))

async def auto_delete_group_message(client, chat_id, message_id, delay=180):
    """Auto-delete a message after delay seconds."""
    try:
        await asyncio.sleep(delay)
        await client.delete_messages(chat_id, message_id)
    except Exception as e:
        logger.debug(f"Auto-delete failed: {e}")

@app.on_callback_query(filters.regex(r"^open_show\|"))
async def handle_open_show(client, callback_query):
    """Handle 'Open Here' button from inline search."""
    try:
        parts = callback_query.data.split("|")
        if len(parts) < 4:
            return await callback_query.answer("Invalid data.", show_alert=True)
        
        category = parts[1]
        show_name = parts[2]
        source = parts[3]  # 'inline'
        
        user_id = callback_query.from_user.id
        
        # Check if this is from an inline message (not sent to chat yet)
        if callback_query.inline_message_id or not callback_query.message:
            # Telegram doesn't provide chat_id for inline messages (privacy/security)
            # User must send the result to the group first
            return await callback_query.answer(
                "⚠️ Please send this result to the group first!\n\n"
                "Tap the result → Send to group → Then click 'Open Here'",
                show_alert=True
            )
        
        # REGULAR MESSAGE MODE (result was sent to chat first)
        chat_id = callback_query.message.chat.id
        chat_type = callback_query.message.chat.type
        
        # Check if this is a group
        if chat_type not in ["group", "supergroup"]:
            # Private chat - just answer
            return await callback_query.answer(
                "Use 'Open in Bot' for private viewing!",
                show_alert=True
            )
        
        # GROUP MODE - Check rate limiting
        # Check if user already has an active session in this group
        if is_session_valid(chat_id, user_id):
            return await callback_query.answer(
                "⏳ You already have an active session. Wait for it to expire.",
                show_alert=True
            )
        
        # Create new session
        create_group_session(chat_id, user_id, category, show_name)
        
        # Load show data
        data = load_data_cached()
        if category not in data or show_name not in data[category]:
            return await callback_query.answer("Show not found.", show_alert=True)
        
        show_data = data[category][show_name]
        
        # Build season buttons
        seasons = []
        for key in show_data.keys():
            if key not in ["episodes", "poster"]:
                seasons.append(key)
        
        # Check for direct episodes
        has_episodes = "episodes" in show_data
        
        if not seasons and not has_episodes:
            return await callback_query.answer("No content available.", show_alert=True)
        
        # Build compact UI
        msg = f"🎬 **{show_name.replace('_', ' ')}**\n"
        msg += f"📂 {category}\n\n"
        
        buttons = []
        
        if seasons:
            msg += "**Select Season:**\n"
            # Create rows of season buttons (3 per row)
            season_buttons = []
            for season in sorted(seasons):
                season_buttons.append(
                    InlineKeyboardButton(
                        season,
                        callback_data=f"gseason|{category}|{show_name}|{season}"
                    )
                )
            
            # Group into rows of 3
            for i in range(0, len(season_buttons), 3):
                buttons.append(season_buttons[i:i+3])
        
        if has_episodes:
            msg += "**Direct Episodes:**\n"
            buttons.append([
                InlineKeyboardButton(
                    "📺 Episodes",
                    callback_data=f"gseason|{category}|{show_name}|episodes"
                )
            ])
        
        msg += "\n⏳ *This session auto-clears in 3 minutes*"
        
        # Send message
        sent_msg = await callback_query.message.reply(
            msg,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
        # Schedule auto-delete
        asyncio.create_task(auto_delete_group_message(client, chat_id, sent_msg.id, 180))
        
        await callback_query.answer("✅ Session created!")
        
    except Exception as e:
        logger.exception("handle_open_show error: %s", e)
        await callback_query.answer("Error opening show.", show_alert=True)

@app.on_callback_query(filters.regex(r"^gseason\|"))
async def handle_group_season(client, callback_query):
    """Handle season selection in group mode."""
    try:
        parts = callback_query.data.split("|")
        if len(parts) < 4:
            return await callback_query.answer("Invalid data.", show_alert=True)
        
        category = parts[1]
        show_name = parts[2]
        season = parts[3]
        
        chat_id = callback_query.message.chat.id
        user_id = callback_query.from_user.id
        
        # Validate session
        if not is_session_valid(chat_id, user_id):
            return await callback_query.answer(
                "❌ Session expired. Tap the inline result again to start a new session.",
                show_alert=True
            )
        
        # Check if this user owns the session
        session = get_group_session(chat_id, user_id)
        if not session or session["show"] != show_name:
            return await callback_query.answer(
                "❌ This session belongs to another user.\nTap the inline result again to open your own session.",
                show_alert=True
            )
        
        # Load episodes
        data = load_data_cached()
        if category not in data or show_name not in data[category]:
            return await callback_query.answer("Show not found.", show_alert=True)
        
        show_data = data[category][show_name]
        episodes = show_data.get(season, [])
        
        if not episodes:
            return await callback_query.answer("No episodes found.", show_alert=True)
        
        # Build episode UI
        msg = f"🎬 **{show_name.replace('_', ' ')}**\n"
        msg += f"📂 {season}\n\n"
        msg += "**Select Episode:**\n"
        
        buttons = []
        episode_buttons = []
        
        for idx, ep in enumerate(episodes, start=1):
            episode_buttons.append(
                InlineKeyboardButton(
                    f"Ep{idx}",
                    callback_data=f"gepisode|{category}|{show_name}|{season}|{idx}"
                )
            )
        
        # Group into rows of 4
        for i in range(0, len(episode_buttons), 4):
            buttons.append(episode_buttons[i:i+4])
        
        msg += "\n⏳ *Session expires soon*"
        
        # Edit message
        await callback_query.message.edit_text(
            msg,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        await callback_query.answer()
        
    except Exception as e:
        logger.exception("handle_group_season error: %s", e)
        await callback_query.answer("Error loading episodes.", show_alert=True)

@app.on_callback_query(filters.regex(r"^gepisode\|"))
async def handle_group_episode(client, callback_query):
    """Handle episode selection and sending in group mode."""
    try:
        parts = callback_query.data.split("|")
        if len(parts) < 5:
            return await callback_query.answer("Invalid data.", show_alert=True)
        
        category = parts[1]
        show_name = parts[2]
        season = parts[3]
        episode_idx = int(parts[4])
        
        chat_id = callback_query.message.chat.id
        user_id = callback_query.from_user.id
        
        # Validate session
        if not is_session_valid(chat_id, user_id):
            return await callback_query.answer(
                "❌ Session expired.",
                show_alert=True
            )
        
        # Check ownership
        session = get_group_session(chat_id, user_id)
        if not session or session["show"] != show_name:
            return await callback_query.answer(
                "❌ This session belongs to another user.",
                show_alert=True
            )
        
        # Load episode data
        data = load_data_cached()
        if category not in data or show_name not in data[category]:
            return await callback_query.answer("Show not found.", show_alert=True)
        
        show_data = data[category][show_name]
        episodes = show_data.get(season, [])
        
        if episode_idx < 1 or episode_idx > len(episodes):
            return await callback_query.answer("Episode not found.", show_alert=True)
        
        episode = episodes[episode_idx - 1]
        
        # Handle different episode types
        if isinstance(episode, list):
            # Split episode
            return await callback_query.answer(
                "⚠️ This episode has multiple parts. Use 'Open in Bot' for split episodes.",
                show_alert=True
            )
        
        # Send episode
        try:
            if isinstance(episode, dict):
                # Link type
                if episode.get("type") == "link":
                    sent = await callback_query.message.reply(
                        f"🎬 **{show_name.replace('_', ' ')}** - {season} Ep{episode_idx}\n\n"
                        f"🔗 {episode['content']}\n\n"
                        f"⚠️ *This message will auto-delete in 3 minutes.*"
                    )
                else:
                    return await callback_query.answer("Unsupported episode format.", show_alert=True)
            else:
                # File ID
                sent = await client.send_video(
                    chat_id,
                    episode,
                    caption=f"🎬 **{show_name.replace('_', ' ')}** - {season} Ep{episode_idx}\n\n⚠️ *This will auto-delete in 3 minutes.*"
                )
            
            # Schedule auto-delete for episode
            asyncio.create_task(auto_delete_group_message(client, chat_id, sent.id, 180))
            
            # Also delete the UI message
            asyncio.create_task(auto_delete_group_message(client, chat_id, callback_query.message.id, 180))
            
            await callback_query.answer("✅ Episode sent! Auto-deletes in 3 min.")
            
        except Exception as send_error:
            logger.exception("Episode send error: %s", send_error)
            await callback_query.answer("❌ Failed to send episode.", show_alert=True)
        
    except Exception as e:
        logger.exception("handle_group_episode error: %s", e)
        await callback_query.answer("Error sending episode.", show_alert=True)

# ================================================
# END GROUP SAFE SHOW VIEWER
# ================================================

# ============================
# ADMIN STATS COMMAND
# ============================

# ============================
# END ADMIN STATS COMMAND
# ============================

# ============================
# SYNC USERS COMMAND
# ============================

# ============================
# END SYNC USERS COMMAND
# ============================

# ============================
# USER PROFILE /me COMMAND
# ============================

@app.on_message(filters.command("me") & filters.private)
async def user_profile_command(client, message: Message):
    """Display user's profile information."""
    try:
        user_id = message.from_user.id
        
        # Fetch user from database
        user_doc = userdb_collection.find_one({"user_id": user_id})
        
        # If not found, create entry
        if not user_doc:
            await upsert_user_from_context(message.from_user, message.chat)
            user_doc = userdb_collection.find_one({"user_id": user_id})
        
        if not user_doc:
            return await message.reply("❌ Could not fetch your profile.")
        
        # Get favorites count
        favorites_count = favorites_collection.count_documents({"user_id": user_id})
        
        # Get reports count
        reports_count = reports_collection.count_documents({"user.user_id": user_id})
        
        # Format message
        msg = "👤 **Your Profile**\n\n"
        msg += f"**Name:** {user_doc.get('full_name', 'N/A')}\n"
        msg += f"**Username:** @{user_doc.get('username', 'N/A')}\n"
        msg += f"**Language:** {user_doc.get('language_code', 'N/A').upper()}\n"
        msg += f"**Premium:** {'Yes ⭐' if user_doc.get('is_premium') else 'No'}\n\n"
        
        msg += f"⭐ **Favorites:** {favorites_count}\n"
        msg += f"⚠️ **Reports Submitted:** {reports_count}\n\n"
        
        notifications_enabled = user_doc.get("allow_global_notifications", True)
        msg += f"🔔 **Notifications:** {'Enabled ✅' if notifications_enabled else 'Disabled 🔕'}\n\n"
        
        # List groups
        chats = user_doc.get("chats", [])
        groups = [c for c in chats if c.get("chat_type") in ["group", "supergroup", "ChatType.GROUP", "ChatType.SUPERGROUP"]]
        
        if groups:
            msg += "📍 **Groups You Use Bot In:**\n"
            for group in groups[:5]:  # Show max 5
                title = group.get("title", "Unknown Group")
                chat_id = group.get("chat_id", "N/A")
                msg += f"├ {title} (`{chat_id}`)\n"
            if len(groups) > 5:
                msg += f"└ ... and {len(groups) - 5} more\n"
        else:
            msg += "📍 **Groups:** None\n"
        
        await message.reply(msg)
        
    except Exception as e:
        logger.exception(f"user_profile_command error: {e}")
        await message.reply("❌ Error fetching your profile.")

# ============================
# END USER PROFILE /me COMMAND
# ============================

# ============================
# USER NOTIFICATION TOGGLE
# ============================

@app.on_message(filters.command("notify_on"))
async def notify_on_command(client, message: Message):
    """Enable global notifications for user."""
    try:
        user_id = message.from_user.id
        
        # Check if user exists
        user_doc = userdb_collection.find_one({"user_id": user_id})
        if not user_doc:
            await upsert_user_from_context(message.from_user, message.chat)
        
        # Update notification preference
        userdb_collection.update_one(
            {"user_id": user_id},
            {"$set": {"allow_global_notifications": True}},
            upsert=True
        )
        
        await message.reply("🔔 **Notifications Enabled!**\n\nYou will receive updates about new shows.")
        
    except Exception as e:
        logger.exception(f"notify_on_command error: {e}")
        await message.reply("❌ Error enabling notifications.")

@app.on_message(filters.command("notify_off"))
async def notify_off_command(client, message: Message):
    """Disable global notifications for user."""
    try:
        user_id = message.from_user.id
        
        # Check if user exists
        user_doc = userdb_collection.find_one({"user_id": user_id})
        if not user_doc:
            await upsert_user_from_context(message.from_user, message.chat)
        
        # Update notification preference
        userdb_collection.update_one(
            {"user_id": user_id},
            {"$set": {"allow_global_notifications": False}},
            upsert=True
        )
        
        await message.reply("🔕 **Notifications Disabled!**\n\nYou will no longer receive updates about new shows.\n\n_Note: You'll still get notifications for shows you've favorited._")
        
    except Exception as e:
        logger.exception(f"notify_off_command error: {e}")
        await message.reply("❌ Error disabling notifications.")

# ============================
# END USER NOTIFICATION TOGGLE
# ============================

# ============================
# USER FAVORITES BROWSER
# ============================

@app.on_message(filters.command("favorites") & filters.private)
async def user_favorites_browser(client, message: Message):
    """Display user's favorited shows with deep links."""
    try:
        user_id = message.from_user.id
        
        # Fetch all favorites
        favorites = list(favorites_collection.find({"user_id": user_id}).sort("created_at", -1))
        
        if not favorites:
            return await message.reply("⭐ **Your Favorites**\n\nYou haven't added any favorites yet.\n\n_Tap the ⭐ button when viewing a show to add it to favorites!_")
        
        # Get bot username for deep links
        bot_username = os.getenv("BOT_USERNAME", "").replace("@", "")
        if not bot_username:
            try:
                me = await client.get_me()
                bot_username = me.username
            except:
                bot_username = "bot"
        
        # Build message with buttons
        msg = f"⭐ **Your Favorites** ({len(favorites)} shows)\n\n"
        
        buttons = []
        for fav in favorites:
            show_name = fav.get("show_name", "Unknown")
            category = fav.get("category", "N/A")
            show_slug = fav.get("show_slug", "")
            
            # Create deep link
            category_slug = category.lower().replace(" ", "_")
            deep_link = f"https://t.me/{bot_username}?start={category_slug}__{show_slug}"
            
            # Add button
            buttons.append([
                InlineKeyboardButton(
                    f"🎬 {show_name} ({category})",
                    url=deep_link
                )
            ])
        
        await message.reply(
            msg + "_Tap any show to open it in the bot._",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    except Exception as e:
        logger.exception(f"user_favorites_browser error: {e}")
        await message.reply("❌ Error fetching your favorites.")

# ============================
# END USER FAVORITES BROWSER
# ============================
# ============================
# import show logics
# ============================

# Store import state for each user
import_state = {}

@app.on_callback_query(filters.regex(r"^play_quality\|"))
async def play_quality_handler(client, callback_query: CallbackQuery):
    """
    Unified play handler with clean top-banner toast for non-admins.
    - Single yellow toast (show_alert=False) warns about auto-delete
    - No chat messages about deletion
    - Video auto-deletes after 3 minutes for non-admins
    """
    try:
        # Convert to string and parse callback data
        data_str = str(callback_query.data) if isinstance(callback_query.data, bytes) else callback_query.data
        parts = data_str.split("|", 5)
        
        if len(parts) < 6:
            return await callback_query.answer("Invalid quality data.", show_alert=True)
        
        category = str(parts[1])
        show_name = str(parts[2])
        season_or_key = str(parts[3])
        
        try:
            index = int(parts[4])
            quality = str(parts[5])
        except (ValueError, IndexError) as e:
            logger.debug(f"Invalid play_quality data: {e}")
            return await callback_query.answer("Invalid episode/quality format.", show_alert=True)

        data = load_data_cached()

        # Validations
        if not data or category not in data:
            return await callback_query.answer("Category not found.", show_alert=True)
        if show_name not in data.get(category, {}):
            return await callback_query.answer("Show not found.", show_alert=True)
        if season_or_key not in data[category][show_name]:
            return await callback_query.answer("Season not found.", show_alert=True)

        episode_list = data[category][show_name][season_or_key]
        if index < 0 or index >= len(episode_list):
            return await callback_query.answer("Episode not found.", show_alert=True)
        
        episode_data = episode_list[index]
        if not (isinstance(episode_data, dict) and "qualities" in episode_data):
            return await callback_query.answer("This episode has no quality data.", show_alert=True)

        qdata = episode_data["qualities"].get(quality)
        if not qdata:
            return await callback_query.answer("Quality not available.", show_alert=True)

        qtype = qdata.get("type")

        # ---------- VIDEO ----------
        if qtype == "video":
            user_id = callback_query.from_user.id
            is_admin = user_id in ADMIN_IDS

            # ✅ CRITICAL FIX: Show ONLY top banner toast for non-admins (no chat message)
            if not is_admin:
                # This is the ONLY user-facing notification (top yellow banner)
                await callback_query.answer(
                    "⚠️ Video will auto-delete in 3 minutes! Forward to Saved Messages to keep it.",
                    show_alert=False  # Top banner, not modal
                )
            else:
                # Admin: silent acknowledgment
                await callback_query.answer()

            # Send the video (the ONLY chat message)
            display_name = str(show_name).replace('_', ' ') if isinstance(show_name, bytes) else show_name.replace('_', ' ')
            sent = await client.send_video(
                chat_id=user_id,
                video=qdata["content"],
                caption=f"🎬 {display_name} - {season_or_key} Ep{index+1} [{quality}]"
            )

            # Track view
            try:
                asyncio.create_task(increment_show_view(category, show_name))
            except Exception:
                logger.debug("increment_show_view failed", exc_info=True)

            # ✅ For non-admins: schedule auto-delete (no extra messages)
            if not is_admin:
                track_user_message(user_id, sent)
                asyncio.create_task(auto_delete_message(sent, 180))

            # IMPORTANT: Return here - no further callback_query.answer calls
            return

        # ---------- LINK ----------
        if qtype == "link":
            display_name = str(show_name).replace('_', ' ') if isinstance(show_name, bytes) else show_name.replace('_', ' ')
            url = qdata.get("content")
            if url and isinstance(url, str):
                await client.send_message(
                    chat_id=callback_query.from_user.id,
                    text=(
                        f"🎬 {display_name} - {season_or_key} Ep{index+1} [{quality}]\n"
                        f"🔗 {url}"
                    ),
                    disable_web_page_preview=False
                )
            else:
                logger.warning(f"Invalid URL for {show_name}: {url}")
                return await callback_query.answer("Invalid URL.", show_alert=True)
            return await callback_query.answer()

        # ---------- SPLIT (show part buttons) ----------
        if qtype == "split":
            parts_list = qdata.get("parts", [])
            buttons = []
            for i, fid in enumerate(parts_list):
                if fid:
                    buttons.append([
                        InlineKeyboardButton(
                            f"▶️ Part {i+1}",
                            callback_data=f"play_quality_part|{category}|{show_name}|{season_or_key}|{index}|{quality}|{i}"
                        )
                    ])
            if not buttons:
                return await callback_query.answer("Split parts not uploaded yet.", show_alert=True)

            display_name = str(show_name).replace('_', ' ') if isinstance(show_name, bytes) else show_name.replace('_', ' ')
            await callback_query.message.edit_text(
                f"🎬 {display_name} - {season_or_key} Ep{index+1} [{quality}] (Split)",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return await callback_query.answer()

        # Unsupported format
        return await callback_query.answer("Unsupported quality format.", show_alert=True)

    except Exception as e:
        logger.exception("play_quality_handler error: %s", e)
        try:
            return await callback_query.answer("Error playing quality.", show_alert=True)
        except Exception:
            logger.debug("Failed to answer callback_query after exception.", exc_info=True)
            return

@app.on_callback_query(filters.regex(r"^play_quality_part\|"))
async def play_quality_part_handler(client, callback_query: CallbackQuery):
    """Send individual split parts with proper error handling."""
    try:
        # Convert to string and parse callback data
        data_str = str(callback_query.data) if isinstance(callback_query.data, bytes) else callback_query.data
        parts = data_str.split("|", 6)
        
        if len(parts) < 7:
            return await callback_query.answer("Invalid data format.", show_alert=True)
        
        category = str(parts[1])
        show_name = str(parts[2])
        season_or_key = str(parts[3])
        
        try:
            index = int(parts[4])
            quality = str(parts[5])
            part_index = int(parts[6])
        except (ValueError, IndexError) as e:
            logger.debug(f"Invalid play_quality_part format: {e}")
            return await callback_query.answer("Invalid episode/part format.", show_alert=True)

        data = load_data_cached()
        if not data or show_name not in data.get(category, {}):
            return await callback_query.answer("Show not found.", show_alert=True)

        if season_or_key not in data[category][show_name]:
            return await callback_query.answer("Season not found.", show_alert=True)

        episode_list = data[category][show_name][season_or_key]
        if index < 0 or index >= len(episode_list):
            return await callback_query.answer("Episode not found.", show_alert=True)

        episode_data = episode_list[index]
        if not (isinstance(episode_data, dict) and "qualities" in episode_data):
            return await callback_query.answer("No quality data.", show_alert=True)

        qdata = episode_data["qualities"].get(quality)
        if not qdata or qdata.get("type") != "split":
            return await callback_query.answer("Not a split quality.", show_alert=True)

        parts_list = qdata.get("parts", [])
        if part_index < 0 or part_index >= len(parts_list) or not parts_list[part_index]:
            return await callback_query.answer("That part is not uploaded yet.", show_alert=True)

        file_id = parts_list[part_index]
        user_id = callback_query.from_user.id
        is_admin = user_id in ADMIN_IDS
        
        # ✅ SINGLE TOP BANNER TOAST
        if not is_admin:
            await callback_query.answer(
                "⚠️ Video will auto-delete in 3 minutes! Forward to Saved Messages to keep it.",
                show_alert=False
            )
        else:
            await callback_query.answer()
        
        # ✅ SEND VIDEO
        display_name = str(show_name).replace('_', ' ') if isinstance(show_name, bytes) else show_name.replace('_', ' ')
        sent = await client.send_video(
            chat_id=user_id,
            video=file_id,
            caption=f"🎬 {display_name} | {season_or_key} Ep{index+1} [{quality}] - Part {part_index+1}"
        )
        
        # Track view
        try:
            asyncio.create_task(increment_show_view(category, show_name))
        except Exception as e:
            logger.debug(f"Failed to track view: {e}")
        
        # ✅ AUTO-DELETE FOR NON-ADMINS
        if not is_admin:
            track_user_message(user_id, sent)
            asyncio.create_task(auto_delete_message(sent, 180))
        
        return

    except Exception as e:
        logger.exception("play_quality_part_handler error: %s", e)
        try:
            return await callback_query.answer("Error sending part.", show_alert=True)
        except Exception:
            logger.debug("Failed to answer callback after exception", exc_info=True)
            return

# ============================
# TRENDING SHOWS
# ============================

@app.on_message(filters.command("fav"))
async def top_favorites_command(client, message: Message):
    """Display top 10 most favorited shows (Global)."""
    try:
        # Track user interaction
        await upsert_user_from_context(message.from_user, message.chat)
        
        # Aggregate top favorited shows
        pipeline = [
            {"$group": {
                "_id": {"show_name": "$show_name", "category": "$category", "show_slug": "$show_slug"},
                "count": {"$sum": 1}
            }},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        
        top_shows = list(favorites_collection.aggregate(pipeline))
        
        if not top_shows:
            await message.reply("📉 No favorites data yet.")
            return

        bot_me = await client.get_me()
        bot_username = bot_me.username or "bot"

        
        # Build message
        msg = "⭐ **Top 10 Most Favorited Shows**\n\n"
        
        buttons = []
        for idx, item in enumerate(top_shows, 1):
            show_info = item["_id"]
            count = item["count"]
            show_name = show_info.get("show_name", "Unknown")
            category = show_info.get("category", "N/A")
            show_slug = show_info.get("show_slug", "")
            
            msg += f"{idx}. 🎬 **{show_name}** – ⭐ {count}\n"
            msg += f"   📂 {category}\n\n"
            
            # Create deep link button
            category_slug = category.lower().replace(" ", "_")
            deep_link = f"https://t.me/{bot_username}?start={category_slug}__{show_slug}"
            
            buttons.append([
                InlineKeyboardButton(
                    f"▶ {show_name}",
                    url=deep_link
                )
            ])

        await message.reply(
            msg,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    except Exception as e:
        logger.exception(f"top_favorites_command ERROR: {e}")
        await message.reply("❌ Failed to load favorites.")


@app.on_message(filters.command(["trending", "treanding", "top10", "popular"]))
async def real_trending_command(client, message: Message):
    """Display top 10 most viewed/active shows."""
    try:
        # Track user interaction
        await upsert_user_from_context(message.from_user, message.chat)
        
        # Fetch top viewed from stats_collection
        # Sort by views desc
        top_shows = list(stats_collection.find().sort("views", -1).limit(10))
        
        if not top_shows:
            await message.reply(
                "📉 **Trending Data Building...**\n\n"
                "We just started tracking views! Check back soon to see what's trending.\n"
                "Try `/fav` to see most favorited shows in the meantime."
            )
            return

        bot_me = await client.get_me()
        bot_username = bot_me.username or "bot"
        
        # Build message
        msg = "📈 **Top 10 Trending Shows (Most Viewed)**\n\n"
        
        buttons = []
        for idx, item in enumerate(top_shows, 1):
            show_name = item.get("show_name", "Unknown")
            category = item.get("category", "N/A")
            views = item.get("views", 0)
            show_slug = item.get("show_slug", "")
            
            msg += f"{idx}. 🎬 **{show_name}** – 👁 {views} views\n"
            msg += f"   📂 {category}\n\n"
            
            # Create deep link button
            category_slug = category.lower().replace(" ", "_")
            if not show_slug and show_name:
                 show_slug = normalize_show_slug(show_name)

            deep_link = f"https://t.me/{bot_username}?start={category_slug}__{show_slug}"
            
            buttons.append([
                InlineKeyboardButton(
                    f"▶ {show_name}",
                    url=deep_link
                )
            ])

        await message.reply(
            msg,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    except Exception as e:
        logger.exception(f"real_trending_command ERROR: {e}")
        await message.reply("❌ Failed to load trending.")
        logger.exception(f"trending_shows_command ERROR: {e}")
        await message.reply("❌ Failed to load trending shows.")

async def safe_autodelete(client, msg, delay=180):
    """
    Deletes a message ONLY for normal users.
    Never deletes admin chats, menus, or reports.
    """
    try:
        user_id = msg.chat.id

        # 1) DO NOT delete anything for admins
        if user_id in ADMIN_IDS:
            return

        # 2) Only act in private chats
        if msg.chat.type not in ("private", "ChatType.PRIVATE"):
            return

        # 3) Messages we should NEVER delete (menus, reports)
        keep_keywords = [
            "Choose a category",
            "📺 Show:",
            "📂 Season",
            "Episode",
            "Report submitted",
            "User Report",
            "⚠️ Report Issue",
        ]

        if msg.text and any(k in msg.text for k in keep_keywords):
            return  # skip deleting important menu messages

        # 4) Wait and delete
        await asyncio.sleep(delay)
        await msg.delete()

    except Exception:
        pass

# ============================
# HELPER FUNCTIONS
# ============================

def sanitize_regex_input(text):
    """Escape regex special characters in user input."""
    import re
    return re.escape(text)

async def on_startup():
    await load_hash_cache_on_startup()
    logger.info("Bot is starting...")
    
    # Try to resolve storage channel with retries
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Resolving Storage Channel ID: {STORAGE_CHANNEL_ID} (Attempt {attempt}/{max_retries})")
            chat = await app.get_chat(STORAGE_CHANNEL_ID)
            logger.info(f"✅ Storage Channel resolved successfully: {chat.title}")
            break
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"⚠️ Attempt {attempt} failed: {e}. Retrying in 2 seconds...")
                await asyncio.sleep(2)
            else:
                logger.warning(f"⚠️ Could not resolve Storage Channel after {max_retries} attempts: {e}")
                logger.info("ℹ️ This is normal on first deployment. The channel will be resolved on first upload/import.")

    # Start background cleanup task
    asyncio.create_task(cleanup_stale_states())
    logger.info("✅ Started cleanup task")

# ============================
# NEW FEATURE: RECENT UPDATES
# ============================

# Global list to store recent updates (max 20)
recent_updates = []

# MongoDB collection for recent updates
recent_updates_collection = db["recent_updates"]


def load_recent_updates():
    """Load recent updates from MongoDB on startup."""
    global recent_updates
    try:
        doc = recent_updates_collection.find_one({"_id": "recent_updates"})
        if doc and "updates" in doc:
            recent_updates = doc["updates"]
            logger.info(f"✅ Loaded {len(recent_updates)} recent updates from MongoDB")
        else:
            recent_updates = []
            logger.info("ℹ️ No recent updates found in MongoDB, starting fresh")
    except Exception as e:
        logger.exception(f"Error loading recent updates: {e}")
        recent_updates = []


def save_recent_updates():
    """Save recent updates to MongoDB."""
    try:
        recent_updates_collection.update_one(
            {"_id": "recent_updates"},
            {"$set": {"updates": recent_updates}},
            upsert=True
        )
        logger.debug(f"💾 Saved {len(recent_updates)} recent updates to MongoDB")
    except Exception as e:
        logger.exception(f"Error saving recent updates: {e}")


def add_recent_update(category, show_name, season, episode_number):
    """
    Add a new update to the recent updates list.
    
    Args:
        category: Category name (e.g., "Hindi Dubbed", "Japanese Drama")
        show_name: Show name (e.g., "good_boy")
        season: Season key (e.g., "1", "S1")
        episode_number: Episode number (1-indexed integer)
    """
    global recent_updates
    
    try:
        import time
        
        # Create new update entry
        new_update = {
            "timestamp": int(time.time()),
            "category": category,
            "show_name": show_name,
            "season": season,
            "episode_number": episode_number
        }
        
        # Add to beginning of list (most recent first)
        recent_updates.insert(0, new_update)
        
        # Trim to max 20 entries
        if len(recent_updates) > 20:
            recent_updates = recent_updates[:20]
        
        # Save to MongoDB
        save_recent_updates()
        
        logger.info(f"📝 Added recent update: {show_name} S{season} E{episode_number} ({category})")
        
    except Exception as e:
        logger.exception(f"Error adding recent update: {e}")


def format_time_ago(timestamp):
    """Format timestamp as 'X min/hours/days ago'."""
    import time
    
    now = int(time.time())
    diff = now - timestamp
    
    if diff < 60:
        return "just now"
    elif diff < 3600:
        mins = diff // 60
        return f"{mins} min ago" if mins == 1 else f"{mins} mins ago"
    elif diff < 86400:
        hours = diff // 3600
        return f"{hours} hour ago" if hours == 1 else f"{hours} hours ago"
    else:
        days = diff // 86400
        return f"{days} day ago" if days == 1 else f"{days} days ago"


@app.on_message(filters.command("recent_updates"))
async def recent_updates_command(client, message: Message):
    """Display recent updates panel."""
    try:
        # Track user interaction
        await upsert_user_from_context(message.from_user, message.chat)
        
        if not recent_updates:
            return await message.reply(
                "📭 **No Recent Updates**\n\n"
                "No episodes have been uploaded yet.\n"
                "Check back later for new content!"
            )
        
        # Build message
        msg = "🔥 **RECENT UPDATES**\n\n"
        
        buttons = []
        
        for idx, update in enumerate(recent_updates[:10], 1):  # Show top 10
            category = update.get("category", "Unknown")
            show_name = update.get("show_name", "Unknown")
            season = update.get("season", "?")
            episode_num = update.get("episode_number", "?")
            timestamp = update.get("timestamp", 0)
            
            # Format show name for display
            display_name = show_name.replace("_", " ").title()
            
            # Build entry
            msg += f"{idx}. 🎬 **{display_name}**\n"
            msg += f"   📺 S{season} • Ep{episode_num}\n"
            msg += f"   📂 {category}\n"
            msg += f"   ⏱ {format_time_ago(timestamp)}\n\n"
            
            # Add button for this update
            buttons.append([
                InlineKeyboardButton(
                    f"▶ Open {display_name}",
                    callback_data=f"open_recent|{category}|{show_name}"
                )
            ])
        
        # Add back button
        buttons.append([
            InlineKeyboardButton("🏠 Back to Categories", callback_data="back_to_categories")
        ])
        
        await message.reply(
            msg,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    except Exception as e:
        logger.exception(f"recent_updates_command error: {e}")
        await message.reply("❌ Error loading recent updates.")


@app.on_callback_query(filters.regex("^open_recent\\|"))
async def handle_open_recent(client, callback_query: CallbackQuery):
    """Handle opening a show from recent updates."""
    try:
        # Parse callback data: open_recent|category|show_name
        parts = callback_query.data.split("|")
        
        if len(parts) < 3:
            return await callback_query.answer("❌ Invalid data", show_alert=True)
        
        category = parts[1]
        show_name = parts[2]
        
        # Load data to verify show exists
        data = load_data_cached()
        
        if category not in data or show_name not in data[category]:
            return await callback_query.answer(
                "❌ Show not found. It may have been deleted.",
                show_alert=True
            )
        
        # Get show data
        show_data = data[category][show_name]
        
        # Build season selection panel (same as existing show_menu logic)
        display_name = show_name.replace("_", " ").title()
        
        # Get available seasons
        seasons = [k for k in show_data.keys() if k not in ["poster", "episodes"]]
        
        if not seasons and "episodes" not in show_data:
            return await callback_query.answer(
                "❌ No episodes available",
                show_alert=True
            )
        
        # Build message
        msg = f"📺 **{display_name}**\n"
        msg += f"📂 Category: {category}\n\n"
        
        if seasons:
            msg += f"🎬 **Select Season:**"
        else:
            msg += f"🎬 **Episodes:**"
        
        # Build buttons
        buttons = []
        
        # Season buttons
        if seasons:
            for season_key in sorted(seasons):
                episode_count = len(show_data[season_key]) if isinstance(show_data[season_key], list) else 0
                buttons.append([
                    InlineKeyboardButton(
                        f"Season {season_key} ({episode_count} episodes)",
                        callback_data=f"season|{category}|{show_name}|{season_key}"
                    )
                ])
        
        # Direct episodes (no seasons)
        if "episodes" in show_data:
            episodes = show_data["episodes"]
            if isinstance(episodes, list) and episodes:
                buttons.append([
                    InlineKeyboardButton(
                        f"Episodes ({len(episodes)})",
                        callback_data=f"season|{category}|{show_name}|episodes"
                    )
                ])
        
        # Back button
        buttons.append([
            InlineKeyboardButton("◀️ Back to Updates", callback_data="show_recent_updates")
        ])
        
        # Edit message
        await callback_query.message.edit_text(
            msg,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
        await callback_query.answer()
        
    except Exception as e:
        logger.exception(f"handle_open_recent error: {e}")
        await callback_query.answer("❌ Error opening show", show_alert=True)


@app.on_callback_query(filters.regex("^show_recent_updates$"))
async def show_recent_updates_callback(client, callback_query: CallbackQuery):
    """Show recent updates panel from callback."""
    try:
        if not recent_updates:
            return await callback_query.message.edit_text(
                "📭 **No Recent Updates**\n\n"
                "No episodes have been uploaded yet.\n"
                "Check back later for new content!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Back to Categories", callback_data="back_to_categories")
                ]])
            )
        
        # Build message
        msg = "🔥 **RECENT UPDATES**\n\n"
        
        buttons = []
        
        for idx, update in enumerate(recent_updates[:10], 1):
            category = update.get("category", "Unknown")
            show_name = update.get("show_name", "Unknown")
            season = update.get("season", "?")
            episode_num = update.get("episode_number", "?")
            timestamp = update.get("timestamp", 0)
            
            display_name = show_name.replace("_", " ").title()
            
            msg += f"{idx}. 🎬 **{display_name}**\n"
            msg += f"   📺 S{season} • Ep{episode_num}\n"
            msg += f"   📂 {category}\n"
            msg += f"   ⏱ {format_time_ago(timestamp)}\n\n"
            
            buttons.append([
                InlineKeyboardButton(
                    f"▶ Open {display_name}",
                    callback_data=f"open_recent|{category}|{show_name}"
                )
            ])
        
        buttons.append([
            InlineKeyboardButton("🏠 Back to Categories", callback_data="back_to_categories")
        ])
        
        await callback_query.message.edit_text(
            msg,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
        await callback_query.answer()
        
    except Exception as e:
        logger.exception(f"show_recent_updates_callback error: {e}")
        await callback_query.answer("❌ Error loading updates", show_alert=True)


# Load recent updates on startup
load_recent_updates()


# ============================
# RENDER KEEP-ALIVE SERVER
# ============================


# ============================================
# USER MAX-PROFILE HANDLERS
# ============================================

@app.on_message(filters.command("search_user") & admin_filter & filters.private)
async def search_user_command(client, message):
    """
    Search for a user by ID, username, or name.
    Shows Marker 1 (Profile Card).
    """
    logger.info(f"DEBUG: search_user_command triggered by {message.from_user.id} with text: {message.text}")
    try:
        if len(message.command) < 2:
            return await message.reply("Usage: /search_user <id|username|name>")
            
        query = message.text.split(" ", 1)[1].strip()
        
        # Strip @ if present for username search
        if query.startswith("@"):
            query = query[1:]
        
        # Try to find user
        user_doc = None
        
        # 1. Try by ID
        if query.isdigit():
            user_doc = userdb_collection.find_one({"user_id": int(query)})
            
        # 2. Try by Username
        if not user_doc:
            user_doc = userdb_collection.find_one({"username": {"$regex": f"^{query}$", "$options": "i"}})
            
        # 3. Try by Name (Fuzzyish)
        if not user_doc:
            user_doc = userdb_collection.find_one({"full_name": {"$regex": query, "$options": "i"}})
            
        if not user_doc:
            return await message.reply("❌ User not found in database.")
            
        user_id = user_doc["user_id"]
        
        # Calculate/Get Profile
        calculate_max_profile(user_id) # Refresh metrics
        
        # Prepare Marker 1 (Main View)
        photo_id = None
        try:
            photos = [p async for p in client.get_chat_photos(user_id, limit=1)]
            if photos:
                photo_id = photos[0].file_id
        except:
            pass
            
        # Caption
        caption = (
            f"👤 **USER PROFILE**\n"
            f"ID: `{user_id}`\n"
            f"Name: {user_doc.get('full_name', 'Unknown')}\n"
            f"Username: @{user_doc.get('username', 'None')}\n"
            f"Language: {user_doc.get('language_code', 'Unknown')}\n"
            f"Last Seen: {user_doc.get('last_interaction', datetime.now()).strftime('%Y-%m-%d %H:%M')}\n\n"
            f"👇 **ACTIONS:**"
        )
        
        buttons = [
            [InlineKeyboardButton("🧠 Deep Analysis (Max-Profile)", callback_data=f"max_profile_{user_id}")],
            [InlineKeyboardButton("🗂 Report History", callback_data=f"user_history_{user_id}")],
            [InlineKeyboardButton("🚫 Ban User (Mock)", callback_data="noop")]
        ]
        
        if photo_id:
            await client.send_photo(
                chat_id=message.chat.id,
                photo=photo_id,
                caption=caption,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            await message.reply(
                caption,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            
    except Exception as e:
        logger.exception(f"search_user error: {e}")
        await message.reply(f"Error searching user: {e}")

@app.on_callback_query(filters.regex(r"^max_profile_(\d+)"))
async def view_max_profile(client, callback_query):
    """
    Marker 2: Show the Deep Analysis text block.
    """
    try:
        user_id = int(callback_query.data.split("_")[2])
        
        # Regenerate profile to get latest stats
        profile = calculate_max_profile(user_id)
        text_report = format_max_profile_text(profile, user_id)
        
        # Add back button
        buttons = [[InlineKeyboardButton("🔙 Back to Profile", callback_data=f"profile_back_{user_id}")]]
        
        # Edit the message (keep photo if present)
        try:
            await callback_query.message.edit_caption(
                caption=text_report,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except:
             await callback_query.message.edit_text(
                text=text_report,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            
    except Exception as e:
        logger.exception(f"view_max_profile error: {e}")
        await callback_query.answer("Error loading profile")

@app.on_callback_query(filters.regex(r"^user_history_(\d+)"))
async def view_user_history(client, callback_query):
    """
    Marker 3: Show report history.
    """
    try:
        user_id = int(callback_query.data.split("_")[2])
        
        cursor = reports_collection.find({"user.user_id": user_id}).sort("created_at", -1).limit(5)
        reports = list(cursor)
        
        if not reports:
            msg = f"📂 **REPORT HISTORY**\nID: `{user_id}`\n\nNo reports found."
        else:
            msg = f"📂 **REPORT HISTORY ({len(reports)} found)**\nID: `{user_id}`\n\n"
            for r in reports:
                status_emoji = {"pending": "🔴", "processing": "🟡", "resolved": "🟢"}.get(r.get('status'), "⚪")
                date_str = r['created_at'].strftime('%Y-%m-%d')
                issue = r['report'].get('issue', 'No issue')[:50]
                msg += f"{status_emoji} `{date_str}`: {issue}...\n"
                
        buttons = [[InlineKeyboardButton("🔙 Back to Profile", callback_data=f"profile_back_{user_id}")]]
        
        try:
            await callback_query.message.edit_caption(
                caption=msg,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except:
             await callback_query.message.edit_text(
                text=msg,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            
    except Exception as e:
        logger.exception(f"view_user_history error: {e}")

@app.on_callback_query(filters.regex(r"^profile_back_(\d+)"))
async def back_to_profile(client, callback_query):
    """Return to Marker 1."""
    try:
        user_id = int(callback_query.data.split("_")[2])
        user_doc = userdb_collection.find_one({"user_id": user_id})
        
        # Fallback if user not found (rare)
        name = user_doc.get('full_name', 'Unknown') if user_doc else "Unknown"
        username = user_doc.get('username', 'None') if user_doc else "None"
        
        caption = (
            f"👤 **USER PROFILE**\n"
            f"ID: `{user_id}`\n"
            f"Name: {name}\n"
            f"Username: @{username}\n"
            f"👇 **ACTIONS:**"
        )
        
        buttons = [
            [InlineKeyboardButton("🧠 Deep Analysis (Max-Profile)", callback_data=f"max_profile_{user_id}")],
            [InlineKeyboardButton("🗂 Report History", callback_data=f"user_history_{user_id}")],
            [InlineKeyboardButton("🚫 Ban User (Mock)", callback_data="noop")]
        ]
        
        try:
            await callback_query.message.edit_caption(
                caption=caption,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except:
             await callback_query.message.edit_text(
                text=caption,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            
    except Exception as e:
        logger.exception(f"back_to_profile error: {e}")

@app.on_message(filters.command("cleanup") & filters.private)
async def cleanup_session(client, message: Message):
    """Clean up user's session messages."""
    user_id = message.from_user.id
    
    if user_id in user_session_messages:
        message_ids = user_session_messages[user_id]
        if message_ids:
            try:
                await client.delete_messages(chat_id=user_id, message_ids=message_ids)
                await message.reply(f"✅ Cleaned up {len(message_ids)} messages.")
            except Exception as e:
                await message.reply(f"❌ Cleanup failed: {e}")
            user_session_messages[user_id] = []
    else:
        await message.reply("No messages to clean up.")

def run_flask_server():
    """Run Flask server in background thread for Render keep-alive."""
    import os
    from flask import Flask
    
    # embed simple flask app
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return "Bot is running!"
    
    port = int(os.environ.get("PORT", 5000))
    
    logger.info(f"🌐 Starting Flask server on port {port}...")
    
    # Run Flask server (this blocks in this thread)
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False  # Important: disable reloader in production
    )

# ============================
# GRACEFUL SHUTDOWN
# ============================

def graceful_shutdown(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info("🛑 Received shutdown signal, cleaning up...")
    
    try:
        # Cancel active notification tasks
        if active_notification_tasks:
            logger.info(f"Cancelling {len(active_notification_tasks)} notification tasks...")
            for task in active_notification_tasks:
                if not task.done():
                    task.cancel()
        
        # Save recent updates
        save_recent_updates()
        logger.info("✅ Saved recent updates")
        
        # Close MongoDB connection
        client.close()
        logger.info("✅ Closed MongoDB connection")
        
        # Stop Pyrogram client (non-blocking)
        logger.info("✅ Stopping Pyrogram client")
        
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
    
    logger.info("👋 Shutdown complete")
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)

# ============================================================
# DIAGNOSTIC VERSION - Use this to see what's happening
# ============================================================
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🚀 K-DRAMA BOT STARTING")
    logger.info("=" * 50)
    
    # Start Flask server in background thread
    from threading import Thread
    import time
    
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask server thread started")
    
    # Periodic reconnection using Pyrogram's run() with timeout
    CONNECTION_LIFETIME = 1500  # 25 minutes
    
    def restart_loop():
        """Restart the bot periodically to maintain fresh connections"""
        restart_count = 0
        
        while True:
            try:
                restart_count += 1
                logger.info(f"🔌 Starting bot (restart #{restart_count})...")
                
                # Use Pyrogram's run() which handles everything internally
                # We'll create a separate thread to stop it after timeout
                stop_event = threading.Event()
                
                def auto_stop():
                    """Stop the bot after CONNECTION_LIFETIME seconds"""
                    time.sleep(CONNECTION_LIFETIME)
                    logger.info("⏰ Connection lifetime exceeded, stopping bot...")
                    stop_event.set()
                    try:
                        app.stop()
                    except:
                        pass
                
                # Start auto-stop timer in background
                stop_thread = Thread(target=auto_stop, daemon=True)
                stop_thread.start()
                
                # Run the bot - this blocks until stopped
                app.run()
                
                # If we get here, bot was stopped (either by timer or error)
                logger.info("✅ Bot stopped, waiting 5 seconds before reconnect...")
                time.sleep(5)
                
            except KeyboardInterrupt:
                logger.info("⚠️ Bot interrupted by user")
                break
            except Exception as e:
                logger.error(f"❌ Bot error: {e}")
                logger.exception(e)
                logger.info("⏳ Retrying in 30 seconds...")
                time.sleep(30)
        
        logger.info("👋 Bot shutdown complete")
    
    # Run with periodic restarts
    try:
        restart_loop()
    except KeyboardInterrupt:
        logger.info("⚠️ Interrupted by user")
    except Exception as e:
        logger.exception(f"💥 Fatal error: {e}")
    finally:
        logger.info("=" * 50)
        logger.info("🏁 BOT TERMINATED")
        logger.info("=" * 50)