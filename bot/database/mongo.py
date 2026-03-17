import logging
from motor.motor_asyncio import AsyncIOMotorClient
from bot.config import MONGO_URI

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.client = AsyncIOMotorClient(
            MONGO_URI,
            maxPoolSize=50,
            minPoolSize=10,
            maxIdleTimeMS=45000,
            serverSelectionTimeoutMS=30000,
            connectTimeoutMS=10000,
            socketTimeoutMS=45000,
            retryWrites=True,
            compressors='zlib'
        )
        self.db = self.client['kdrama']
        
        # Collections
        self.shows = self.db['shows']
        self.user_verification = self.db['user_verification']
        self.reports = self.db['reports']
        self.users = self.db['users']
        self.userdb = self.db['users_canonical']
        self.users_max = self.db['users_max_profile']
        self.config = self.db['config']
        self.favorites = self.db['favorites']
        self.hash_lookup = self.db['hash_lookup']
        self.recent_updates = self.db['recent_updates']
        self.stats = self.db['show_stats']

    async def create_indexes(self):
        """Create necessary indexes for performance."""
        try:
            await self.shows.create_index([("category", 1), ("show_name", 1)], unique=True)
            await self.user_verification.create_index("user_id", unique=True)
            await self.reports.create_index("user.user_id")
            await self.reports.create_index("status")
            await self.reports.create_index([("created_at", -1)])
            await self.userdb.create_index("user_id", unique=True)
            await self.userdb.create_index("chats.chat_id")
            await self.userdb.create_index("allow_global_notifications")
            await self.favorites.create_index([("user_id", 1), ("show_slug", 1)], unique=True)
            await self.favorites.create_index("user_id")
            await self.favorites.create_index("show_slug")
            await self.hash_lookup.create_index("last_accessed", expireAfterSeconds=604800)
            await self.stats.create_index("show_slug", unique=True)
            await self.stats.create_index([("views", -1)])
            await self.favorites.create_index([("created_at", -1)])
            logger.info("✅ Database indexes verified/created.")
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

# Singleton instance
db = Database()
