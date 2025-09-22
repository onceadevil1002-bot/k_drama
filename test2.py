from pymongo import MongoClient
import logging
import json

# --- Logging setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- Your Mongo URI (same as in bot.py) ---
MONGO_URI = "mongodb://kdrama_bot:show@ac-nuextg1-shard-00-00.7i4xnv0.mongodb.net:27017/admin?ssl=true"

# --- Mongo connection (optimized timeouts) ---
client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=5000,   # 5s
    connectTimeoutMS=5000,
    socketTimeoutMS=5000,
    tls=True
)

db = client["kdrama"]
collection = db["shows"]

# --- Test the connection ---
try:
    client.admin.command("ping")
    logger.info("✅ MongoDB connection successful!")
except Exception as e:
    logger.error("❌ MongoDB connection failed: %s", e)
    exit(1)

# --- Print orientation of shows collection ---
logger.info("Fetching orientation of MongoDB 'shows' collection...")

cursor = collection.find({}, {"_id": 0}).limit(25)  # fetch 5 documents for sample
for doc in cursor:
    print(json.dumps(doc, indent=2))
