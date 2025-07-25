from pymongo import MongoClient
from dotenv import load_dotenv
import os

# Load .env file (contains MONGO_URI)
load_dotenv()

# Connect to MongoDB
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client["kdrama"]
collection = db["shows"]

# Track updated count
updated_count = 0

# Loop through each show document
for doc in collection.find():
    if "poster" not in doc:
        collection.update_one(
            {"_id": doc["_id"]},
            {"$set": {"poster": []}}
        )
        print(f"✅ Added 'poster': [] to show → {doc.get('show_name', 'Unknown')}")
        updated_count += 1

print(f"\n🎉 Poster field added to {updated_count} document(s).")
