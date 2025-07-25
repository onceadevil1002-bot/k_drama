from pymongo import MongoClient
import json

# === MongoDB Connection ===
MONGO_URI = "mongodb://kdrama_bot:show@ac-nuextg1-shard-00-00.7i4xnv0.mongodb.net:27017/admin?ssl=true"
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = client["kdrama"]
collection = db["shows"]

# === Load all documents ===
docs = list(collection.find())

if not docs:
    print("❌ No documents found in MongoDB.")
else:
    print(f"✅ Found {len(docs)} document(s) in MongoDB:\n")
    for idx, doc in enumerate(docs, start=1):
        print(f"📄 Document #{idx}:")
        print(json.dumps(doc, indent=2, default=str))
        print("=" * 60)