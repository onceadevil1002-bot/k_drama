from pymongo import MongoClient

MONGO_URI = "mongodb://kdrama_bot:show@ac-nuextg1-shard-00-00.7i4xnv0.mongodb.net:27017/admin?ssl=true"

client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=20000,  # 20s
    connectTimeoutMS=20000,
    socketTimeoutMS=20000,
    tls=True
)

db = client["kdrama"]
collection = db["shows"]

# Test the connection
try:
    client.admin.command("ping")
    print("✅ MongoDB connection successful!")
except Exception as e:
    print("❌ MongoDB connection failed:", e)