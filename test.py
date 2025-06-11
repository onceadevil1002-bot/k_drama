from pymongo import MongoClient

print("🔍 Trying MongoDB connection...")

try:
    client = MongoClient("mongodb://kdrama_bot:shows@ac-nuextg1-shard-00-00.7i4xnv0.mongodb.net:27017/admin?ssl=true", serverSelectionTimeoutMS=10000)
    print("✅ Connected to:", client.server_info()["version"])
except Exception as e:
    print("❌ Connection failed:", e)