from pymongo import MongoClient
from dotenv import load_dotenv
import os
import json

# Load environment variables from .env
load_dotenv()

# Connect to MongoDB
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)

# Access collection
db = client["kdrama"]
collection = db["shows"]

# Fetch and print all documents with formatting
print("📂 All Documents in MongoDB:\n")
for doc in collection.find():
    print(json.dumps(doc, indent=2, default=str))
    print("=" * 50)
