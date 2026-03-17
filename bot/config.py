import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# App Configuration
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Admin Configuration
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
SECONDARY_ADMIN_ID = int(os.environ.get("SECONDARY_ADMIN_ID", 0))
ADMIN_IDS = [ADMIN_ID, 6661974604, 6244759828]
if SECONDARY_ADMIN_ID:
    ADMIN_IDS.append(SECONDARY_ADMIN_ID)

# Channel Configuration
STORAGE_CHANNEL_ID = int(os.environ.get("STORAGE_CHANNEL_ID", 0))
MAIN_CHANNEL_LINK = os.environ.get("MAIN_CHANNEL_LINK", "")
SECOND_CHANNEL_LINK = os.environ.get("SECOND_CHANNEL_LINK", "")
REQUIRED_CHANNELS_RAW = os.environ.get("REQUIRED_CHANNELS", "@Seoul_Entertainment_DKD")
REQUIRED_CHANNELS = [c.strip() for c in REQUIRED_CHANNELS_RAW.split(",") if c.strip()]

# Database Configuration
# Priority: MONGO_URI, then mongo_url
MONGO_URI = os.environ.get("MONGO_URI") or os.environ.get("mongo_url")
if not MONGO_URI:
    raise ValueError("MONGO_URI or mongo_url not set in environment variables.")

# Optional: Redis Configuration
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Category Emojis
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
